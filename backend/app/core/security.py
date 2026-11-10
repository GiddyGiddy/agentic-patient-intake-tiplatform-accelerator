import hashlib

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import settings
from app.core.reference_sessions import reference_sessions

_bearer = HTTPBearer(auto_error=False)
_jwks = jwt.PyJWKClient(settings.oidc_jwks_url)


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """Validates the Gesundheits-ID access token (OIDC, RS256) against the IdP JWKS."""
    if settings.reference_bff_enabled:
        if not settings.reference_rp_enabled:
            raise HTTPException(503, "Reference RP is not configured")
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("origin") != settings.reference_frontend_origin:
            raise HTTPException(403, "Invalid origin")
        session = reference_sessions.get_session(request.cookies.get("reference_session", ""))
        if not session:
            raise HTTPException(401, "Missing or expired reference session")
        sub = "reference:" + hashlib.sha256(session.sub.encode("utf-8")).hexdigest()
        return {"sub": sub, "kvnr": session.kvnr, "reference_session": True}
    if settings.auth_disabled:
        return {"sub": "dev-patient", "kvnr": settings.dev_kvnr}
    if creds is None:
        raise HTTPException(401, "Missing bearer token")
    try:
        key = _jwks.get_signing_key_from_jwt(creds.credentials).key
        return jwt.decode(
            creds.credentials,
            key,
            algorithms=["RS256"],
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"Invalid token: {exc}") from exc
