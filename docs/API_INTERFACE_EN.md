# qamposer-backend API Interface

API interface specification for qamposer-backend used by qamposer-scanner.

## Overview

### Base URL

| Environment | URL                     |
| ----------- | ----------------------- |
| Development | `http://localhost:8080` |
| Production  | TBD                     |

### Common Headers

```
Content-Type: application/json
```

## API Endpoints

### POST /api/circuit/simulate

Simulate a quantum circuit and return measurement results.

#### Request

```typescript
interface CircuitRequest {
  qubits: number; // Number of qubits (1-5)
  gates: Gate[]; // List of gates (max 20)
  shots: number; // Number of shots (100-10000, default: 1024)
  profile: SimulationProfile; // Simulation settings
}

interface Gate {
  type: string; // Gate type
  qubit?: number; // Target qubit for single-qubit gates (0-indexed)
  control?: number; // Control qubit for CNOT (0-indexed)
  target?: number; // Target qubit for CNOT (0-indexed)
  parameter?: number; // Rotation angle in radians
  position: number; // Position in circuit (column index, 0-indexed)
}

interface SimulationProfile {
  type: "ideal" | "noisy_fake"; // Backend type
  backend_name?: string; // Backend name (required for noisy_fake)
  seed?: number; // Random seed for reproducibility
}
```

#### Supported Gate Types

| type   | Description          | Required Fields                           |
| ------ | -------------------- | ----------------------------------------- |
| `H`    | Hadamard gate        | qubit, position                           |
| `X`    | Pauli-X gate         | qubit, position                           |
| `Y`    | Pauli-Y gate         | qubit, position                           |
| `Z`    | Pauli-Z gate         | qubit, position                           |
| `CNOT` | Controlled-NOT gate  | control, target, position                 |
| `RX`   | X-axis rotation gate | qubit, position, parameter (default: π/4) |
| `RY`   | Y-axis rotation gate | qubit, position, parameter (default: π/4) |
| `RZ`   | Z-axis rotation gate | qubit, position, parameter (default: π/4) |

#### Request Example

```json
{
  "qubits": 2,
  "gates": [
    {
      "type": "H",
      "qubit": 0,
      "position": 0
    },
    {
      "type": "CNOT",
      "control": 0,
      "target": 1,
      "position": 1
    }
  ],
  "shots": 1024,
  "profile": {
    "type": "ideal"
  }
}
```

#### Response (Success: 200 OK)

```typescript
interface SimulationResult {
  counts: Record<string, number>; // Measurement counts
  execution_time: number; // Execution time in seconds
  qsphere?: QSpherePoint[]; // Q-sphere coordinates (optional)
}

interface QSpherePoint {
  state: string; // Basis state (e.g., "00", "01")
  x: number; // X coordinate (-1 to 1)
  y: number; // Y coordinate (-1 to 1)
  z: number; // Z coordinate (-1 to 1)
  probability: number; // Probability (0 to 1)
  phase: number; // Phase in radians (-π to π)
}
```

#### Response Example

```json
{
  "counts": {
    "00": 512,
    "11": 512
  },
  "execution_time": 0.125,
  "qsphere": [
    {
      "state": "00",
      "x": 0.0,
      "y": 0.0,
      "z": 1.0,
      "probability": 0.5,
      "phase": 0.0
    },
    {
      "state": "11",
      "x": 0.0,
      "y": 0.0,
      "z": -1.0,
      "probability": 0.5,
      "phase": 0.0
    }
  ]
}
```

#### Error Responses

**400 Bad Request (Validation Error)**

```json
{
  "detail": "Invalid gate type: SWAP. Must be one of ['H', 'X', 'Y', 'Z', 'CNOT', 'RX', 'RY', 'RZ']",
  "error_code": "VALIDATION_ERROR"
}
```

**500 Internal Server Error (Simulation Error)**

```json
{
  "detail": "Simulation failed: circuit too complex",
  "error_code": "SIMULATION_ERROR"
}
```

---

### GET /api/circuit/gates

Get the list of supported gates.

#### Response (200 OK)

```json
{
  "single_qubit": ["H", "X", "Y", "Z"],
  "rotation": ["RX", "RY", "RZ"],
  "two_qubit": ["CNOT"]
}
```

---

### GET /api/circuit/backends

Get the list of available backends.

#### Response (200 OK)

