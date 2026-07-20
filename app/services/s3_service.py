import json
import uuid
from typing import BinaryIO

from botocore.exceptions import ClientError

from app.config import settings
from app.core.s3 import get_s3_client

ORIGINAL_PREFIX = "original__"
OCR_JSON_KEY = "ocr.json"


def build_original_key(tenant_id: uuid.UUID, owner_ref: str, doc_id: uuid.UUID, file_name: str) -> str:
    """Spec §5 path: <tenant_id>/<owner_ref>/<doc_id>/original__<file_name>"""
    return f"{tenant_id}/{owner_ref}/{doc_id}/{ORIGINAL_PREFIX}{file_name}"


def build_ocr_json_key(tenant_id: uuid.UUID, owner_ref: str, doc_id: uuid.UUID) -> str:
    return f"{tenant_id}/{owner_ref}/{doc_id}/{OCR_JSON_KEY}"


def build_proxy_url(doc_id: uuid.UUID, *, kind: str = "file") -> str:
    """
    Fallback URL pointing at this microservice's auth-proxy endpoints.
    Not used as the primary URL anymore (we publish presigned S3 URLs), but
    kept so the `/file` and `/ocr-json` endpoints remain reachable for
    consumers that prefer a stable, service-mediated path.
    """
    base = settings.public_base_url.rstrip("/")
    if kind == "file":
        return f"{base}/v1/documents/{doc_id}/file"
    if kind == "ocr_json":
        return f"{base}/v1/documents/{doc_id}/ocr-json"
    raise ValueError(f"Unknown URL kind: {kind}")


class S3Service:
    def __init__(self, bucket: str | None = None) -> None:
        self.client = get_s3_client()
        self.bucket = bucket or settings.aws_s3_bucket_name

    def upload_fileobj(self, fileobj: BinaryIO, key: str, content_type: str | None = None) -> None:
        extra = {"ContentType": content_type} if content_type else {}
        self.client.upload_fileobj(fileobj, self.bucket, key, ExtraArgs=extra)

    def upload_bytes(self, data: bytes, key: str, content_type: str | None = None) -> None:
        kwargs = {"Bucket": self.bucket, "Key": key, "Body": data}
        if content_type:
            kwargs["ContentType"] = content_type
        self.client.put_object(**kwargs)

    def upload_json(self, payload: dict, key: str) -> None:
        self.upload_bytes(
            json.dumps(payload).encode("utf-8"),
            key,
            content_type="application/json",
        )

    def get_object_bytes(self, key: str) -> bytes:
        resp = self.client.get_object(Bucket=self.bucket, Key=key)
        return resp["Body"].read()

    def stream_object(self, key: str):
        """Return the raw botocore StreamingBody so handlers can stream it."""
        return self.client.get_object(Bucket=self.bucket, Key=key)

    def generate_presigned_get(self, key: str, expires_in: int) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def delete_key(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except ClientError:
            # Idempotent delete; swallow "no such key"
            pass

    def delete_prefix(self, prefix: str) -> None:
        """Best-effort recursive delete of every object under a prefix."""
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            contents = page.get("Contents") or []
            if not contents:
                continue
            self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": obj["Key"]} for obj in contents]},
            )


# --- presigned URL helpers ----------------------------------------------------
#
# `document.s3_url` is now a presigned URL that consumers cache in Redis and
# share between apps. Presigned URLs expire (max 7 days for SigV4), so we
# regenerate at every read instead of trusting the stored value.

def fresh_s3_url_for(document) -> str:
    s3 = S3Service()
    return s3.generate_presigned_get(
        build_original_key(
            document.tenant_id, document.owner_ref, document.id, document.file_name
        ),
        settings.s3_presigned_url_expires_seconds,
    )


def fresh_ocr_json_url_for(document) -> str | None:
    """Returns a fresh presigned URL only when OCR has produced a JSON file."""
    # Avoid importing OcrStatus to keep this module free of model coupling.
    if document.ocr_status != "done":
        return None
    s3 = S3Service()
    return s3.generate_presigned_get(
        build_ocr_json_key(document.tenant_id, document.owner_ref, document.id),
        settings.s3_presigned_url_expires_seconds,
    )


def _is_local_url(url: str | None) -> bool:
    """Local-storage docs carry a servable /files/... URL, not an S3 key."""
    return bool(url) and "/files/" in url


def refresh_document_urls(document) -> None:
    """Replace the document's stored URL fields with fresh presigned values.

    Mutates in-place. Only applies to S3-backed documents: if no S3 bucket is
    configured, or the document is stored locally (its URL already points at
    /files/...), the stored value is kept as-is. Call before serializing to a
    DocumentRead or building the notifier payload.
    """
    if not settings.aws_s3_bucket_name or _is_local_url(document.s3_url):
        return
    document.s3_url = fresh_s3_url_for(document)
    document.s3_url_ocr_json = fresh_ocr_json_url_for(document)
