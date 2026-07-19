from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "upload-doc-service"
    app_env: str = "development"
    app_debug: bool = True
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # Used to build s3_url / s3_url_ocr_json values when storing documents.
    # Spec §15.1 decision: auth-proxy. The stored URL points back at this
    # service; clients fetch through /v1/documents/<id>/file. Also the base
    # for public upload-portal links and local file URLs.
    public_base_url: str = "http://localhost:8000"

    # Storage backend for document bytes: "s3" (default) or "local".
    # The collection-portal flow uses "local" explicitly regardless of this.
    storage_backend: str = "s3"
    local_storage_dir: str = "var/uploads"
    # When true, locally-stored files are served publicly (no auth) at /files.
    # Seam for later flipping to signed/expiring links (spec decision #5).
    public_files: bool = True

    # Collection portal: how long an unused upload link stays live.
    document_request_ttl_days: int = 7
    # Tenant the staff portal operates as. If unset, the single existing
    # tenant is used; ambiguous when more than one tenant exists.
    portal_tenant_name: Optional[str] = None

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/upload_doc"
    )

    # Redis / Celery (internal microservice queue — tenant brokers are separate)
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # Rate-limit storage. Defaults to in-memory so tests + local dev don't
    # require Redis. Production / docker-compose override to a redis:// URI.
    rate_limit_storage_uri: str = "memory://"

    # AWS / S3
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_s3_region: str = "eu-north-1"
    aws_s3_bucket_name: str = ""
    s3_endpoint_url: Optional[str] = None

    # Presigned URL expiry for the `s3_url` exposed to consumers.
    # 7 days = SigV4 maximum. Consumers cache the URL in Redis between apps;
    # before this window expires we re-issue and re-publish (no-op for now —
    # we just regenerate on every read).
    s3_presigned_url_expires_seconds: int = 7 * 24 * 60 * 60

    # Upload constraints (spec §6.1)
    upload_max_bytes: int = 10 * 1024 * 1024
    upload_mime_allowlist: list[str] = [
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "image/jpeg",
        "image/png",
        "image/webp",
    ]

    # OCR provider (spec §10)
    ocr_api_key: str = ""
    ocr_default_language: str = "eng"
    ocr_request_timeout_seconds: float = 60.0
    ocr_table_mode_document_types: list[str] = [
        "bank_statement",
        "creditor_statement",
        "creditor_report",
    ]
    ocr_max_retries: int = 3

    # External base64-storage service (parallel pipeline alongside S3).
    # When BASE64_SERVICE_URL is empty the push task records a permanent
    # failure so the misconfiguration is visible per-document.
    base64_service_url: str = ""
    base64_service_api_key: str = ""
    base64_service_timeout_seconds: float = 60.0
    base64_max_retries: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
