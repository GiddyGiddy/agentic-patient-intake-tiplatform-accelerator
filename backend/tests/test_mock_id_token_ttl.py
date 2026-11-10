import hashlib
import importlib.util
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from fastapi.testclient import TestClient


def load_idp():
    path = Path(__file__).resolve().parents[2] / "mock-ti-services" / "mock-id-dienst" / "main.py"
    spec = importlib.util.spec_from_file_location("mock_id_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("configured", "expected"), [(None, 3600), ("900", 900)])
def test_mock_access_token_lifetime(monkeypatch, configured, expected):
    if configured is None:
        monkeypatch.delenv("MOCK_ACCESS_TOKEN_TTL_S", raising=False)
    else:
        monkeypatch.setenv("MOCK_ACCESS_TOKEN_TTL_S", configured)
    idp = load_idp()
    client = TestClient(idp.app)
    verifier = "test-verifier"
    redirect_uri = "http://localhost:3000/"
    response = client.get("/authorize/confirm", params={
        "client_id": idp.CLIENT_ID, "redirect_uri": redirect_uri,
        "code_challenge": idp._b64url(hashlib.sha256(verifier.encode()).digest()),
        "state": "test", "kvnr": "X110000001",
    }, follow_redirects=False)
    assert response.status_code == 302
    code = parse_qs(urlsplit(response.headers["location"]).query)["code"][0]
    response = client.post("/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": idp.CLIENT_ID,
        "redirect_uri": redirect_uri, "code_verifier": verifier,
    })
    assert response.status_code == 200
    token = response.json()
    claims = jwt.decode(token["access_token"], idp._private.public_key(), algorithms=["RS256"],
                        audience=idp.AUDIENCE, issuer=idp.ISSUER)
    assert token["expires_in"] == expected
    assert claims["exp"] - claims["iat"] == expected


@pytest.mark.parametrize("invalid", ["0", "-1", "invalid"])
def test_mock_access_token_lifetime_rejects_invalid_values(monkeypatch, invalid):
    monkeypatch.setenv("MOCK_ACCESS_TOKEN_TTL_S", invalid)
    with pytest.raises(ValueError):
        load_idp()
