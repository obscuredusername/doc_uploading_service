"""
Structured JSON logging.

Single entrypoint — `configure_logging()` — called once from main.py and the
Celery worker bootstrap. Output is one JSON object per line so logs are
ingestible by Loki / CloudWatch / Datadog without custom parsing.
"""
import logging
import sys

from pythonjsonlogger import jsonlogger

from app.config import settings

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        jsonlogger.JsonFormatter(
            _LOG_FORMAT,
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG if settings.app_debug else logging.INFO)

    # Quiet down noisy libraries unless we're in debug mode.
    if not settings.app_debug:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        logging.getLogger("botocore").setLevel(logging.WARNING)
        logging.getLogger("boto3").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)

    _configured = True
