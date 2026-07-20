"""
Storage backend abstraction.

Both the existing S3 path and the new local-disk path implement this same
interface, so the rest of the app never cares where bytes actually live —
it just picks a backend (see ``get_storage_backend``) and calls these
methods. This is the seam that lets us keep S3 for one flow and serve
locally-stored files for the collection-portal flow.
"""
from abc import ABC, abstractmethod
from typing import Iterator


class StorageBackend(ABC):
    """A place to put and fetch document bytes, keyed by an opaque string."""

    name: str

    @abstractmethod
    def save(self, key: str, data: bytes, content_type: str | None = None) -> None:
        """Persist ``data`` at ``key``, overwriting any existing object."""

    @abstractmethod
    def load(self, key: str) -> bytes:
        """Return the full object at ``key``. Raises if it does not exist."""

    @abstractmethod
    def open_stream(self, key: str) -> Iterator[bytes]:
        """Yield the object at ``key`` in chunks, without loading it all at once."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove the object at ``key``. Idempotent — missing key is a no-op."""

    @abstractmethod
    def public_url(self, key: str) -> str:
        """A URL a client can use to fetch the object.

        For local storage this points at this service's public file route;
        for S3 it is a presigned URL. Callers store this on the Document row.
        """

    def ensure_dir(self, key: str) -> None:
        """Create an (empty) directory at ``key``. No-op for object stores."""
        return None
