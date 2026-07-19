from functools import lru_cache

import boto3
from botocore.client import BaseClient
from botocore.config import Config

from app.config import settings


@lru_cache
def get_s3_client() -> BaseClient:
    """
    Returns a cached boto3 S3 client. Safe to call from both FastAPI
    request handlers (sync calls should be offloaded via run_in_threadpool
    if they are large) and Celery workers.
    """
    return boto3.client(
        "s3",
        region_name=settings.aws_s3_region,
        aws_access_key_id=settings.aws_access_key_id or None,
        aws_secret_access_key=settings.aws_secret_access_key or None,
        endpoint_url=settings.s3_endpoint_url,
        config=Config(
            retries={"max_attempts": 5, "mode": "standard"},
            signature_version="s3v4",
        ),
    )
