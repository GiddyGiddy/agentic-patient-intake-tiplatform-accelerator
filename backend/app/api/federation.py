"""Entity statement for the isolated gematik reference-server experiment only."""

import json
import logging
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from fastapi import APIRouter, HTTPException, Response
from jwt.algorithms import ECAlgorithm

from config import settings

router = APIRouter()
log = logging.getLogger(__name__)

RP_ENTITY_ID = "http://127.0.0.1:8084"
FEDMASTER_ENTITY_ID = "http://127.0.0.1:8083"
REDIRECT_URI = f"{FEDMASTER_ENTITY_ID}/auth"
SIGNING_KID = "puk_fd_sig"
ENCRYPTION_KID = "test-enc"


def _read_key(path: str) -> ec.EllipticCurvePrivateKey:
    if not path:
        raise ValueError("key file not configured")
    key = load_pem_private_key(Path(path).read_bytes(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("reference RP requires a P-256 private key")
    return key


def _public_jwk(key: ec.EllipticCurvePrivateKey, kid: str, use: str) -> dict:
    return {**json.loads(ECAlgorithm.to_jwk(key.public_key())), "kid": kid, "use": use}


@router.get("/.well-known/openid-federation")
def reference_rp_entity_statement() -> Response:
    if not settings.reference_rp_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        signing_key = _read_key(settings.reference_rp_signing_key_file)
        encryption_key = _read_key(settings.reference_rp_encryption_key_file)
        if signing_key.public_key().public_numbers() == encryption_key.public_key().public_numbers():
            raise ValueError("signing and encryption keys must be distinct")
    except (OSError, ValueError, TypeError) as exc:
        log.error("Reference RP key configuration is invalid: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Reference RP key configuration invalid") from exc

    now = int(time.time())
    signing_jwk = _public_jwk(signing_key, SIGNING_KID, "sig")
    encryption_jwk = _public_jwk(encryption_key, ENCRYPTION_KID, "enc")
    claims = {
        "iss": RP_ENTITY_ID,
        "sub": RP_ENTITY_ID,
        "iat": now,
        "exp": now + 600,
        "jwks": {"keys": [signing_jwk]},
        "authority_hints": [FEDMASTER_ENTITY_ID],
        "metadata": {
            "openid_relying_party": {
                "redirect_uris": [REDIRECT_URI],
                "scope": "urn:telematik:versicherter openid",
                "jwks": {"keys": [encryption_jwk]},
                "ti_features_supported": {"id_token_version_supported": ["1.0.0"]},
            }
        },
    }
    token = jwt.encode(
        claims, signing_key, algorithm="ES256",
        headers={"typ": "entity-statement+jwt", "kid": SIGNING_KID},
    )
    return Response(
        content=token,
        media_type="application/entity-statement+jwt",
        headers={"Cache-Control": "no-store"},
    )
