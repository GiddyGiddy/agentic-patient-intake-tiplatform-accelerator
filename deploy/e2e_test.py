"""Authenticated end-to-end check of a staging stack (stdlib only; runs inside the stack network).

Logs in at the mock IdP (authorization code + PKCE, no browser), then drives the intake through the
proxy: chat -> triage -> consent -> ePA read -> booking -> KIM. Exit code != 0 on any failed check.
Env: BASE (default https://proxy), IDP (default http://mock-id-dienst:8081), KVNR (default X110000001),
TIMEOUT_S per request (default 300; a CPU-only LLM can be slow).
"""
import base64
import hashlib
import json
import os
import secrets
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("BASE", "https://proxy").rstrip("/")
IDP = os.environ.get("IDP", "http://mock-id-dienst:8081").rstrip("/")
KVNR = os.environ.get("KVNR", "X110000001")
TIMEOUT = int(os.environ.get("TIMEOUT_S", "300"))
CLIENT_ID = "patient-intake-frontend"
REDIRECT_URI = os.environ.get("REDIRECT_URI") or f"https://{os.environ.get('STAGING_HOST') or 'localhost'}/"
TLS = ssl.create_default_context()
TLS.check_hostname = False
TLS.verify_mode = ssl.CERT_NONE
failures = 0


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f" ({detail})" if detail else ""), flush=True)
    failures += 0 if ok else 1


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def login() -> str:
    verifier = b64url(secrets.token_bytes(32))
    query = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": b64url(hashlib.sha256(verifier.encode()).digest()),
        "state": "e2e",
        "kvnr": KVNR,
        "action": "allow",
    })
    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(f"{IDP}/authorize/confirm?{query}", timeout=30)
        raise SystemExit("IdP did not redirect")
    except urllib.error.HTTPError as redirect:
        location = redirect.headers.get("Location", "")
        if redirect.code not in (301, 302, 303, 307) or not location:
            raise SystemExit(
                f"IdP rejected the login: HTTP {redirect.code} {redirect.read()[:200]!r} "
                f"(redirect_uri={REDIRECT_URI}; must be listed in the IdP's ALLOWED_REDIRECT_URIS, "
                "see STAGING_HOST / REDIRECT_URI)"
            ) from None
    code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query).get("code", [""])[0]
    check("IdP issued an authorization code", bool(code))
    form = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    }).encode()
    req = urllib.request.Request(f"{IDP}/token", data=form, method="POST")
    token = json.load(urllib.request.urlopen(req, timeout=30))["access_token"]
    check("IdP issued an access token", bool(token))
    return token


def call(method: str, path: str, token: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{BASE}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=TLS) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        return err.code, {"error": err.read()[:300].decode(errors="replace")}


def main() -> int:
    token = login()

    status, _ = call("DELETE", "/api/session", token)
    check("reset previous session", status == 204, f"HTTP {status}")

    messages = [
        "Ich habe seit drei Tagen Kopfschmerzen und leichtes Fieber.",
        "Es begann langsam, Schmerzstärke etwa 5 von 10, keine Übelkeit.",
        "Ich nehme keine weiteren Beschwerden wahr, die Symptome bleiben gleich.",
        "Keine Allergien bekannt.",
    ]
    view: dict = {}
    for i, text in enumerate(messages, 1):
        status, view = call("POST", "/api/chat", token, {"message": text})
        check(f"chat message {i}", status == 200, f"HTTP {status}" + (
            " - proxy timeout? raise proxy_read_timeout or use BASE=http://backend:8000" if status == 504 else ""))
        if status != 200:
            print(view)
            return 1
        print(f"     reply: {(view.get('reply') or '')[:100]!r} triage={view.get('triage_score')}", flush=True)
        if view.get("iam_required"):
            break
    check("triage finished and consent requested", bool(view.get("iam_required")), str(view.get("triage_score")))

    status, view = call("POST", "/api/consent", token, {"granted": True})
    check("consent accepted", status == 200 and view.get("consent_granted"), f"HTTP {status}")
    check("ePA medication plan read (TI gateway + FHIR)", bool(view.get("medication_plan")), str(view.get("medication_plan")))
    check("appointment booked (KIS)", bool(view.get("appointment_id")), str(view.get("appointment_id")))
    check("KIM message sent", view.get("kim_sent") is True)
    check("no scheduling failure", not view.get("scheduling_failed"))

    call("DELETE", "/api/session", token)
    print("PASSED" if failures == 0 else f"{failures} check(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
