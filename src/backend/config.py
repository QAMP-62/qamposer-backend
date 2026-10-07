"""Application settings, read from environment variables (and `.env` in development).

Secrets are never hard-coded: anything sensitive is a `SecretStr` that is only
unwrapped where it is used, so it does not show up in reprs or logs.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:4321",
    "http://localhost:5173",
    "https://qamp-62.github.io",
    "https://qamposer.org",
    "https://www.qamposer.org",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    debug: bool = Field(default=False, description="Debug mode (exposes /openapi.json)")

    # --- Password login (/token). Disabled unless all of these are set. ---
    jwt_secret_key: SecretStr | None = Field(default=None, description="HS256 signing key for access tokens")
    auth_user: str | None = Field(default=None, description="Pre-defined user name")
    auth_password_hash: SecretStr | None = Field(default=None, description="bcrypt hash of the user's password")

    # --- CORS / CSRF ---
    allowed_origins: list[str] = Field(default_factory=lambda: list(DEFAULT_ALLOWED_ORIGINS))

    # --- IBM Quantum (real hardware, bring-your-own-key) ---
    ibm_enabled: bool = Field(default=True, description="Expose the /api/ibm endpoints")
    credential_encryption_key: SecretStr | None = Field(
        default=None,
        description=(
            "Base64-encoded 32-byte key for encrypting stored IBM API keys. Only needed when the "
            "session store is shared or persistent; the in-memory store generates one per process."
        ),
    )
    ibm_session_ttl_seconds: int = Field(default=2 * 60 * 60, ge=60, description="Absolute lifetime of a session")
    ibm_max_shots: int = Field(default=4096, ge=1)
    ibm_max_execution_time: int | None = Field(
        default=None, ge=1, description="Per-job QPU time cap in seconds (SamplerV2 max_execution_time)"
    )
    ibm_session_rate_limit_per_minute: int = Field(default=5, ge=1, description="Connect attempts per IP")
    ibm_job_rate_limit_per_hour: int = Field(default=10, ge=1, description="Job submissions per session")
    ibm_connect_timeout_seconds: float = Field(default=20.0, gt=0)

    # Number of reverse proxies in front of the app (e.g. 1 on Render). Used to read the
    # client IP from X-Forwarded-For for rate limiting; 0 trusts no forwarding headers.
    trusted_proxy_hops: int = Field(default=0, ge=0)

    # Secure cookies need HTTPS. Browsers treat http://localhost as secure, so keep this on
    # unless a non-localhost plain-HTTP setup requires turning it off.
    cookie_secure: bool = True

    @property
    def password_login_enabled(self) -> bool:
        return bool(self.jwt_secret_key and self.auth_user and self.auth_password_hash)


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Backwards-compatible flag used across the app
DEBUG_MODE = get_settings().debug
