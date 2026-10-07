"""Models for the IBM Quantum real-hardware API."""

from typing import Literal, Optional

from pydantic import BaseModel, Field, SecretStr

from backend.model.circuit import Gate, QSpherePoint


class ConnectRequest(BaseModel):
    # SecretStr keeps the key out of reprs, logs and validation error messages
    api_key: SecretStr = Field(..., min_length=1, max_length=512, description="IBM Cloud API key")
    instance: Optional[str] = Field(default=None, max_length=512, description="Instance CRN or name (optional)")


class SessionInfo(BaseModel):
    connected: bool
    instance: Optional[str] = None
    expires_at: Optional[str] = Field(default=None, description="ISO 8601 (UTC)")


class Device(BaseModel):
    name: str
    num_qubits: int
    pending_jobs: int
    operational: bool


class Usage(BaseModel):
    remaining_seconds: Optional[float] = None
    consumed_seconds: Optional[float] = None
    limit_reached: bool = False


class JobRequest(BaseModel):
    qubits: int = Field(..., ge=1, le=5)
    gates: list[Gate] = Field(..., min_length=1, max_length=20)
    shots: int = Field(default=1024, ge=1)
    backend_name: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_\-]+$")


class JobResult(BaseModel):
    counts: dict[str, int]
    execution_time: float = Field(default=0, description="QPU time in seconds (0 when unknown)")
    qsphere: Optional[list[QSpherePoint]] = None


class Job(BaseModel):
    id: str
    backend: str
    status: Literal["queued", "running", "done", "error", "cancelled"]
    result: Optional[JobResult] = None
    error: Optional[str] = None
