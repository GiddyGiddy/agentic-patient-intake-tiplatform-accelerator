import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient

from app.api.federation import FEDMASTER_ENTITY_ID, REDIRECT_URI, RP_ENTITY_ID
from config import settings
from main import app


def _write_key(path, key):
    path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))


@pytest.fixture
def reference_keys(tmp_path, monkeypatch):
    signing = ec.generate_private_key(ec.SECP256R1())
    encryption = ec.generate_private_key(ec.SECP256R1())
    signing_path = tmp_path / "signing.pem"
    encryption_path = tmp_path / "encryption.pem"
    _write_key(signing_path, signing)
    _write_key(encryption_path, encryption)
    monkeypatch.setattr(settings, "reference_rp_enabled", True)
    monkeypatch.setattr(settings, "reference_rp_signing_key_file", str(signing_path))
    monkeypatch.setattr(settings, "reference_rp_encryption_key_file", str(encryption_path))
    return signing, encryption, signing_path, encryption_path


def test_reference_endpoint_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "reference_rp_enabled", False)
    assert TestClient(app).get("/.well-known/openid-federation").status_code == 404


def test_reference_statement_is_signed_and_contains_only_public_keys(reference_keys):
    signing, encryption, _, _ = reference_keys
    response = TestClient(app).get("/.well-known/openid-federation")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/entity-statement+jwt"
    assert response.headers["cache-control"] == "no-store"
    assert jwt.get_unverified_header(response.text) == {
        "alg": "ES256", "kid": "puk_fd_sig", "typ": "entity-statement+jwt",
    }
    claims = jwt.decode(
        response.text, signing.public_key(), algorithms=["ES256"], issuer=RP_ENTITY_ID,
        options={"require": ["iss", "sub", "iat", "exp"]},
    )
    assert claims["sub"] == RP_ENTITY_ID
    assert claims["authority_hints"] == [FEDMASTER_ENTITY_ID]
    assert claims["exp"] - claims["iat"] == 600
    signing_jwk = claims["jwks"]["keys"][0]
    rp = claims["metadata"]["openid_relying_party"]
    encryption_jwk = rp["jwks"]["keys"][0]
    assert rp["redirect_uris"] == [REDIRECT_URI]
    assert jwt.PyJWK.from_dict(signing_jwk).key.public_numbers() == signing.public_key().public_numbers()
    assert jwt.PyJWK.from_dict(encryption_jwk).key.public_numbers() == encryption.public_key().public_numbers()
    assert "d" not in signing_jwk and "d" not in encryption_jwk


def test_reference_endpoint_rejects_missing_or_reused_key(reference_keys, monkeypatch):
    _, _, signing_path, encryption_path = reference_keys
    client = TestClient(app)
    monkeypatch.setattr(settings, "reference_rp_encryption_key_file", str(signing_path))
    assert client.get("/.well-known/openid-federation").status_code == 503
    encryption_path.unlink()
    monkeypatch.setattr(settings, "reference_rp_encryption_key_file", str(encryption_path))
    assert client.get("/.well-known/openid-federation").status_code == 503


@pytest.mark.parametrize("key", [
    lambda: ec.generate_private_key(ec.SECP384R1()),
    lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048),
])
def test_reference_endpoint_rejects_unsupported_keys(reference_keys, key):
    _, _, signing_path, _ = reference_keys
    _write_key(signing_path, key())
    assert TestClient(app).get("/.well-known/openid-federation").status_code == 503
