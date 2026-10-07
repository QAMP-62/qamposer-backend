from logging import ERROR, WARNING, getLogger
from logging.config import dictConfig

from backend.config import DEBUG_MODE
from backend.const import LOG_FORMAT

LOG_LEVEL = "INFO" if DEBUG_MODE else "INFO"

logging_config = {
    "version": 1,
    "disable_existing_loggers": False,
    "root": {"level": LOG_LEVEL, "handlers": ["consoleHandler"]},
    "loggers": {"backend": {"propagate": 0, "handlers": ["consoleHandler"], "level": LOG_LEVEL}},
    "handlers": {
        "consoleHandler": {
            "class": "logging.StreamHandler",
            "level": "DEBUG",
            "formatter": "consoleFormatter",
            "stream": "ext://sys.stdout",
            "filters": ["redact"],
        }
    },
    "filters": {"redact": {"()": "backend.util.redact.RedactingFilter"}},
    "formatters": {
        "consoleFormatter": {
            "format": LOG_FORMAT,
        }
    },
}


def init() -> None:
    dictConfig(logging_config)
    logger = getLogger(__name__)
    logger.info("Logger initialized")
    logger.info(f"DEBUG MODE: {DEBUG_MODE}")
    logger.info(f"LOG LEVEL: {LOG_LEVEL}")
    logger.debug("TEST DEBUG MESSAGE")
    getLogger("passlib").setLevel(ERROR)
    # These can log request details (URLs, headers) that include credentials at lower levels
    for name in ("qiskit_ibm_runtime", "urllib3", "requests", "ibm_cloud_sdk_core"):
        getLogger(name).setLevel(WARNING)
