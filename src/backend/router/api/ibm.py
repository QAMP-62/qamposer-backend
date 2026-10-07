"""IBM Quantum real-hardware endpoints (users bring their own API key).

Security model:
- The API key is sent once (POST /api/ibm/session), validated, encrypted and kept
  server-side for the session only. It is never returned or logged.
- The browser only holds an opaque session id in an HttpOnly, Secure, SameSite=Strict
  cookie. State-changing requests must come from an allowed Origin (CSRF defence).
- Jobs can only be read or cancelled by the session that submitted them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from backend.config import Settings, get_settings
from backend.ibm.crypto import CredentialCipher, CredentialDecryptionError
from backend.ibm.gateway import (
    FINAL_STATUSES,
    Credentials,
    HardwareError,
    HardwareGateway,
    JobInfo,
    QiskitRuntimeGateway,
)
from backend.ibm.ratelimit import SlidingWindowLimiter
from backend.ibm.sessions import IbmSession, InMemorySessionStore, SessionStore
from backend.model.circuit import CircuitRequest
from backend.model.ibm import ConnectRequest, Device, Job, JobRequest, JobResult, SessionInfo, Usage
from backend.quantum.converter import json_to_qasm
from backend.quantum.simulator import _compute_qsphere

logger = logging.getLogger(__name__)

JOB_RATE_WINDOW_SECONDS = 60 * 60
CONNECT_FAILED = "Could not connect to IBM Quantum with this API key and instance."


class IbmState:
    """Process-wide state for the IBM endpoints (overridable in tests)."""

    def __init__(self, settings: Settings, gateway: HardwareGateway | None = None, clock=time.time) -> None:
        self.settings = settings
        self.clock = clock
        self.cipher = CredentialCipher.from_setting(settings.credential_encryption_key)
        self.store: SessionStore = InMemorySessionStore(self.cipher, settings.ibm_session_ttl_seconds, clock)
        self.gateway: HardwareGateway = gateway or QiskitRuntimeGateway()
        self.connect_limiter = SlidingWindowLimiter(settings.ibm_session_rate_limit_per_minute, 60)


_state: IbmState | None = None


def get_state() -> IbmState:
    global _state
    if _state is None:
        _state = IbmState(get_settings())
    return _state


def cookie_name(settings: Settings) -> str:
    # The __Host- prefix requires Secure and Path=/, and pins the cookie to this host
    return "__Host-qamposer_ibm" if settings.cookie_secure else "qamposer_ibm"


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def require_allowed_origin(request: Request, state: IbmState = Depends(get_state)) -> None:  # noqa: B008
    """CSRF defence for state-changing requests: the Origin must be an allowed site."""
    origin = request.headers.get("origin")
    if origin is None or origin not in state.settings.allowed_origins:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Origin not allowed")


def require_enabled(state: IbmState = Depends(get_state)) -> None:  # noqa: B008
    if not state.settings.ibm_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Real hardware is not enabled on this server")


router = APIRouter(
    prefix="/api/ibm",
    tags=["ibm"],
    dependencies=[Depends(require_enabled), Depends(no_store)],
)


def _client_ip(request: Request, settings: Settings) -> str:
    # Each trusted proxy appends the address it saw; earlier entries can be forged
    if settings.trusted_proxy_hops > 0:
        forwarded = [ip.strip() for ip in request.headers.get("x-forwarded-for", "").split(",") if ip.strip()]
        if len(forwarded) >= settings.trusted_proxy_hops:
            return forwarded[-settings.trusted_proxy_hops]
    return request.client.host if request.client else "unknown"


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _session_from_cookie(request: Request, state: IbmState) -> tuple[str, IbmSession] | None:
    session_id = request.cookies.get(cookie_name(state.settings))
    if not session_id:
        return None
    session = state.store.get(session_id)
    return (session_id, session) if session else None


def _require_session(request: Request, state: IbmState) -> tuple[str, IbmSession, Credentials]:
    found = _session_from_cookie(request, state)
    if found is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not connected to IBM Quantum")
    session_id, session = found
    try:
        api_key = state.store.api_key(session_id, session)
    except CredentialDecryptionError as e:
        state.store.delete(session_id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not connected to IBM Quantum") from e
    credentials = Credentials(
        cache_key=state.cipher.session_key(session_id), api_key=api_key, instance=session.instance
    )
    return session_id, session, credentials


async def _call(func, *args):
    """Run a blocking gateway call, turning its failures into safe HTTP errors."""
    try:
        return await run_in_threadpool(func, *args)
    except HardwareError as e:
        raise HTTPException(e.status_code, e.user_message) from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 - details may contain credentials; log the type only
        logger.error("IBM Quantum request failed: %s", type(e).__name__)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "IBM Quantum request failed") from e


def _set_cookie(response: Response, state: IbmState, session_id: str, max_age: int) -> None:
    response.set_cookie(
        cookie_name(state.settings),
        session_id,
        max_age=max_age,
        path="/",
        secure=state.settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _clear_cookie(response: Response, state: IbmState) -> None:
    response.delete_cookie(
        cookie_name(state.settings),
        path="/",
        secure=state.settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


# === Session ===


@router.post("/session", response_model=SessionInfo, dependencies=[Depends(require_allowed_origin)])
async def connect(
    body: ConnectRequest,
    request: Request,
    response: Response,
    state: IbmState = Depends(get_state),  # noqa: B008
) -> SessionInfo:
    if not state.connect_limiter.allow(_client_ip(request, state.settings)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many connection attempts; try again later")

    # Replace any existing session from this browser
    previous = _session_from_cookie(request, state)
    if previous:
        state.store.delete(previous[0])

    probe = Credentials(cache_key=f"connect:{id(body)}", api_key=body.api_key, instance=body.instance)
    try:
        instance = await asyncio.wait_for(
            _call(state.gateway.connect, probe), timeout=state.settings.ibm_connect_timeout_seconds
        )
    except TimeoutError as e:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "IBM Quantum did not respond in time") from e
    finally:
        state.gateway.forget(probe.cache_key)

    session_id, session = state.store.create(body.api_key, instance or body.instance)
    _set_cookie(response, state, session_id, state.settings.ibm_session_ttl_seconds)
    return SessionInfo(connected=True, instance=session.instance, expires_at=_iso(session.expires_at))


@router.get("/session", response_model=SessionInfo)
async def session_info(request: Request, state: IbmState = Depends(get_state)) -> SessionInfo:  # noqa: B008
    found = _session_from_cookie(request, state)
    if found is None:
        return SessionInfo(connected=False)
    _, session = found
    return SessionInfo(connected=True, instance=session.instance, expires_at=_iso(session.expires_at))


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_allowed_origin)])
async def disconnect(request: Request, response: Response, state: IbmState = Depends(get_state)) -> None:  # noqa: B008
    found = _session_from_cookie(request, state)
    if found:
        state.gateway.forget(state.cipher.session_key(found[0]))
        state.store.delete(found[0])
    _clear_cookie(response, state)


# === Devices and usage ===


@router.get("/backends", response_model=list[Device])
async def devices(
    request: Request,
    min_qubits: int = Query(1, ge=1, le=127),  # noqa: B008
    state: IbmState = Depends(get_state),  # noqa: B008
) -> list[Device]:
    _, _, credentials = _require_session(request, state)
    found = await _call(state.gateway.devices, credentials, min_qubits)
    return [Device(**d.__dict__) for d in found]


@router.get("/usage", response_model=Usage)
async def usage(request: Request, state: IbmState = Depends(get_state)) -> Usage:  # noqa: B008
    _, _, credentials = _require_session(request, state)
    info = await _call(state.gateway.usage, credentials)
    return Usage(**info.__dict__)


# === Jobs ===


def _to_job(info: JobInfo, qasm: str | None) -> Job:
    result = None
    if info.status == "done" and info.counts is not None:
        qsphere = None
        if qasm is not None:
            from qiskit import QuantumCircuit

            qsphere = _compute_qsphere(QuantumCircuit.from_qasm_str(qasm))
        result = JobResult(counts=info.counts, execution_time=info.quantum_seconds or 0, qsphere=qsphere)
    return Job(id=info.id, backend=info.backend, status=info.status, result=result, error=info.error)


@router.post(
    "/jobs",
    response_model=Job,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_allowed_origin)],
)
async def submit_job(body: JobRequest, request: Request, state: IbmState = Depends(get_state)) -> Job:  # noqa: B008
    session_id, session, credentials = _require_session(request, state)
    settings = state.settings

    if body.shots > settings.ibm_max_shots:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Shots must be at most {settings.ibm_max_shots}")
    try:
        # Same gate validation as the simulator endpoint
        CircuitRequest(qubits=body.qubits, gates=body.gates, shots=1024)
    except ValidationError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid circuit") from e

    # One job at a time per session
    if session.active_job_id is not None:
        active = await _call(state.gateway.job, credentials, session.active_job_id)
        if active.status not in FINAL_STATUSES:
            raise HTTPException(status.HTTP_409_CONFLICT, "A job is already queued or running")
        session.active_job_id = None

    now = state.clock()
    while session.submissions and now - session.submissions[0] >= JOB_RATE_WINDOW_SECONDS:
        session.submissions.popleft()
    if len(session.submissions) >= settings.ibm_job_rate_limit_per_hour:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many jobs this hour; try again later")

    qasm = json_to_qasm(qubits=body.qubits, gates=body.gates)
    info = await _call(
        state.gateway.submit, credentials, qasm, body.backend_name, body.shots, settings.ibm_max_execution_time
    )

    session.submissions.append(now)
    session.jobs[info.id] = qasm
    session.active_job_id = info.id
    state.store.save(session_id, session)
    logger.info("Submitted IBM Quantum job to %s", info.backend)
    return _to_job(info, qasm)


JOB_ID_PATTERN = r"^[A-Za-z0-9_\-]{1,128}$"


async def _session_job(
    state: IbmState, session_id: str, session: IbmSession, credentials: Credentials, job_id: str
) -> tuple[JobInfo, str | None]:
    """
    Fetch a job for this session. Jobs submitted from another session (e.g. before the
    server restarted and the user reconnected) are only returned when IBM confirms they
    belong to this key's instance; the gateway raises 404 otherwise, so the IBM account,
    not a guessable id, is what authorizes access.
    """
    info = await _call(state.gateway.job, credentials, job_id)
    if job_id not in session.jobs:
        session.jobs[job_id] = ""  # adopted: the circuit (for the Q-sphere) is unknown
        state.store.save(session_id, session)
    return info, session.jobs[job_id] or None


@router.get("/jobs/{job_id}", response_model=Job)
async def job_status(
    request: Request,
    job_id: str = Path(..., pattern=JOB_ID_PATTERN),  # noqa: B008
    state: IbmState = Depends(get_state),  # noqa: B008
) -> Job:
    session_id, session, credentials = _require_session(request, state)
    info, qasm = await _session_job(state, session_id, session, credentials, job_id)
    if info.status in FINAL_STATUSES and session.active_job_id == job_id:
        session.active_job_id = None
        state.store.save(session_id, session)
    return _to_job(info, qasm)


@router.delete(
    "/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_allowed_origin)]
)
async def cancel_job(
    request: Request,
    job_id: str = Path(..., pattern=JOB_ID_PATTERN),  # noqa: B008
    state: IbmState = Depends(get_state),  # noqa: B008
) -> None:
    session_id, session, credentials = _require_session(request, state)
    await _session_job(state, session_id, session, credentials, job_id)
    await _call(state.gateway.cancel, credentials, job_id)
    if session.active_job_id == job_id:
        session.active_job_id = None
        state.store.save(session_id, session)
