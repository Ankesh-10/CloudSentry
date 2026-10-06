import json
import logging
import os
import re
from datetime import datetime, timezone

# Attributes every LogRecord has; anything else was passed via `extra=`.
_STANDARD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}

# Values that must never reach a log line, wherever they appear in a message.
_SECRET_PATTERNS = [
    (re.compile(r"(postgres(?:ql)?://[^:/\s]+:)[^@\s]+(@)"), r"\1***\2"),              # DSN passwords
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1***"),                 # bearer tokens
    # JWTs: no leading \b, a token glued to other text ("...abceyJ...") must still go.
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+"), "***JWT***"),
    (re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"), "***AWS_KEY***"),                     # AWS access key ids
    (re.compile(r"(?i)((?:secret|password|api[_-]?key|token)\s*[=:]\s*)[^\s,;'\"]+"), r"\1***"),
]


def scrub(text: str) -> str:
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class SecretScrubFilter(logging.Filter):
    """Redacts credentials from the rendered message (and exception text,
    via the formatters below) before any handler sees it."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        scrubbed = scrub(message)
        if scrubbed != message:
            record.msg, record.args = scrubbed, ()
        return True


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        from backend.app.request_context import current_request_id
        if not hasattr(record, "request_id"):
            record.request_id = current_request_id()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line; parseable by Render/any log shipper."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and key not in payload and value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = scrub(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    def formatException(self, ei) -> str:
        return scrub(super().formatException(ei))


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, (level or "INFO").upper(), logging.INFO))
    handler = logging.StreamHandler()
    if os.getenv("LOG_FORMAT", "json").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(TextFormatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s"))
    handler.addFilter(RequestIdFilter())
    handler.addFilter(SecretScrubFilter())
    root.handlers = [handler]
    # Quiet chatty HTTP client loggers that would otherwise log every PostgREST call.
    for noisy in ("httpx", "httpcore", "botocore", "urllib3", "apscheduler.executors.default"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
