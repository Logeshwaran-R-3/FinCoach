"""Ingest pipeline: extract -> normalize -> validate -> dedupe -> insert."""

from __future__ import annotations

from datetime import datetime, timezone

from fincoach.storage import sb_data, source_repo
from fincoach.ingest.dedupe import dedupe
from fincoach.ingest.errors import IngestError
from fincoach.ingest.extract_csv import extract_csv
from fincoach.ingest.extract_docx import extract_docx
from fincoach.ingest.extract_image import extract_image
from fincoach.ingest.extract_pdf import extract_pdf
from fincoach.ingest.extract_text import extract_text
from fincoach.ingest.extract_xlsx import extract_xlsx
from fincoach.ingest.normalize import normalize
from fincoach.ingest.validate import validate

EXTRACTORS = {
    "csv": extract_csv,
    "xlsx": extract_xlsx,
    "pdf": extract_pdf,
    "docx": extract_docx,
    "image": extract_image,
    "sms_text": extract_text,
}


def _client(uid: str, access_token: str | None = None):
    try:
        from flask import current_app, has_app_context

        if has_app_context() and current_app:
            configured = current_app.config.get("DATA_CLIENT")
            if configured is not None:
                if callable(configured):
                    return configured(uid, access_token)
                return configured
    except ImportError:
        pass
    return sb_data.get_client(uid, access_token)


def _set_status(uid: str, source_id: int, status: str, error="",
                txn_count=0, rejected=0, access_token=None):
    source_repo.set_status(uid, source_id, status, error=error,
                           txn_count=txn_count, rejected=rejected,
                           access_token=access_token)


def _friendly_error(kind: str, filename: str, exc: Exception) -> str:
    """Map library/format crashes to a one-line user-facing message."""
    name = type(exc).__name__
    msg = str(exc)
    fname = filename or f"upload.{kind}"
    if "Tesseract" in name or "tesseract" in msg.lower():
        return ("OCR needs the Tesseract program installed (the "
                "'pytesseract' pip package alone is not enough); "
                f"could not read {fname!r}.")
    if name == "BadZipFile":
        return (f"{fname!r} is not a valid .{kind} file "
                "(could not open it).")
    if "Pdf" in name or "pypdf" in type(exc).__module__:
        return f"{fname!r} is not a readable PDF ({msg})."
    if name == "UnidentifiedImageError":
        return f"{fname!r} is not a readable image (PNG/JPG expected)."
    short = msg.strip().splitlines()[0] if msg.strip() else name
    return f"could not extract {fname!r} ({kind}): {short[:200]}"


def run_ingest(uid: str, source_id: int,
               access_token: str | None = None) -> dict:
    src = source_repo.get(uid, source_id, access_token=access_token)
    extractor = EXTRACTORS.get(src.kind)
    if extractor is None:
        raise IngestError(f"no extractor for kind {src.kind!r}")
    _set_status(uid, source_id, "processing", access_token=access_token)
    try:
        raw = extractor(
            source_repo.read_bytes(uid, src, access_token=access_token),
            src.filename)
    except IngestError as exc:
        _set_status(uid, source_id, "failed", error=str(exc),
                    access_token=access_token)
        raise
    except Exception as exc:
        message = _friendly_error(src.kind, src.filename, exc)
        _set_status(uid, source_id, "failed", error=message,
                    access_token=access_token)
        raise IngestError(message) from exc
    normalized = []
    rejected = []
    for r in raw:
        try:
            normalized.append(normalize(r))
        except (ValueError, KeyError) as exc:
            rejected.append({"txn": r, "reason": str(exc)})
    valid, rej = validate(normalized)
    rejected.extend(rej)
    client = _client(uid, access_token)
    existing = client.rows("transactions")
    unique, dupes = dedupe(valid, existing)
    created = datetime.now(timezone.utc).isoformat()
    client.add_many("transactions", [{
        "source_id": source_id,
        "date": txn["date"],
        "description": txn["description"],
        "merchant": txn.get("merchant", ""),
        "amount": txn["amount"],
        "currency": txn.get("currency", "INR"),
        "type": txn.get("type", "other"),
        "category": txn.get("category", ""),
        "origin": txn.get("origin", "verified"),
        "note": txn.get("note", ""),
        "hash": txn.get("hash", ""),
        "created_at": created,
    } for txn in unique])
    _set_status(uid, source_id, "done", txn_count=len(unique),
                rejected=len(rejected) + len(dupes),
                access_token=access_token)
    return {
        "inserted": len(unique),
        "rejected": rejected,
        "duplicates": len(dupes),
    }
