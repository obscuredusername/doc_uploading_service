"""
OCR provider abstraction (spec §10).

Initial provider: OCR.space. Swapping to Textract / Tesseract later is a
change inside `perform_ocr` only — the signature is provider-agnostic.
"""
from typing import Any

import httpx

from app.config import settings

OCR_SPACE_ENDPOINT = "https://api.ocr.space/parse/image"


class OCRError(Exception):
    """Raised on OCR provider failure. Wraps both transient and permanent failures."""

    def __init__(self, message: str, *, transient: bool = True) -> None:
        super().__init__(message)
        self.transient = transient


def perform_ocr(file_bytes: bytes, content_type: str, *, is_table: bool, file_name: str) -> dict[str, Any]:
    """
    Send a file to the configured OCR provider and return the raw response dict.

    Raises OCRError on failure. `transient=True` means the worker should retry.
    """
    if not settings.ocr_api_key:
        raise OCRError("OCR provider not configured: OCR_API_KEY is empty", transient=False)

    data = {
        "apikey": settings.ocr_api_key,
        "language": settings.ocr_default_language,
        "isOverlayRequired": "false",
        "OCREngine": "2",
        "scale": "true",
        "isTable": "true" if is_table else "false",
    }
    files = {"file": (file_name, file_bytes, content_type or "application/octet-stream")}

    try:
        response = httpx.post(
            OCR_SPACE_ENDPOINT,
            data=data,
            files=files,
            timeout=settings.ocr_request_timeout_seconds,
        )
    except httpx.HTTPError as e:
        raise OCRError(f"OCR network error: {e}", transient=True) from e

    if response.status_code >= 500:
        raise OCRError(f"OCR provider 5xx ({response.status_code})", transient=True)
    if response.status_code >= 400:
        raise OCRError(
            f"OCR provider rejected request ({response.status_code}): {response.text[:200]}",
            transient=False,
        )

    payload = response.json()
    if payload.get("IsErroredOnProcessing"):
        msg = payload.get("ErrorMessage") or "OCR provider returned error"
        if isinstance(msg, list):
            msg = "; ".join(str(m) for m in msg)
        raise OCRError(str(msg), transient=False)

    return payload


def extract_flat_text(ocr_payload: dict[str, Any]) -> str:
    """Pull the flat text out of an OCR.space response."""
    parsed = ocr_payload.get("ParsedResults") or []
    return "\n".join((r.get("ParsedText") or "") for r in parsed).strip()