```typescript
interface BackendInfo {
  id: string; // Backend identifier
  name: string; // Display name
  num_qubits: number; // Number of supported qubits
  backend_type: "ideal" | "noisy_fake"; // Type
  description?: string; // Description
}
```

#### Response Example

```json
[
  {
    "id": "ideal",
    "name": "Ideal Simulator",
    "num_qubits": 32,
    "backend_type": "ideal",
    "description": "Noiseless statevector simulator"
  },
  {
    "id": "fake_manila",
    "name": "Fake Manila",
    "num_qubits": 5,
    "backend_type": "noisy_fake",
    "description": "Noisy simulator based on IBM Manila"
  }
]
```

---

### GET /health

Health check endpoint.

#### Response (200 OK)

```json
{
  "status": "ok"
}
```

---

### GET /version

Version information.

#### Response (200 OK)

```json
{
  "version": "1.0.0"
}
```

---

## CORS Configuration

Allowed origins are set with the `ALLOWED_ORIGINS` environment variable (JSON list). The default allows `http://localhost:3000`, `http://localhost:4321`, `http://localhost:5173`, `https://qamp-62.github.io`, `https://qamposer.org` and `https://www.qamposer.org`. Credentials (cookies) are allowed for these origins.

---

## Real Hardware (IBM Quantum)

Bring-your-own-key access to IBM Quantum devices. All endpoints are under `/api/ibm` and return `Cache-Control: no-store`.

- **Session cookie:** `POST /api/ibm/session` sets `__Host-qamposer_ibm` (`HttpOnly; Secure; SameSite=Strict; Path=/`). Browsers must send requests with credentials (`fetch(..., { credentials: 'include' })`), from a page on the same site as the API.
- **CSRF:** `POST` and `DELETE` requests must carry an `Origin` header listed in `ALLOWED_ORIGINS`, otherwise `403`.
- **Errors:** `{"detail": "<message safe to show>"}`. `401` = not connected (or the key was rejected), `404` = job not found, `409` = a job is already active / cannot be cancelled, `429` = rate limited, `502` / `504` = IBM Quantum failed / timed out. Validation errors (`422`) do not echo the input.

### POST /api/ibm/session

Connect with an IBM Cloud API key. The key is validated (no QPU time used), encrypted and kept for the session only.

```typescript
// Request
{ api_key: string; instance?: string /* CRN or name */ }
// Response 200 (+ Set-Cookie)
{ connected: true; instance: string | null; expires_at: string /* ISO 8601 */ }
```

### GET /api/ibm/session

```typescript
{ connected: boolean; instance: string | null; expires_at: string | null }
```

### DELETE /api/ibm/session

Disconnect: deletes the stored key and clears the cookie. `204`.

### GET /api/ibm/backends?min_qubits=N

Operational devices available to the instance with at least `N` qubits, shortest queue first.

```typescript
Array<{ name: string; num_qubits: number; pending_jobs: number; operational: boolean }>
```

### GET /api/ibm/usage

```typescript
{ remaining_seconds: number | null; consumed_seconds: number | null; limit_reached: boolean }
```

### POST /api/ibm/jobs

Submit a circuit (same gate format and limits as `/api/circuit/simulate`). It is transpiled to the device's native gates and layout (ISA) and run with SamplerV2. One active job per session; shots up to `IBM_MAX_SHOTS`.

```typescript
// Request
{ qubits: number; gates: Gate[]; shots: number; backend_name: string }
// Response 201
Job
```

### GET /api/ibm/jobs/{id}

```typescript
interface Job {
  id: string;
  backend: string;
  status: "queued" | "running" | "done" | "error" | "cancelled";
  result: null | {
    counts: Record<string, number>; // same bit order as the simulator
    execution_time: number;         // QPU seconds (0 when unknown)
    qsphere: QSpherePoint[] | null; // ideal statevector; null if the circuit is unknown to this session
  };
  error: string | null;
}
```

### DELETE /api/ibm/jobs/{id}

Cancel a queued or running job. `204`.

---

## Constraints

| Item             | Constraint                           |
| ---------------- | ------------------------------------ |
| Number of qubits | 1-5                                  |
| Number of gates  | Max 20                               |
| Number of shots  | 100-10,000                           |
| Rotation angle   | Radians (default: π/4 ≈ 0.785398163) |

---

_Last updated: 2026-10-06_
