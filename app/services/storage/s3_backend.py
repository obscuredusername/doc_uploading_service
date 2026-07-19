"""
S3 storage backend.

Thin adapter that satisfies :class:`StorageBackend` by delegating to the
existing :class:`app.services.s3_service.S3Service`, so the established S3
path keeps working unchanged while joining the new backend interface.
"""
from typing import Iterator

from app.config import settings
from app.core.s3 import get_s3_client
from app.services.s3_service import S3Service
from app.services.storage.base import StorageBackend

_PRESIGN_TTL_SECONDS = 3600


class S3StorageBackend(StorageBackend):
    name = "s3"

    def __init__(self, bucket: str | None = None) -> None:
        self._svc = S3Service(bucket=bucket)

    def save(self, key: str, data: bytes, content_type: str | None = None) -> None:
        self._svc.upload_bytes(data, key, content_type=content_type)

    def load(self, key: str) -> bytes:
        return self._svc.get_object_bytes(key)

    def open_stream(self, key: str) -> Iterator[bytes]:
        obj = self._svc.stream_object(key)
        yield from obj["Body"].iter_chunks()

    def delete(self, key: str) -> None:
        self._svc.delete_key(key)

    def public_url(self, key: str) -> str:
        # Presigned GET so callers can fetch directly without app auth.
        return get_s3_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": self._svc.bucket, "Key": key},
            ExpiresIn=_PRESIGN_TTL_SECONDS,
        )
