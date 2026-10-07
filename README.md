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

Environment variables (all optional; see `.env.example`):

| Variable                                         | Description                                                                 | Default            |
| ------------------------------------------------ | --------------------------------------------------------------------------- | ------------------ |
| `DEBUG`                                          | Debug mode (exposes `/openapi.json`)                                        | `false`            |
| `ALLOWED_ORIGINS`                                | Origins allowed by CORS and by the CSRF check (JSON list)                   | localhost + qamposer.org |
| `JWT_SECRET_KEY`, `AUTH_USER`, `AUTH_PASSWORD_HASH` | Password login (`/token`); disabled unless all three are set             | unset              |
| `IBM_ENABLED`                                    | Expose the real-hardware endpoints (`/api/ibm/*`)                          | `true`             |
| `IBM_SESSION_TTL_SECONDS`                        | Lifetime of a real-hardware session                                        | `7200`             |
| `IBM_MAX_SHOTS`                                  | Max shots per hardware job                                                 | `4096`             |
| `IBM_MAX_EXECUTION_TIME`                         | QPU-time cap per job in seconds (SamplerV2 `max_execution_time`)           | unset              |
| `IBM_SESSION_RATE_LIMIT_PER_MINUTE`              | Connect attempts per client IP                                             | `5`                |
| `IBM_JOB_RATE_LIMIT_PER_HOUR`                    | Job submissions per session                                                | `10`               |
| `TRUSTED_PROXY_HOPS`                             | Reverse proxies in front of the app (read the client IP from `X-Forwarded-For`; `1` on Render) | `0` |
| `CREDENTIAL_ENCRYPTION_KEY`                      | Base64 32-byte key for stored API keys; only needed for a shared session store | random per process |
| `COOKIE_SECURE`                                  | `Secure` session cookie (keep on; `http://localhost` counts as secure)     | `true`             |

## Real Hardware (IBM Quantum)

Users run circuits on IBM Quantum devices with **their own** API key (bring-your-own-key). The server never owns a key and keeps a user's key only for their session.

**Flow:** `POST /api/ibm/session` (key) → `GET /api/ibm/backends` → `POST /api/ibm/jobs` → poll `GET /api/ibm/jobs/{id}` → `DELETE /api/ibm/session`. See [docs/API_INTERFACE_EN.md](docs/API_INTERFACE_EN.md#real-hardware-ibm-quantum).

**Security model**

- The key is sent once, validated with IBM without using QPU time, encrypted with AES-256-GCM (bound to its session slot) and stored in memory keyed by an HMAC of the session id. It is never returned, logged, or written to disk (`QiskitRuntimeService.save_account` is not used).
- The browser gets an opaque session id in a `__Host-qamposer_ibm` cookie: `HttpOnly; Secure; SameSite=Strict; Path=/`, expiring with the session.
- State-changing requests must come from an `ALLOWED_ORIGINS` origin (CSRF defence on top of `SameSite=Strict`). Responses are `Cache-Control: no-store`; validation errors never echo the request body.
- A job can be read or cancelled only by its session, or by a session whose IBM key owns it (IBM is the authority), so a reconnected user can follow a job submitted before a restart.
- Limits: connect attempts per IP, job submissions per session, one active job per session, a shots cap and an optional QPU-time cap per job.
- Logs pass through a filter that masks credential-like values; `qiskit_ibm_runtime`, `urllib3` and `requests` log at WARNING or above.

**Deployment**

- Serve the API on the same site as the frontend over HTTPS (e.g. `api.qamposer.org` for `qamposer.org`); the session cookie is not sent to other sites.
- Behind a reverse proxy, set `TRUSTED_PROXY_HOPS` so rate limits apply per client.
- Sessions live in process memory: run a single worker (the Docker image does), and expect users to reconnect after a restart. Running several workers or instances needs a shared session store (implement `SessionStore` in `backend/ibm/sessions.py`, e.g. with Redis) and a fixed `CREDENTIAL_ENCRYPTION_KEY`.

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
