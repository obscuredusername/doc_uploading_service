"""
Storage backend selection.

``get_storage_backend()`` returns the backend chosen by
``settings.storage_backend`` ("s3" | "local"). Pass an explicit name to
override per call site (e.g. the collection portal always uses "local"
regardless of the global default).
"""
from functools import lru_cache

from app.config import settings
from app.services.storage.base import StorageBackend
from app.services.storage.local_backend import LocalStorageBackend
from app.services.storage.s3_backend import S3StorageBackend

__all__ = [
    "StorageBackend",
    "LocalStorageBackend",
    "S3StorageBackend",
    "get_storage_backend",
]

_BACKENDS = {
    "s3": S3StorageBackend,
    "local": LocalStorageBackend,
}


@lru_cache
def _cached(name: str) -> StorageBackend:
    try:
        return _BACKENDS[name]()
    except KeyError:
        raise ValueError(
            f"Unknown storage backend {name!r}. Valid: {sorted(_BACKENDS)}"
        )


def get_storage_backend(name: str | None = None) -> StorageBackend:
    return _cached(name or settings.storage_backend)
