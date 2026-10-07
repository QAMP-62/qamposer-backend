"""Exercise the real Qiskit pipeline of QiskitRuntimeGateway with qiskit-ibm-runtime's
local testing mode (fake backends, no IBM account or network)."""

import pytest
from pydantic import SecretStr
from qiskit import QuantumCircuit
from qiskit.transpiler import generate_preset_pass_manager
from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2

from backend.ibm.gateway import Credentials, HardwareError, QiskitRuntimeGateway
from backend.model.circuit import Gate
from backend.quantum.converter import json_to_qasm

BELL_QASM = json_to_qasm(
    qubits=2,
    gates=[
        Gate(type="H", qubit=0, position=0),
        Gate(type="CNOT", control=0, target=1, position=1),
    ],
)
CREDENTIALS = Credentials(cache_key="test", api_key=SecretStr("unused"), instance=None)


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> QiskitRuntimeGateway:
    gateway = QiskitRuntimeGateway()
    local = QiskitRuntimeService(channel="local")
    monkeypatch.setattr(gateway, "_service", lambda credentials: local)
    return gateway


def test_submits_an_isa_circuit_to_a_device(gateway: QiskitRuntimeGateway) -> None:
    job = gateway.submit(CREDENTIALS, BELL_QASM, "fake_manila", shots=100, max_execution_time=None)
    assert job.id
    assert job.backend == "fake_manila"
    assert job.status == "queued"


def test_rejects_devices_with_too_few_qubits(gateway: QiskitRuntimeGateway) -> None:
    five_qubits = BELL_QASM.replace("qreg q[2]", "qreg q[6]").replace("creg c[2]", "creg c[6]")
    with pytest.raises(HardwareError) as error:
        gateway.submit(CREDENTIALS, five_qubits, "fake_manila", shots=100, max_execution_time=None)
    assert error.value.status_code == 400


def test_rejects_unknown_devices(gateway: QiskitRuntimeGateway) -> None:
    with pytest.raises(HardwareError) as error:
        gateway.submit(CREDENTIALS, BELL_QASM, "no_such_device", shots=100, max_execution_time=None)
    assert error.value.status_code == 400


def test_counts_are_read_from_classical_register_c() -> None:
    # Mirrors how QiskitRuntimeGateway.job() extracts counts from a finished job
    backend = QiskitRuntimeService(channel="local").backend("fake_manila")
    circuit = QuantumCircuit.from_qasm_str(BELL_QASM)
    isa = generate_preset_pass_manager(backend=backend, optimization_level=1).run(circuit)
    result = SamplerV2(mode=backend).run([isa], shots=200).result()

    counts = result[0].data.c.get_counts()

    assert sum(counts.values()) == 200
    assert counts.get("00", 0) + counts.get("11", 0) > 150  # mostly Bell outcomes despite noise
    assert all(len(key) == 2 for key in counts)
