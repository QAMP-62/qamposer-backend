"""Utility to compute Q-sphere coordinates from a statevector."""

from __future__ import annotations

from math import comb

import numpy as np
from qiskit.quantum_info import Statevector

from backend.model.circuit import QSpherePoint

# Points below this probability are omitted to reduce clutter.
_PROBABILITY_THRESHOLD = 1e-12


def _bitstring(index: int, num_qubits: int) -> str:
    return format(index, f"0{num_qubits}b")


def _bit_string_index(bitstring: str) -> int:
    """
    Lexicographic rank of a bitstring among all bitstrings of the same
    length and Hamming weight ('1' positions counted from the left).
    """
    n = len(bitstring)
    ones = [pos for pos, char in enumerate(bitstring) if char == "1"]
    k = len(ones)
    positions = [n - 1 - x for x in ones]
    return sum(comb(positions[k - 1 - i], i + 1) for i in range(k))


def compute_qsphere_points(state: Statevector, max_qubits: int = 5) -> list[QSpherePoint]:
    """
    Compute Q-sphere coordinates from a statevector.

    Point placement follows Qiskit's official ``plot_state_qsphere`` layout
    (``qiskit/visualization/state_visualization.py``): latitude is linear in
    Hamming weight (z = 1 - 2*weight/n), longitude is assigned by
    combinatorial rank within each weight ring with a per-ring offset and a
    southern-hemisphere mirror rule, and the global phase is removed before
    computing per-state phases.

    Args:
        state: Final statevector of the circuit (without measurements).
        max_qubits: Return an empty list if the circuit exceeds this many qubits.

    Returns:
        List of QSpherePoint entries. States with probability below 1e-12 are
        omitted; the remaining probabilities are reported as-is (they sum to
        ~1 for a pure state).
    """
    num_qubits = state.num_qubits
    if num_qubits > max_qubits:
        return []

    amplitudes = np.asarray(state.data)
    dim = amplitudes.shape[0]

    # Remove the global phase so the largest-magnitude amplitude has phase 0.
    # Magnitudes are rounded to 13 decimals before argmax, as in the official
    # code, so machine-epsilon noise cannot change which amplitude wins.
    loc = np.round(np.absolute(amplitudes), decimals=13).argmax()
    global_angle = (np.angle(amplitudes[loc]) + 2 * np.pi) % (2 * np.pi)
    amplitudes = np.exp(-1j * global_angle) * amplitudes

    d = num_qubits
    points: list[QSpherePoint] = []
    for idx in range(dim):
        amplitude = amplitudes[idx]
        probability = min(float(np.real(amplitude * np.conj(amplitude))), 1.0)
        if probability < _PROBABILITY_THRESHOLD:
            continue

        element = _bitstring(idx, num_qubits)
        weight = element.count("1")
        zvalue = 1 - 2 * weight / d
        number_of_divisions = comb(d, weight)
        weight_order = _bit_string_index(element)

        angle = (weight / d) * 2 * np.pi + weight_order * 2 * np.pi / number_of_divisions
        if (weight > d / 2) or ((weight == d / 2) and (weight_order >= number_of_divisions / 2)):
            angle = np.pi - angle - 2 * np.pi / number_of_divisions

        radius = np.sqrt(max(0.0, 1 - zvalue**2))
        points.append(
            QSpherePoint(
                state=element,
                x=float(radius * np.cos(angle)),
                y=float(radius * np.sin(angle)),
                z=float(zvalue),
                probability=probability,
                phase=float(np.angle(amplitude)),
            )
        )

    return points
