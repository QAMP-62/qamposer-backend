from pathlib import Path

# common
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# logging
LOG_FORMAT = "[%(process)d][%(levelname)s][%(name)s] %(message)s"

# encoding
UTF8 = "utf-8"

# version
VERSION = "0.1.0"

# debug
ENV_DEBUG_MODE = "DEBUG"


# openapi
TAG_METADATA = "Metadata"
TAG_USER = "User"
TAG_SECURITY = "Security"


# security
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 4
# Credentials (JWT signing key, user, password hash) come from the environment:
# see backend.config.Settings and .env.example.
