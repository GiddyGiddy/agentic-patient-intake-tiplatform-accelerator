import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import settings

_bearer = HTTPBearer(auto_error=False)
_jwks = jwt.PyJWKClient(settings.oidc_jwks_url)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """Validates the Gesundheits-ID access token (OIDC, RS256) against the IdP JWKS."""
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
