import base64
import hashlib
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from jwcrypto import jwe, jwk

from app.api import reference_auth
from app.core.reference_sessions import reference_sessions
from config import settings
from main import app


@pytest.fixture
def bff(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "auth_disabled", False)
    monkeypatch.setattr(settings, "reference_bff_enabled", True)
    monkeypatch.setattr(settings, "reference_rp_enabled", True)
    encryption = ec.generate_private_key(ec.SECP256R1())
    signing = ec.generate_private_key(ec.SECP256R1())
    path = tmp_path / "enc.pem"
    path.write_bytes(encryption.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    monkeypatch.setattr(settings, "reference_rp_encryption_key_file", str(path))
    client = TestClient(app, base_url="http://127.0.0.1:8083")
    provider = SimpleNamespace(
        par_endpoint="http://127.0.0.1:8085/PAR_Auth",
        token_endpoint="http://127.0.0.1:8085/token",
        authorization_endpoint="http://127.0.0.1:8085/auth",
        token_signing_key=signing.public_key(),
    )
    info = {"par": None, "changes": {}}

    class Idp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, data, **kwargs):
            if url == provider.par_endpoint:
                info["par"] = data
                return httpx.Response(201, json={"request_uri": "urn:request:test"}, request=httpx.Request("POST", url))
            assert url == provider.token_endpoint
            assert data["code_verifier"]
            challenge = base64.urlsafe_b64encode(
                hashlib.sha256(data["code_verifier"].encode()).digest()
            ).rstrip(b"=").decode()
            assert challenge == info["par"]["code_challenge"]
            now = int(time.time())
            claims = {
                "iss": reference_auth.ISSUER, "aud": reference_auth.RP_ENTITY_ID,
                "sub": "reference-sub", "iat": now, "exp": now + 300,
                "nonce": info["par"]["nonce"],
                "urn:telematik:claims:id": "X110000001",
                "urn:telematik:claims:profession": "1.2.276.0.76.4.49",
                **info["changes"],
            }
            token = jwt.encode(claims, signing, algorithm="ES256")
            encrypted = jwe.JWE(token.encode(), protected={"alg": "ECDH-ES", "enc": "A256GCM"})
            encrypted.add_recipient(jwk.JWK.from_pyca(encryption.public_key()))
            return httpx.Response(200, json={
                "id_token": encrypted.serialize(compact=True), "access_token": "TODO ACCESS_TOKEN",
            }, request=httpx.Request("POST", url))

    monkeypatch.setattr(reference_auth, "resolve_reference_provider", lambda _: provider)
    monkeypatch.setattr(reference_auth.httpx, "Client", lambda **kwargs: Idp())
    return client, info


