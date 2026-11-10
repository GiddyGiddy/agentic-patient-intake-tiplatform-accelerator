"""Minimal mock of an OIDC identity provider (gematik Gesundheits-ID). Dev only.

Implements the authorization code flow with PKCE (S256) for a public SPA client.
"""
import base64
import hashlib
import html
import json
import os
import secrets
import time
from urllib.parse import parse_qs, urlencode

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jwt.algorithms import RSAAlgorithm

ISSUER = "http://mock-id-dienst:8081"
AUDIENCE = "patient-intake"
KID = "mock-key-1"
CLIENT_ID = "patient-intake-frontend"
ALLOWED_REDIRECTS = set(
    os.getenv("ALLOWED_REDIRECT_URIS", "http://localhost:3000/").split(",")
)
DEMO_PATIENTS = {"X110000001": "Erika Mustermann", "X110000002": "Max Mustermann"}
CODE_TTL = 60
TOKEN_TTL = int(os.getenv("MOCK_ACCESS_TOKEN_TTL_S", "3600"))
if TOKEN_TTL <= 0:
    raise ValueError("MOCK_ACCESS_TOKEN_TTL_S must be a positive number of seconds")

_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_jwk = {
    **json.loads(RSAAlgorithm.to_jwk(_private.public_key())),
    "kid": KID,
    "use": "sig",
    "alg": "RS256",
}
CODES: dict[str, dict] = {}

app = FastAPI(title="Mock ID-Dienst")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[u.rstrip("/") for u in ALLOWED_REDIRECTS],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _check_client(client_id: str, redirect_uri: str) -> None:
    if client_id != CLIENT_ID:
        raise HTTPException(400, "unknown client_id")
    if redirect_uri not in ALLOWED_REDIRECTS:
        raise HTTPException(400, "redirect_uri not allowed")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@app.get("/.well-known/openid-configuration")
def discovery():
    return {
        "issuer": ISSUER,
        "jwks_uri": f"{ISSUER}/jwks",
        "authorization_endpoint": "/authorize",
        "token_endpoint": "/token",
        "end_session_endpoint": "/logout",
        "code_challenge_methods_supported": ["S256"],
    }


@app.get("/jwks")
def jwks():
    return {"keys": [_jwk]}


@app.get("/authorize", response_class=HTMLResponse)
def authorize(
    response_type: str = "",
    client_id: str = "",
    redirect_uri: str = "",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "",
):
    _check_client(client_id, redirect_uri)
    if response_type != "code" or code_challenge_method != "S256" or not code_challenge:
        raise HTTPException(400, "code flow with PKCE (S256) required")

    hidden = "".join(
        f'<input type="hidden" name="{k}" value="{html.escape(v, quote=True)}">'
        for k, v in {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
        }.items()
    )
    options = "".join(
        f'<label><input type="radio" name="kvnr" value="{k}" {"checked" if i == 0 else ""}> '
        f"{html.escape(name)} ({k})</label><br>"
        for i, (k, name) in enumerate(DEMO_PATIENTS.items())
    )
    return f"""<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gesundheits-ID (Mock)</title>
<style>body{{font-family:system-ui;max-width:420px;margin:3rem auto;padding:0 1rem}}
button{{padding:.6rem 1rem;margin-right:.5rem}}</style></head><body>
<h1>Gesundheits-ID (Mock)</h1>
<p>Entwicklungs-Login. Wählen Sie einen Testpatienten:</p>
<form method="get" action="/authorize/confirm">{hidden}{options}<p>
<button type="submit" name="action" value="allow">Anmelden</button>
<button type="submit" name="action" value="deny">Abbrechen</button></p></form>
</body></html>"""


@app.get("/authorize/confirm")
def confirm(
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str = "",
    kvnr: str = "",
    action: str = "allow",
):
    _check_client(client_id, redirect_uri)
    if action == "deny":
        query = urlencode({"error": "access_denied", "state": state})
        return RedirectResponse(f"{redirect_uri}?{query}", status_code=302)
    if kvnr not in DEMO_PATIENTS:
        raise HTTPException(400, "unknown patient")

    now = time.time()
    for k in [k for k, v in CODES.items() if v["exp"] < now]:
        del CODES[k]
    code = secrets.token_urlsafe(32)
    CODES[code] = {
        "sub": kvnr,
        "kvnr": kvnr,
        "challenge": code_challenge,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "exp": now + CODE_TTL,
    }
    query = urlencode({"code": code, "state": state})
    return RedirectResponse(f"{redirect_uri}?{query}", status_code=302)


@app.post("/token")
async def token(request: Request):
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}

    def fail(error: str):
        return JSONResponse({"error": error}, status_code=400)

    if form.get("grant_type") != "authorization_code":
        return fail("unsupported_grant_type")
    entry = CODES.pop(form.get("code", ""), None)  # codes are single-use
    if entry is None or entry["exp"] < time.time():
        return fail("invalid_grant")
    if (
        entry["client_id"] != form.get("client_id")
        or entry["redirect_uri"] != form.get("redirect_uri")
    ):
        return fail("invalid_grant")
    verifier = form.get("code_verifier", "")
    if not secrets.compare_digest(
        _b64url(hashlib.sha256(verifier.encode()).digest()), entry["challenge"]
    ):
        return fail("invalid_grant")

    now = int(time.time())
    access = jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": entry["sub"],
            "kvnr": entry["kvnr"],
            "iat": now,
            "exp": now + TOKEN_TTL,
        },
        _private,
        algorithm="RS256",
        headers={"kid": KID},
    )
    return {"access_token": access, "token_type": "Bearer", "expires_in": TOKEN_TTL}


@app.get("/logout")
def logout(post_logout_redirect_uri: str = ""):
    if post_logout_redirect_uri in ALLOWED_REDIRECTS:
        return RedirectResponse(post_logout_redirect_uri, status_code=302)
    return HTMLResponse("<p>Sie wurden abgemeldet.</p>")
