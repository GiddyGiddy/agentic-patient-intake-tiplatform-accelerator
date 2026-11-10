"""Browser login for the pinned, isolated reference server; never production."""

import base64
import hashlib
import logging
import re
import secrets
import time
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from jwcrypto import jwe, jwk
from jwcrypto.common import JWException

from app.api.federation import REDIRECT_URI, RP_ENTITY_ID, _read_key
from app.core.reference_mock import reference_mock_epa_available
from app.core.reference_sessions import reference_sessions
from app.core.reference_trust import ISSUER, ReferenceTrustError, resolve_reference_provider
from config import settings

router = APIRouter()
log = logging.getLogger(__name__)
PENDING_COOKIE = "reference_login"
SESSION_COOKIE = "reference_session"


def _enabled():
    if not settings.reference_bff_enabled or not settings.reference_rp_enabled:
        raise HTTPException(404, "Not found")


def _cookie(response: Response, name: str, value: str, age: int):
    response.set_cookie(name, value, max_age=age, httponly=True, samesite="lax", path="/")


@router.get("/auth/reference/start")
def start_reference_login():
    _enabled()
    verifier, nonce, state = (secrets.token_urlsafe(32) for _ in range(3))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    try:
        with httpx.Client(trust_env=False) as client:
            provider = resolve_reference_provider(client)
            response = client.post(provider.par_endpoint, data={
                "client_id": RP_ENTITY_ID, "redirect_uri": REDIRECT_URI,
                "response_type": "code", "scope": "urn:telematik:versicherter openid",
                "state": state, "nonce": nonce, "code_challenge": challenge,
                "code_challenge_method": "S256", "acr_values": "gematik-ehealth-loa-high",
            }, timeout=30.0)
            response.raise_for_status()
            uri = response.json()["request_uri"]
            if not isinstance(uri, str) or not uri:
                raise ValueError("Invalid request URI")
        pending = reference_sessions.create_pending(state, nonce, verifier)
    except (httpx.HTTPError, ReferenceTrustError, KeyError, ValueError, RuntimeError) as exc:
        log.warning("Reference login start failed: %s", type(exc).__name__)
        raise HTTPException(502, "Reference login unavailable") from exc
    redirect = RedirectResponse(
        f"{provider.authorization_endpoint}?{urlencode({'client_id': RP_ENTITY_ID, 'request_uri': uri})}",
        status_code=302, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
    _cookie(redirect, PENDING_COOKIE, pending, 300)
    return redirect


@router.get("/auth")
def reference_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    _enabled()
    pending = reference_sessions.consume_pending(request.cookies.get(PENDING_COOKIE, ""))
    if not pending or not state or not state.isascii() or not secrets.compare_digest(pending.state, state):
        raise HTTPException(400, "Invalid or expired login state")
    if error or not code:
        raise HTTPException(400, "Reference authorization failed")
    try:
        with httpx.Client(trust_env=False) as client:
            provider = resolve_reference_provider(client)
            response = client.post(provider.token_endpoint, data={
                "grant_type": "authorization_code", "code": code, "client_id": RP_ENTITY_ID,
                "redirect_uri": REDIRECT_URI, "code_verifier": pending.verifier,
            }, timeout=5.0)
            response.raise_for_status()
            encrypted = jwe.JWE()
            encrypted.allowed_algs = ["ECDH-ES", "A256GCM"]
            private = _read_key(settings.reference_rp_encryption_key_file)
            encrypted.deserialize(response.json()["id_token"], key=jwk.JWK.from_pyca(private))
            if encrypted.jose_header.get("alg") != "ECDH-ES" or encrypted.jose_header.get("enc") != "A256GCM":
                raise ValueError("Invalid ID token encryption")
            claims = jwt.decode(
                encrypted.payload, provider.token_signing_key, algorithms=["ES256"],
                issuer=ISSUER, audience=RP_ENTITY_ID,
                options={"require": ["iss", "aud", "sub", "exp", "iat", "nonce"]},
            )
            if not isinstance(claims["nonce"], str) or not claims["nonce"].isascii() or not secrets.compare_digest(claims["nonce"], pending.nonce):
                raise ValueError("Invalid nonce")
            kvnr = claims.get("urn:telematik:claims:id")
            sub = claims["sub"]
            if not isinstance(kvnr, str) or not re.fullmatch(r"[A-Z][0-9]{9}", kvnr):
                raise ValueError("Invalid KVNR")
            if not isinstance(sub, str) or not sub or len(sub) > 128:
                raise ValueError("Invalid subject")
            if claims.get("urn:telematik:claims:profession") != "1.2.276.0.76.4.49":
                raise ValueError("Invalid profession")
            now = time.time()
            if settings.reference_demo_session_ttl_s:
                if not reference_mock_epa_available():
                    raise HTTPException(503, "Extended reference session requires the local mock profile.")
                expiry = now + settings.reference_demo_session_ttl_s
            else:
                expiry = min(float(claims["exp"]), now + 3600)
            session = reference_sessions.create_session(sub, kvnr, expiry)
            reference_sessions.revoke(request.cookies.get(SESSION_COOKIE, ""))
    except (httpx.HTTPError, ReferenceTrustError, JWException, jwt.PyJWTError,
            KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
        log.warning("Reference callback failed: %s", type(exc).__name__)
        raise HTTPException(401, "Reference authentication failed") from exc
    redirect = RedirectResponse(
        settings.reference_frontend_origin + "/", status_code=303,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
    redirect.delete_cookie(PENDING_COOKIE, path="/", samesite="lax")
    _cookie(redirect, SESSION_COOKIE, session, max(1, int(expiry - time.time())))
    return redirect


@router.get("/auth/reference/session")
def session_info(request: Request, response: Response):
    _enabled()
    session = reference_sessions.get_session(request.cookies.get(SESSION_COOKIE, ""))
    if not session:
        raise HTTPException(401, "Missing or expired reference session")
    response.headers["Cache-Control"] = "no-store"
    return {
        "sub": session.sub,
        "expiresAt": int(session.expires * 1000),
        "mockEpaAvailable": reference_mock_epa_available(),
    }


@router.post("/auth/reference/logout", status_code=204)
def logout(request: Request):
    _enabled()
    if request.headers.get("origin") != settings.reference_frontend_origin:
        raise HTTPException(403, "Invalid origin")
    reference_sessions.revoke(request.cookies.get(SESSION_COOKIE, ""))
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="lax")
    return response
