import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_context = ContextVar("request_id", default=None)


class SafeJsonFormatter(logging.Formatter):
    def format(self, record):
        result = {
            "time": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage()[:200],
            "logger": record.name,
        }
        if request_id_context.get():
            result["request_id"] = request_id_context.get()
        for key in (
            "request_id",
            "job_id",
            "execution_id",
            "article_id",
            "script_version_id",
            "operation",
            "status_code",
        ):
            if getattr(record, key, None) is not None:
                result[key] = str(getattr(record, key))
        # Tracebacks and arbitrary extra fields may contain provider payloads.
        return json.dumps(result)


def configure_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(SafeJsonFormatter())
    logger = logging.getLogger("app")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