def _start(client, info):
    response = client.get("/auth/reference/start", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("http://127.0.0.1:8085/auth?")
    assert "httponly" in response.headers["set-cookie"].lower()
    assert info["par"]["code_challenge_method"] == "S256"
    assert parse_qs(urlsplit(response.headers["location"]).query)["request_uri"] == ["urn:request:test"]
    return info["par"]["state"]


def test_reference_login_session_csrf_and_logout(bff):
    client, info = bff
    state = _start(client, info)
    callback = client.get("/auth", params={"state": state, "code": "test-code"}, follow_redirects=False)
    assert callback.status_code == 303
    assert "httponly" in callback.headers["set-cookie"].lower()
    session = client.get("/auth/reference/session")
    assert session.status_code == 200
    assert set(session.json()) == {"sub", "expiresAt", "mockEpaAvailable"}
    assert session.json()["mockEpaAvailable"] is False
    assert client.post("/api/chat", json={"message": "husten"}).status_code == 403
    headers = {"origin": settings.reference_frontend_origin}
    assert client.post("/api/consent", json={"granted": True}, headers=headers).status_code == 503
    assert client.get("/auth", params={"state": state, "code": "test-code"}).status_code == 400
    assert client.post("/auth/reference/logout", headers=headers).status_code == 204
    assert client.get("/auth/reference/session").status_code == 401


def test_reference_session_defaults_to_token_expiry(bff):
    client, info = bff
    before = time.time()
    state = _start(client, info)
    assert client.get("/auth", params={"state": state, "code": "test"}, follow_redirects=False).status_code == 303
    expiry = client.get("/auth/reference/session").json()["expiresAt"] / 1000
    assert before + 295 <= expiry <= time.time() + 300


def test_local_mock_session_lasts_15_minutes_but_still_expires(bff, monkeypatch):
    client, info = bff
    monkeypatch.setattr(settings, "reference_mock_epa_enabled", True)
    monkeypatch.setattr(settings, "reference_demo_session_ttl_s", 900)
    monkeypatch.setattr(settings, "epa_base_url", "http://mock-ti-gateway:8082/fhir")
    monkeypatch.setattr(settings, "fhir_base_url", "http://mock-ti-gateway:8082/fhir")
    monkeypatch.setattr(settings, "kim_smtp_host", "mock-kim-cm")
    monkeypatch.setattr(settings, "kim_smtp_port", 2525)
    state = _start(client, info)
    before = time.time()
    callback = client.get("/auth", params={"state": state, "code": "test"}, follow_redirects=False)
    assert callback.status_code == 303
    expiry = client.get("/auth/reference/session").json()["expiresAt"] / 1000
    assert before + 899 <= expiry <= time.time() + 900
    assert "Max-Age=899" in callback.headers["set-cookie"] or "Max-Age=900" in callback.headers["set-cookie"]
    monkeypatch.setattr(time, "time", lambda: expiry - 3)
    assert client.get("/auth/reference/session").status_code == 200
    monkeypatch.setattr(time, "time", lambda: expiry + 1)
    assert client.get("/auth/reference/session").status_code == 401


def test_extended_reference_session_requires_local_mock_profile(bff, monkeypatch):
    client, info = bff
    monkeypatch.setattr(settings, "reference_demo_session_ttl_s", 900)
    monkeypatch.setattr(settings, "reference_mock_epa_enabled", False)
    state = _start(client, info)
    assert client.get("/auth", params={"state": state, "code": "test"}, follow_redirects=False).status_code == 503
    assert client.get("/auth/reference/session").status_code == 401


@pytest.mark.parametrize("changes", [
    {"nonce": "wrong"},
    {"iss": "http://wrong"},
    {"aud": "http://wrong"},
    {"exp": 1},
    {"urn:telematik:claims:id": "invalid"},
    {"urn:telematik:claims:profession": "wrong"},
])
def test_invalid_id_token_does_not_create_session(bff, changes):
    client, info = bff
    state = _start(client, info)
    info["changes"] = changes
    assert client.get("/auth", params={"state": state, "code": "test"}, follow_redirects=False).status_code == 401
    assert client.get("/auth/reference/session").status_code == 401


def test_callback_requires_browser_bound_state(bff):
    client, info = bff
    state = _start(client, info)
    assert client.get("/auth", params={"state": "wrong", "code": "test"}).status_code == 400
    assert client.get("/auth", params={"state": state, "code": "test"}).status_code == 400


def test_reference_mode_does_not_fall_back_to_disabled_auth(bff, monkeypatch):
    client, _ = bff
    monkeypatch.setattr(settings, "auth_disabled", True)
    assert client.post(
        "/api/chat", json={"message": "husten"},
        headers={"origin": settings.reference_frontend_origin},
    ).status_code == 401


def test_expired_session_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "reference_bff_enabled", True)
    monkeypatch.setattr(settings, "reference_rp_enabled", True)
    token = reference_sessions.create_session("sub", "X110000001", time.time() - 1)
    client = TestClient(app)
    client.cookies.set("reference_session", token)
    assert client.get("/auth/reference/session").status_code == 401


