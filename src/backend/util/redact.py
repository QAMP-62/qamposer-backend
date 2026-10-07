"""Logging filter that masks credential-looking values."""

from __future__ import annotations

import logging
import re

_PATTERNS = [
    # key=value / "key": "value" for credential-like names
    re.compile(
        r"""(?i)((?:api[_-]?key|apikey|token|secret|password|authorization)["']?\s*[:=]\s*["']?)([^\s"',}]+)"""
    ),
    # Bearer tokens
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]+)"),
]
# Long opaque strings such as IBM Cloud API keys (44 chars) or JWTs
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_-]{40,}\b")


def redact(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + "[REDACTED]", text)
    return _LONG_TOKEN.sub("[REDACTED]", text)


_FORMATTER = logging.Formatter()


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        if record.exc_info and not record.exc_text:
            # Render the traceback now, redacted; formatters reuse exc_text as-is
            record.exc_text = redact(_FORMATTER.formatException(record.exc_info))
        return True
