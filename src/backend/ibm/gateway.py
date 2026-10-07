"""Access to IBM Quantum through qiskit-ibm-runtime.

Everything that talks to IBM goes through `HardwareGateway`, so the HTTP layer can be
tested with a fake. All methods are blocking; call them from a thread pool.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import SecretStr

if TYPE_CHECKING:
    from qiskit_ibm_runtime import QiskitRuntimeService

ChannelType = Literal["ibm_quantum_platform", "ibm_cloud", "local"]

JobStatus = Literal["queued", "running", "done", "error", "cancelled"]

_STATUS_MAP: dict[str, JobStatus] = {
    "INITIALIZING": "queued",
    "QUEUED": "queued",
    "RUNNING": "running",
    "DONE": "done",
    "ERROR": "error",
    "CANCELLED": "cancelled",
}

FINAL_STATUSES: frozenset[JobStatus] = frozenset({"done", "error", "cancelled"})


class HardwareError(Exception):
    """A failure whose message is safe to show to the user (never contains credentials)."""

    def __init__(self, user_message: str, status_code: int = 502) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.status_code = status_code


@dataclass(frozen=True)
class Credentials:
    """Decrypted credentials for one request. `cache_key` identifies the session."""

    cache_key: str
    api_key: SecretStr
    instance: str | None


@dataclass(frozen=True)
class DeviceInfo:
    name: str
    num_qubits: int
    pending_jobs: int
    operational: bool


@dataclass(frozen=True)
class UsageInfo:
    remaining_seconds: float | None
    consumed_seconds: float | None
    limit_reached: bool


@dataclass(frozen=True)
class JobInfo:
    id: str
    backend: str
    status: JobStatus
    counts: dict[str, int] | None = None
    quantum_seconds: float | None = None
    error: str | None = None


class HardwareGateway(Protocol):
    def connect(self, credentials: Credentials) -> str:
        """Validate the credentials without using QPU time; return the active instance."""
        ...

    def devices(self, credentials: Credentials, min_qubits: int) -> list[DeviceInfo]: ...

    def usage(self, credentials: Credentials) -> UsageInfo: ...

    def submit(
        self, credentials: Credentials, qasm: str, backend_name: str, shots: int, max_execution_time: int | None
    ) -> JobInfo: ...

    def job(self, credentials: Credentials, job_id: str) -> JobInfo:
        """Look up a job. Must raise HardwareError(404) for jobs the credentials cannot access."""
        ...

    def cancel(self, credentials: Credentials, job_id: str) -> None: ...

    def forget(self, cache_key: str) -> None:
        """Drop anything cached for a session (on disconnect)."""
        ...


class QiskitRuntimeGateway:
    """
    `HardwareGateway` backed by `QiskitRuntimeService`. Services are cached per session
    for a short time, so polling a job does not re-authenticate with IBM every time.
    Credentials are never written to disk (`save_account` is not used).
    """

    def __init__(self, channel: ChannelType = "ibm_quantum_platform", cache_ttl_seconds: float = 300) -> None:
        self._channel: ChannelType = channel
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, QiskitRuntimeService]] = {}
        self._lock = threading.Lock()

    def _service(self, credentials: Credentials) -> QiskitRuntimeService:
        from qiskit_ibm_runtime import QiskitRuntimeService

        now = time.monotonic()
        with self._lock:
            # Drop expired services: they hold the plaintext key in memory
            for key in [k for k, (t, _) in self._cache.items() if now - t >= self._cache_ttl]:
                del self._cache[key]
            cached = self._cache.get(credentials.cache_key)
            if cached:
                return cached[1]
        try:
            service = QiskitRuntimeService(
                channel=self._channel,
                token=credentials.api_key.get_secret_value(),
                instance=credentials.instance or None,
            )
        except Exception as e:  # noqa: BLE001 - IBM raises several types; never echo the details
            raise HardwareError(
                "Could not connect to IBM Quantum with this API key and instance.", status_code=401
            ) from e
        with self._lock:
            self._cache[credentials.cache_key] = (now, service)
        return service

    def forget(self, cache_key: str) -> None:
        with self._lock:
            self._cache.pop(cache_key, None)

    def connect(self, credentials: Credentials) -> str:
        service = self._service(credentials)
        try:
            service.backends(simulator=False)
            return service.active_instance() or credentials.instance or ""
        except Exception as e:  # noqa: BLE001
            self.forget(credentials.cache_key)
            raise HardwareError(
                "Could not connect to IBM Quantum with this API key and instance.", status_code=401
            ) from e

    def devices(self, credentials: Credentials, min_qubits: int) -> list[DeviceInfo]:
        service = self._service(credentials)
        devices = []
        for backend in service.backends(simulator=False, operational=True, min_num_qubits=min_qubits):
            status = backend.status()
            devices.append(
                DeviceInfo(
                    name=str(backend.name),
                    num_qubits=backend.num_qubits,
                    pending_jobs=status.pending_jobs,
                    operational=status.operational,
                )
            )
        return sorted(devices, key=lambda d: d.pending_jobs)

    def usage(self, credentials: Credentials) -> UsageInfo:
        usage = self._service(credentials).usage()
        return UsageInfo(
            remaining_seconds=usage.get("usage_remaining_seconds"),
            consumed_seconds=usage.get("usage_consumed_seconds"),
            limit_reached=bool(usage.get("usage_limit_reached")),
        )

    def submit(
        self, credentials: Credentials, qasm: str, backend_name: str, shots: int, max_execution_time: int | None
    ) -> JobInfo:
        from qiskit import QuantumCircuit
        from qiskit.transpiler import generate_preset_pass_manager
        from qiskit_ibm_runtime import SamplerV2

        service = self._service(credentials)
        try:
            backend = service.backend(backend_name)
        except Exception as e:  # noqa: BLE001
            raise HardwareError(f"Device '{backend_name}' is not available to this instance.", 400) from e

        circuit = QuantumCircuit.from_qasm_str(qasm)
        if backend.num_qubits < circuit.num_qubits:
            raise HardwareError(f"Device '{backend_name}' has too few qubits for this circuit.", 400)

        # IBM Runtime only accepts circuits in the device's native gates and layout (ISA)
        isa_circuit = generate_preset_pass_manager(backend=backend, optimization_level=1).run(circuit)
        sampler = SamplerV2(mode=backend)
        if max_execution_time is not None:
            sampler.options.max_execution_time = max_execution_time  # type: ignore[attr-defined]
        job = sampler.run([isa_circuit], shots=shots)
        return JobInfo(id=job.job_id(), backend=backend_name, status="queued")

    def job(self, credentials: Credentials, job_id: str) -> JobInfo:
        try:
            # IBM only returns jobs that belong to this key's instance
            job = self._service(credentials).job(job_id)
        except HardwareError:
            raise
        except Exception as e:  # noqa: BLE001
            raise HardwareError("Job not found", 404) from e
        status = _STATUS_MAP.get(str(job.status()), "queued")
        backend = str(getattr(job.backend(), "name", "") or "")
        if status == "done":
            # The generated QASM measures into classical register `c`
            counts = dict(job.result()[0].data.c.get_counts())
            try:
                quantum_seconds = float(job.usage())
            except Exception:  # noqa: BLE001 - usage is informational
                quantum_seconds = None
            return JobInfo(job_id, backend, status, counts=counts, quantum_seconds=quantum_seconds)
        if status == "error":
            return JobInfo(job_id, backend, status, error="The job failed on IBM Quantum.")
        return JobInfo(job_id, backend, status)

    def cancel(self, credentials: Credentials, job_id: str) -> None:
        try:
            self._service(credentials).job(job_id).cancel()
        except Exception as e:  # noqa: BLE001
            raise HardwareError("The job could not be cancelled (it may have finished).", 409) from e
