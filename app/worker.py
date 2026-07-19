"""Celery worker entry point.

Run with:
    celery -A app.worker.celery_app worker --loglevel=info
"""
from app.core.celery_app import celery_app
from app.core.logging import configure_logging
from app.tasks import document_tasks  # noqa: F401  (ensure tasks are registered)

configure_logging()

__all__ = ["celery_app"]
