"""
Local-disk storage backend.

Writes objects under ``settings.local_storage_dir`` using the storage key as
a relative path, and serves them back through this service's public file
route (``/files/<key>``). Chosen for the collection-portal flow where we want
files kept on our own server with links that are live for anyone to use
(spec decision #5 — public for now).
"""
from pathlib import Path
from typing import Iterator

from app.config import settings
from app.services.storage.base import StorageBackend

_CHUNK = 1024 * 1024  # 1 MiB


class LocalStorageBackend(StorageBackend):
    name = "local"

    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or settings.local_storage_dir).resolve()

    def _path_for(self, key: str) -> Path:
        # Resolve and confine to root — reject traversal via keys like "../..".
        target = (self.root / key).resolve()
        if self.root not in target.parents and target != self.root:
            raise ValueError(f"Refusing storage key outside root: {key!r}")
        return target

    def save(self, key: str, data: bytes, content_type: str | None = None) -> None:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def load(self, key: str) -> bytes:
        return self._path_for(key).read_bytes()

    def open_stream(self, key: str) -> Iterator[bytes]:
        path = self._path_for(key)
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(_CHUNK)
                if not chunk:
                    break
                yield chunk

    def delete(self, key: str) -> None:
        self._path_for(key).unlink(missing_ok=True)

    def public_url(self, key: str) -> str:
        base = settings.public_base_url.rstrip("/")
        return f"{base}/files/{key.lstrip('/')}"
