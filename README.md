# Qamposer Backend

Quantum circuit simulation backend for [qamposer-react](https://github.com/QAMP-62/qamposer-react). Powered by FastAPI and Qiskit.

## Overview

This backend provides the quantum simulation engine for Qamposer. While `qamposer-react` handles the UI (circuit editing, visualization), this backend executes the actual quantum simulations using Qiskit.

```
┌─────────────────────┐         ┌─────────────────────┐
│   qamposer-react    │  HTTP   │  qamposer-backend   │
│  (Circuit Editor)   │ ──────► │  (Qiskit Simulator) │
│                     │ ◄────── │                     │
└─────────────────────┘         └─────────────────────┘
```

## Requirements

- Python 3.13+
- uv

## Quick Start

```bash
# Install dependencies
uv sync

# Start the server
uv run uvicorn backend.main:app --host 0.0.0.0 --port 8080 --reload
```

The API will be available at `http://localhost:8080`.

## API Documentation

Once the server is running:

- Swagger UI: `http://localhost:8080/docs`
- ReDoc: `http://localhost:8080/redoc`

## API Endpoints

### POST /api/circuit/simulate

Execute a quantum circuit simulation.

**Request:**

```json
{
  "qubits": 2,
  "gates": [
    { "type": "H", "qubit": 0, "position": 0 },
    { "type": "CNOT", "control": 0, "target": 1, "position": 1 }
  ],
  "shots": 1024,
  "profile": {
    "type": "ideal"
  }
}
```

**Response:**

```json
{
  "counts": { "00": 512, "11": 512 },
  "execution_time": 0.042
}
```

### GET /api/circuit/backends

List available simulation backends.

### GET /health

Health check endpoint.

## Configuration

Create a `.env` file from the template:

```bash
cp .env.example .env
```

Key environment variables:

| Variable       | Description          | Default                     |
| -------------- | -------------------- | --------------------------- |
| `ENVIRONMENT`  | Runtime environment  | `development`               |
| `LOG_LEVEL`    | Logging level        | `INFO`                      |
| `CORS_ORIGINS` | Allowed CORS origins | `["http://localhost:5173"]` |

## Development

```bash
# Run tests
uv run pytest

# Lint
uv run ruff check .

# Type check
uv run mypy src/
```

## Docker

```bash
# Build
docker build . -t qamposer-backend

# Run
docker run -p 8080:8080 qamposer-backend
```

## License

Apache 2.0
