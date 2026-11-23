"""Source file repository: metadata rows plus original file bytes.

Every function takes the owning user's id. In production, rows live in
Supabase Postgres and bytes in the Supabase Storage bucket "sources";
when FINCOACH_DATA is set (tests only), an offline SQLite stub with the
same API is used instead. See fincoach.storage.sb_data.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from fincoach.storage import sb_data
from fincoach.models import Source

KINDS = ("pdf", "docx", "csv", "xlsx", "image", "sms_text", "manual")

STATUSES = ("pending", "processing", "done", "failed", "archived")


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


def _row_to_source(row: dict) -> Source:
    known = {f for f in Source.__dataclass_fields__}
    return Source(**{k: v for k, v in row.items() if k in known})


def _storage_path(uid: str, sha256: str, filename: str) -> str:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)
    return f"{uid}/{sha256}_{safe}"


def add(uid: str, kind: str, filename: str, data: bytes, mime: str = "",
        access_token: str | None = None) -> Source:
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {KINDS}")
    digest = hashlib.sha256(data).hexdigest()
    client = _client(uid, access_token)
    path = _storage_path(uid, digest, filename)
    client.put_file(path, data, mime)
    created = datetime.now(timezone.utc).isoformat()
    row = client.add("sources", {
        "kind": kind, "filename": filename, "mime": mime, "size": len(data),
        "sha256": digest, "created_at": created, "status": "pending",
        "storage_path": path,
    })
    return _row_to_source(row)


def get(uid: str, source_id: int, access_token: str | None = None) -> Source:
    row = _client(uid, access_token).get("sources", source_id)
    if row is None:
        raise KeyError(f"no source with id {source_id}")
    return _row_to_source(row)


def list(uid: str, access_token: str | None = None) -> list:  # noqa: A001
    rows = _client(uid, access_token).rows("sources")
    return [_row_to_source(r) for r in rows]


def delete(uid: str, source_id: int, access_token: str | None = None) -> int:
    """Delete a source row and its stored file; keep extracted transactions.

    The source disappears from the database, but already-extracted
    transactions stay in the ledger with their source link cleared
    (source_id set to null), so figures never silently change. Returns the
    number of transactions unlinked.
    """
    client = _client(uid, access_token)
    row = client.get("sources", source_id)
    if row is None:
        raise KeyError(f"no source with id {source_id}")
    path = row.get("storage_path") or _storage_path(
        uid, row.get("sha256", ""), row.get("filename", ""))
    try:
        client.del_file(path)
    except (OSError, sb_data.SupabaseDataError):
        pass
    unlinked = 0
    for txn in client.rows("transactions"):
        if txn.get("source_id") == source_id:
            client.set("transactions", txn["id"], {"source_id": None})
            unlinked += 1
    client.remove("sources", source_id)
    return unlinked


def set_status(uid: str, source_id: int, status: str, error: str = "",
               txn_count: int = 0, rejected: int = 0,
               access_token: str | None = None) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    _client(uid, access_token).set("sources", source_id, {
        "status": status, "error": error, "txn_count": txn_count,
        "rejected_count": rejected,
    })


def archive(uid: str, source_id: int,
            access_token: str | None = None) -> Source:
    """Mark a source archived (hidden from ingest queues, bytes kept).

    Archived sources' transactions are excluded from every page's
    calculations until the source is restored.
    """
    client = _client(uid, access_token)
    row = client.get("sources", source_id)
    if row is None:
        raise KeyError(f"no source with id {source_id}")
    updated = client.set("sources", source_id, {"status": "archived"})
    return _row_to_source(updated)


def restore(uid: str, source_id: int,
            access_token: str | None = None) -> Source:
    """Restore an archived source so its transactions count again.

    Status returns to "done" when the source extracted transactions,
    otherwise "pending" (it still needs ingesting).
    """
    client = _client(uid, access_token)
    row = client.get("sources", source_id)
    if row is None:
        raise KeyError(f"no source with id {source_id}")
    status = "done" if int(row.get("txn_count") or 0) > 0 else "pending"
    updated = client.set("sources", source_id, {"status": status})
    return _row_to_source(updated)


def archived_ids(uid: str, access_token: str | None = None) -> set:
    """Ids of this user's archived sources (excluded from calculations)."""
    return {r["id"] for r in _client(uid, access_token).rows("sources")
            if r.get("status") == "archived"}


def read_bytes(uid: str, src: Source,
               access_token: str | None = None) -> bytes:
    client = _client(uid, access_token)
    row = client.get("sources", src.id) if src.id is not None else None
    path = (row or {}).get("storage_path") or _storage_path(
        uid, src.sha256, src.filename)
    try:
        return client.get_file(path)
    except (OSError, sb_data.SupabaseDataError) as exc:
        raise KeyError(f"no stored bytes for source {src.id}") from exc
