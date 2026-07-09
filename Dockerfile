# ---- builder ----
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

WORKDIR /app

# uv settings: copy files into the venv (no hardlinks across mounts) and
# precompile bytecode for faster container startup.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential git && \
    rm -rf /var/lib/apt/lists/*

# Install dependencies first (cached layer, independent of source changes).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Install the project itself.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---- runtime ----
FROM python:3.13-slim AS runtime

WORKDIR /app

# Bring in the fully built virtual environment and the app source.
COPY --from=builder /app /app

# Put the venv on PATH so `gunicorn` / `backend` resolve without activation.
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8080

ENTRYPOINT [ "gunicorn", "backend.main:app", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8080" ]
