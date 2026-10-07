"""Session store for IBM credentials.

A session is identified by an opaque random id that only the browser holds (in an
HttpOnly cookie). The store keys records by an HMAC of that id and keeps the API key
encrypted, bound to its record through AES-GCM associated data.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import SecretStr

from backend.ibm.crypto import CredentialCipher

SESSION_ID_BYTES = 32


@dataclass
class IbmSession:
    instance: str | None
    created_at: float
    expires_at: float
    encrypted_api_key: bytes = field(repr=False)
    # Jobs submitted from this session (id -> QASM); only these may be read or cancelled
    jobs: dict[str, str] = field(default_factory=dict)
    active_job_id: str | None = None
    # Submission times for the per-session rate limit
    submissions: deque[float] = field(default_factory=deque)


class SessionStore(Protocol):
    def create(self, api_key: SecretStr, instance: str | None) -> tuple[str, IbmSession]: ...

    def get(self, session_id: str) -> IbmSession | None: ...

    def api_key(self, session_id: str, session: IbmSession) -> SecretStr: ...

    def save(self, session_id: str, session: IbmSession) -> None: ...

    def delete(self, session_id: str) -> None: ...


class InMemorySessionStore:
    """
    Process-local store with absolute expiry. Suitable for a single worker process;
    swap in a shared implementation (e.g. Redis) before running several workers.
    """

    def __init__(self, cipher: CredentialCipher, ttl_seconds: int, clock=time.time) -> None:
        self._cipher = cipher
        self._ttl = ttl_seconds
        self._clock = clock
        self._sessions: dict[str, IbmSession] = {}
        self._lock = threading.Lock()

    def create(self, api_key: SecretStr, instance: str | None) -> tuple[str, IbmSession]:
        session_id = secrets.token_urlsafe(SESSION_ID_BYTES)
        key = self._cipher.session_key(session_id)
        now = self._clock()
        session = IbmSession(
            instance=instance,
            created_at=now,
            expires_at=now + self._ttl,
            encrypted_api_key=self._cipher.encrypt(api_key, aad=key.encode()),
        )
        with self._lock:
            self._purge_expired(now)
            self._sessions[key] = session
        return session_id, session

    def get(self, session_id: str) -> IbmSession | None:
        key = self._cipher.session_key(session_id)
        now = self._clock()
        with self._lock:
            session = self._sessions.get(key)
            if session is not None and session.expires_at <= now:
                del self._sessions[key]
                return None
            return session

    def api_key(self, session_id: str, session: IbmSession) -> SecretStr:
        key = self._cipher.session_key(session_id)
        return self._cipher.decrypt(session.encrypted_api_key, aad=key.encode())

    def save(self, session_id: str, session: IbmSession) -> None:
        # Records are mutated in place; kept for stores that serialize them
        with self._lock:
            self._sessions[self._cipher.session_key(session_id)] = session

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(self._cipher.session_key(session_id), None)

    def _purge_expired(self, now: float) -> None:
        for key in [k for k, s in self._sessions.items() if s.expires_at <= now]:
            del self._sessions[key]
