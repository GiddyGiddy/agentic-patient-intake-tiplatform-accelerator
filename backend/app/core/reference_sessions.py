"""Single-process, synthetic-data-only reference login transactions and sessions."""

import secrets
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class PendingLogin:
    state: str
    nonce: str
    verifier: str
    expires: float


@dataclass(frozen=True)
class ReferenceSession:
    sub: str
    kvnr: str
    expires: float


class ReferenceSessions:
    def __init__(self):
        self._lock = threading.Lock()
        self._pending: dict[str, PendingLogin] = {}
        self._sessions: dict[str, ReferenceSession] = {}

    def _purge(self):
        now = time.time()
        self._pending = {k: v for k, v in self._pending.items() if v.expires > now}
        self._sessions = {k: v for k, v in self._sessions.items() if v.expires > now}

    def create_pending(self, state: str, nonce: str, verifier: str) -> str:
        with self._lock:
            self._purge()
            if len(self._pending) >= 1000:
                raise RuntimeError("Too many pending reference logins")
            token = secrets.token_urlsafe(32)
            self._pending[token] = PendingLogin(state, nonce, verifier, time.time() + 300)
            return token

    def consume_pending(self, token: str) -> PendingLogin | None:
        with self._lock:
            self._purge()
            return self._pending.pop(token, None)

    def create_session(self, sub: str, kvnr: str, expires: float) -> str:
        with self._lock:
            self._purge()
            if len(self._sessions) >= 1000:
                raise RuntimeError("Too many reference sessions")
            token = secrets.token_urlsafe(32)
            self._sessions[token] = ReferenceSession(sub, kvnr, expires)
            return token

    def get_session(self, token: str) -> ReferenceSession | None:
        with self._lock:
            self._purge()
            return self._sessions.get(token)

    def revoke(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)


reference_sessions = ReferenceSessions()
