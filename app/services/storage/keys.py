"""
Storage key layout for the collection portal.

A case gets one folder named ``<reference>__<name>``; inside it, one folder per
document type (27 of them); inside each, the uploaded file and — when OCR is
requested — a JSON sidecar with the same base name:

    <reference>__<name>/
        proof_of_id/
            passport.png
            passport.json          # OCR result (if ocr=true)
        bank_statement/
            statement.pdf
            statement.json
        ...
"""
import re


def slugify(value: str | None, fallback: str = "unknown") -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", (value or "").strip()).strip("_")
    return s or fallback


def sanitize_filename(name: str | None) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "file").strip()).strip("._")
    return base or "file"


def case_folder(reference: str, client_name: str | None) -> str:
    return f"{slugify(reference)}__{slugify(client_name)}"


def doc_key(reference: str, client_name: str | None, doc_type: str, file_name: str) -> str:
    return f"{case_folder(reference, client_name)}/{doc_type}/{sanitize_filename(file_name)}"


def json_key(reference: str, client_name: str | None, doc_type: str, file_name: str) -> str:
    fn = sanitize_filename(file_name)
    stem = fn.rsplit(".", 1)[0] if "." in fn else fn
    return f"{case_folder(reference, client_name)}/{doc_type}/{stem}.json"
