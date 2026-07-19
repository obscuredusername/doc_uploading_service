from celery import Celery

from app.config import settings

celery_app = Celery(
    "upload_doc",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.document_tasks"],
)

celery_app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="upload_doc",
    timezone="UTC",
    enable_utc=True,
)
