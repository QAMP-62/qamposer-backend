"""Tests for the Q-sphere layout against Qiskit's official plot_state_qsphere.

The fixture expectations were generated with Qiskit 2.5.0's own
plot_state_qsphere layout code (pure-state path). The same fixture is used by
the qamposer-react vitest suite, so the browser and server implementations
cannot drift apart.
"""

import json
from pathlib import Path

import numpy as np
import pytest
from qiskit import QuantumCircuit
from qiskit.quantum_info import Statevector

from backend.model.circuit import Gate
from backend.quantum.converter import json_to_qasm
from backend.quantum.qsphere import compute_qsphere_points

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "qsphere-parity.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text())
CASES = {name: case for name, case in FIXTURE.items() if not name.startswith("_")}


def _statevector_for_case(case: dict) -> Statevector:
    """Build the statevector through the production path (JSON -> QASM -> circuit)."""
    gates = [Gate(**gate) for gate in case["gates"]]
    qasm = json_to_qasm(case["qubits"], gates)
    circuit = QuantumCircuit.from_qasm_str(qasm)
    circuit.remove_final_measurements(inplace=True)
    return Statevector.from_instruction(circuit)


@pytest.mark.parametrize("name", sorted(CASES))
def test_matches_official_qsphere_layout(name: str) -> None:
    case = CASES[name]
    points = compute_qsphere_points(_statevector_for_case(case))
    expected = case["expected_qsphere"]

    assert [p.state for p in points] == [e["state"] for e in expected]
    for point, exp in zip(points, expected):
        assert point.x == pytest.approx(exp["x"], abs=1e-9)
        assert point.y == pytest.approx(exp["y"], abs=1e-9)
        assert point.z == pytest.approx(exp["z"], abs=1e-9)
        assert point.probability == pytest.approx(exp["probability"], abs=1e-9)
        assert point.phase == pytest.approx(exp["phase"], abs=1e-9)


def test_latitude_is_linear_in_hamming_weight() -> None:
    # n=3 weight-1 ring sits at z = 1/3 (official), not cos(pi/3) = 0.5
    state = Statevector(np.array([0, 1, 1, 0, 1, 0, 0, 0]) / np.sqrt(3))
    points = compute_qsphere_points(state)
    assert all(p.z == pytest.approx(1 / 3, abs=1e-9) for p in points)


def test_global_phase_is_removed() -> None:
    # e^{i*pi/6}|1>: the dominant amplitude's phase is normalized away
    state = Statevector(np.array([0, np.exp(1j * np.pi / 6)]))
    (point,) = compute_qsphere_points(state)
    assert point.state == "1"
    assert point.phase == pytest.approx(0.0, abs=1e-9)
    assert point.z == pytest.approx(-1.0, abs=1e-9)


def test_negligible_probabilities_are_omitted() -> None:
    bell = QuantumCircuit(2)
    bell.h(0)
    bell.cx(0, 1)
    points = compute_qsphere_points(Statevector.from_instruction(bell))
    assert [p.state for p in points] == ["00", "11"]


def test_returns_empty_beyond_max_qubits() -> None:
    state = Statevector.from_label("0" * 6)
    assert compute_qsphere_points(state, max_qubits=5) == []
