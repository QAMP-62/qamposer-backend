from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.config import get_settings
from backend.router.api.auth import router as auth_router
from backend.router.api.circuit import router as circuit_router
from backend.router.api.ibm import router as ibm_router
from backend.router.api.meta import router as meta_router
from backend.router.api.user import router as user_router
from backend.router.lifespan import shut_down, start_up


@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_up()
    yield
    shut_down()


settings = get_settings()
app = FastAPI(
    title="Qamposer Backend",
    summary="MVP Backend for Qamposer",
    openapi_url="/openapi.json" if settings.debug else "",
    lifespan=lifespan,
    middleware=(),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(meta_router)
app.include_router(user_router)
app.include_router(auth_router)
app.include_router(circuit_router)
app.include_router(ibm_router)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    # FastAPI echoes the rejected input; never do that where requests carry credentials
    if request.url.path.startswith("/api/ibm"):
        errors = [{k: v for k, v in e.items() if k not in ("input", "ctx")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})
    return await request_validation_exception_handler(request, exc)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    # HSTS only means something over HTTPS (behind a TLS-terminating proxy, trust its header)
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # Real-hardware responses (including errors) must never be cached
    if request.url.path.startswith("/api/ibm"):
        response.headers["Cache-Control"] = "no-store"
    return response