@pytest.mark.parametrize("setting,value", [
    ("reference_mock_epa_enabled", False),
    ("epa_base_url", "https://real-epa.example/fhir"),
    ("fhir_base_url", "https://real-kis.example/fhir"),
    ("kim_smtp_host", "real-kim.example"),
    ("kim_smtp_port", 25),
    ("reference_frontend_origin", "https://example.test"),
])
def test_mock_consent_fails_closed_for_disabled_or_nonlocal_configuration(bff, monkeypatch, setting, value):
    client, info = bff
    monkeypatch.setattr(settings, "reference_mock_epa_enabled", True)
    monkeypatch.setattr(settings, "epa_base_url", "http://mock-ti-gateway:8082/fhir")
    monkeypatch.setattr(settings, "fhir_base_url", "http://mock-ti-gateway:8082/fhir")
    monkeypatch.setattr(settings, "kim_smtp_host", "mock-kim-cm")
    monkeypatch.setattr(settings, "kim_smtp_port", 2525)
    state = _start(client, info)
    assert client.get("/auth", params={"state": state, "code": "test"}, follow_redirects=False).status_code == 303
    monkeypatch.setattr(settings, setting, value)
    assert client.get("/auth/reference/session").json()["mockEpaAvailable"] is False
    response = client.post(
        "/api/consent", json={"granted": True},
        headers={"origin": settings.reference_frontend_origin},
    )
    assert response.status_code == 503


def test_reference_mock_consent_runs_graph_with_verified_kvnr_and_no_bearer(bff, monkeypatch):
    import app.agents.graph as graph_mod
    from app.agents import consent_agent
    from app.core.epa_client import EPAReadResult

    client, info = bff
    monkeypatch.setattr(settings, "reference_mock_epa_enabled", True)
    monkeypatch.setattr(settings, "epa_base_url", "http://mock-ti-gateway:8082/fhir")
    monkeypatch.setattr(settings, "fhir_base_url", "http://mock-ti-gateway:8082/fhir")
    monkeypatch.setattr(settings, "kim_smtp_host", "mock-kim-cm")
    monkeypatch.setattr(settings, "kim_smtp_port", 2525)
    calls = []
    monkeypatch.setattr(consent_agent, "fetch_patient_summary", lambda kvnr, token:
        calls.append(("epa", kvnr, token)) or EPAReadResult(medication_plan=["demo"]))
    monkeypatch.setattr(graph_mod, "scheduling_node", lambda state:
        calls.append(("scheduling", state["kvnr"])) or {"appointment_id": "test-appointment", "next_agent": "KIMAgent"})
    monkeypatch.setattr(graph_mod, "kim_node", lambda state:
        calls.append(("kim",)) or {"kim_sent": True, "next_agent": "END"})
    monkeypatch.setattr("app.api.endpoints.intake_graph", graph_mod.build_graph())

    state = _start(client, info)
    assert client.get("/auth", params={"state": state, "code": "test"}, follow_redirects=False).status_code == 303
    assert client.get("/auth/reference/session").json()["mockEpaAvailable"] is True
    headers = {"origin": settings.reference_frontend_origin, "authorization": "Bearer untrusted-client-token"}
    pending = client.post("/api/chat", json={"message": "Brustschmerzen"}, headers=headers)
    assert pending.status_code == 200 and pending.json()["iam_required"] is True
    assert calls == []
    assert client.post("/api/consent", json={"granted": True}).status_code == 403
    granted = client.post("/api/consent", json={"granted": True, "kvnr": "X999999999"}, headers=headers)
    assert granted.status_code == 200
    assert granted.json()["consent_granted"] is True
    assert granted.json()["appointment_id"] == "test-appointment"
    assert calls == [("epa", "X110000001", ""), ("scheduling", "X110000001"), ("kim",)]
    assert client.post("/api/consent", json={"granted": True}, headers=headers).status_code == 200
    assert len(calls) == 3
    assert client.post("/api/consent", json={"granted": False}, headers=headers).json()["consent_granted"] is False


def test_existing_reference_consent_cannot_resume_after_mock_mode_is_disabled(monkeypatch):
    from fastapi import HTTPException
    from app.api.endpoints import _run
    from app.core.session import IntakeSession

    monkeypatch.setattr(settings, "reference_mock_epa_enabled", False)
    session = IntakeSession(sub="reference-test", consent_until=time.time() + 60)
    with pytest.raises(HTTPException) as exc:
        _run("reference-test", "", session, "X110000001", reference=True)
    assert exc.value.status_code == 503
