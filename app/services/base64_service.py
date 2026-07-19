"""
Parallel base64 pipeline.

Every uploaded document is also base64-encoded and POSTed to an external
service (config: BASE64_SERVICE_URL + BASE64_SERVICE_API_KEY). This is
independent of the S3 storage path — both pipelines run for the same file.

The encoder is trivial. The interesting work is the POST: transient failures
retry with backoff, permanent ones fail immediately so the per-document
`base64_status='failed'` makes the problem visible.
"""
import base64
from typing import Any

import httpx

from app.config import settings


class Base64PushError(Exception):
    """Raised when the push to the external service fails."""

    def __init__(self, message: str, *, transient: bool = True) -> None:
        super().__init__(message)
        self.transient = transient


def encode(file_bytes: bytes) -> str:
    return base64.b64encode(file_bytes).decode("ascii")


def push(payload: dict[str, Any]) -> dict[str, Any]:
    """
    POST the base64 payload to the configured external service.

    Returns the parsed JSON response (or `{}` if the body is empty).
    Raises Base64PushError on any failure.
    """
    if not settings.base64_service_url:
        raise Base64PushError(
            "BASE64_SERVICE_URL not configured", transient=False
        )

    headers = {"Content-Type": "application/json"}
    if settings.base64_service_api_key:
        headers["Authorization"] = f"Bearer {settings.base64_service_api_key}"

    try:
        response = httpx.post(
            settings.base64_service_url,
            json=payload,
            headers=headers,
            timeout=settings.base64_service_timeout_seconds,
        )
    except httpx.HTTPError as e:
        raise Base64PushError(f"network error: {e}", transient=True) from e

    if response.status_code >= 500:
        raise Base64PushError(
            f"upstream 5xx ({response.status_code})", transient=True
        )
    if response.status_code >= 400:
        raise Base64PushError(
            f"upstream {response.status_code}: {response.text[:200]}",
            transient=False,
        )

    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text[:500]}
