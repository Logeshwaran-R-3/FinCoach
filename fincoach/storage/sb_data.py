"""Supabase Postgres + Storage data layer (stdlib-only HTTP).

Production path: PostgREST rows plus Storage file bytes, every request
carrying the anon key and the logged-in user's access token so Row Level
Security keeps each account isolated.

Offline path: StubDataClient, a SQLite-backed stand-in with the same
method API. It is selected ONLY when the FINCOACH_DATA env var is set
(which the test suite does) or when a test injects
app.config["DATA_CLIENT"]. It is never a production backend: a real
deployment leaves FINCOACH_DATA unset and talks to Supabase.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from fincoach.storage import db
from fincoach.config import (
    SUPABASE_ANON_KEY,
    SUPABASE_URL,
    user_db_path,
    user_sources_dir,
)

_TIMEOUT = 15
_STORAGE_BUCKET = "sources"

TABLES = (
    "sources",
    "transactions",
    "category_rules",
    "goals",
    "goal_contribs",
    "profiles",
    "chat_sessions",
    "chat_messages",
    "reminders",
)


class SupabaseDataError(ValueError):
    """User-safe data-layer failure (transport, RLS, or missing row)."""


class SupabaseConfigError(SupabaseDataError):
    """Supabase credentials or session are missing."""


CONFIG_HELP = ("Supabase data is not configured. "
               "Set SUPABASE_URL and SUPABASE_ANON_KEY.")


# ------------------------------------------------------------------ REST
class SupabaseRestClient:
    """Thin PostgREST + Storage client for one user. Stdlib-only HTTP."""

    def __init__(self, base_url: str, anon_key: str, token: str, uid: str):
        if not base_url or not anon_key:
            raise SupabaseConfigError(CONFIG_HELP)
        if not token:
            raise SupabaseConfigError("Not logged in.")
        self.base = base_url.rstrip("/")
        self.key = anon_key
        self.token = token
        self.uid = uid

    # -- low level ---------------------------------------------------
    def _request(self, method: str, path: str, query: str = "",
                 body: bytes | None = None,
                 content_type: str = "application/json",
                 prefer: str = "") -> tuple[int, bytes]:
        url = self.base + path + (("?" + query) if query else "")
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("apikey", self.key)
        req.add_header("Authorization", f"Bearer {self.token}")
        if content_type:
            req.add_header("Content-Type", content_type)
        if prefer:
            req.add_header("Prefer", prefer)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(
                    exc.read().decode("utf-8", "replace") or "{}")
            except ValueError:
                payload = {}
            finally:
                exc.close()
            msg = str(payload.get("message") or payload.get("error")
                      or f"Supabase request failed (HTTP {exc.code}).")
            raise SupabaseDataError(msg)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SupabaseDataError(
                f"Could not reach Supabase ({exc}). Check SUPABASE_URL "
                "and your connection.")

    @staticmethod
    def _q(params: list[tuple[str, str]]) -> str:
        return "&".join(f"{k}={urllib.parse.quote(v, safe=',.')}"
                        for k, v in params)

    def _scope(self, extra: list[tuple[str, str]] | None = None):
        params = [("user_id", f"eq.{self.uid}")]
        if extra:
            params.extend(extra)
        return params

    @staticmethod
    def _rows(raw: bytes) -> list[dict]:
        text = raw.decode("utf-8", "replace").strip()
        if not text:
            return []
        data = json.loads(text)
        return data if isinstance(data, list) else [data]

    # -- table API ---------------------------------------------------
    def rows(self, table: str) -> list[dict]:
        """All of this user's rows, oldest first."""
        query = self._q(self._scope([("select", "*"), ("order", "id.asc")]))
        _, raw = self._request("GET", f"/rest/v1/{table}", query)
        return self._rows(raw)

    def find(self, table: str, **filters) -> list[dict]:
        """Rows matching exact column=value filters, oldest first."""
        extra = [("select", "*"), ("order", "id.asc")]
        extra += [(k, f"eq.{v}") for k, v in filters.items()]
        query = self._q(self._scope(extra))
        _, raw = self._request("GET", f"/rest/v1/{table}", query)
        return self._rows(raw)

    def get(self, table: str, row_id: int) -> dict | None:
        rows = self.find(table, id=row_id)
        return rows[0] if rows else None

    def add(self, table: str, row: dict) -> dict:
        payload = dict(row)
        payload["user_id"] = self.uid
        body = json.dumps(payload).encode("utf-8")
        _, raw = self._request("POST", f"/rest/v1/{table}", body=body,
                               prefer="return=representation")
        rows = self._rows(raw)
        if not rows:
            raise SupabaseDataError(f"Insert into {table} returned no row.")
        return rows[0]

    def add_many(self, table: str, rows_in: list[dict]) -> list[dict]:
        if not rows_in:
            return []
        payload = [dict(r, user_id=self.uid) for r in rows_in]
        body = json.dumps(payload).encode("utf-8")
        _, raw = self._request("POST", f"/rest/v1/{table}", body=body,
                               prefer="return=representation")
        return self._rows(raw)

    def set(self, table: str, row_id: int, patch: dict) -> dict:
        query = self._q(self._scope([("id", f"eq.{row_id}")]))
        body = json.dumps(dict(patch)).encode("utf-8")
        _, raw = self._request("PATCH", f"/rest/v1/{table}", query, body,
                               prefer="return=representation")
        rows = self._rows(raw)
        if not rows:
            raise SupabaseDataError(f"No {table} row {row_id} for this user.")
        return rows[0]

    def remove(self, table: str, row_id: int) -> None:
        query = self._q(self._scope([("id", f"eq.{row_id}")]))
        self._request("DELETE", f"/rest/v1/{table}", query)

    def count(self, table: str) -> int:
        query = self._q(self._scope([("select", "id")]))
        _, raw = self._request("GET", f"/rest/v1/{table}", query)
        return len(self._rows(raw))

    def table_counts(self) -> dict:
        return {t: self.count(t) for t in TABLES}

    # -- Storage API -------------------------------------------------
    def _object_path(self, path: str) -> str:
        return "/storage/v1/object/{}/{}".format(
            _STORAGE_BUCKET, urllib.parse.quote(path))

    def put_file(self, path: str, data: bytes, mime: str = "") -> str:
        req_path = self._object_path(path)
        url = self.base + req_path
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("apikey", self.key)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", mime or "application/octet-stream")
        req.add_header("x-upsert", "true")
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT):
                pass
        except urllib.error.HTTPError as exc:
            raise SupabaseDataError(
                f"File upload failed (HTTP {exc.code}).")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SupabaseDataError(f"Could not reach Supabase ({exc}).")
        return path

    def get_file(self, path: str) -> bytes:
        _, raw = self._request("GET", self._object_path(path))
        return raw

    def del_file(self, path: str) -> None:
        self._request("DELETE", self._object_path(path))


# ------------------------------------------------------------------ stub
class StubDataClient:
    """Offline SQLite-backed stand-in with the Supabase client API.

    Used ONLY when FINCOACH_DATA is set (tests) or when injected via
    app.config["DATA_CLIENT"]. Each uid gets its own SQLite file and
    upload directory, mirroring Supabase per-user isolation.
    """

    def __init__(self, uid: str):
        self.uid = uid
        self._db_path = user_db_path(uid)
        self._files_dir = user_sources_dir(uid)

    def _conn(self):
        conn = db.migrate(self._db_path)
        cols = {r[1] for r in
                conn.execute("PRAGMA table_info(category_rules)")}
        if "hits" not in cols:
            conn.execute("ALTER TABLE category_rules "
                         "ADD COLUMN hits INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        return conn

    @staticmethod
    def _cols(conn, table: str) -> set[str]:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}

    def rows(self, table: str) -> list[dict]:
        conn = self._conn()
        try:
            if table not in self._tables(conn):
                return []
            return [dict(r) for r in conn.execute(
                f"SELECT * FROM {table} ORDER BY id")]
        finally:
            conn.close()

    def find(self, table: str, **filters) -> list[dict]:
        conn = self._conn()
        try:
            if table not in self._tables(conn):
                return []
            cols = self._cols(conn, table)
            usable = {k: v for k, v in filters.items() if k in cols}
            sql = f"SELECT * FROM {table}"
            params: list = []
            if usable:
                sql += " WHERE " + " AND ".join(f"{k} = ?" for k in usable)
                params = list(usable.values())
            sql += " ORDER BY id"
            return [dict(r) for r in conn.execute(sql, params)]
        finally:
            conn.close()

    def get(self, table: str, row_id: int) -> dict | None:
        rows = self.find(table, id=row_id)
        return rows[0] if rows else None

    def add(self, table: str, row: dict) -> dict:
        conn = self._conn()
        try:
            cols = self._cols(conn, table)
            usable = {k: v for k, v in row.items()
                      if k in cols and k != "id"}
            names = ", ".join(usable)
            marks = ", ".join("?" for _ in usable)
            cur = conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})",
                               tuple(usable.values()))
            conn.commit()
            new_id = cur.lastrowid
            got = conn.execute(f"SELECT * FROM {table} WHERE id = ?",
                               (new_id,)).fetchone()
        finally:
            conn.close()
        return dict(got)

    def add_many(self, table: str, rows_in: list[dict]) -> list[dict]:
        return [self.add(table, r) for r in rows_in]

    def set(self, table: str, row_id: int, patch: dict) -> dict:
        conn = self._conn()
        try:
            cols = self._cols(conn, table)
            usable = {k: v for k, v in patch.items()
                      if k in cols and k != "id"}
            if usable:
                assigns = ", ".join(f"{k} = ?" for k in usable)
                conn.execute(f"UPDATE {table} SET {assigns} WHERE id = ?",
                             (*usable.values(), row_id))
                conn.commit()
            got = conn.execute(f"SELECT * FROM {table} WHERE id = ?",
                               (row_id,)).fetchone()
        finally:
            conn.close()
        if got is None:
            raise SupabaseDataError(f"No {table} row {row_id} for this user.")
        return dict(got)

    def remove(self, table: str, row_id: int) -> None:
        conn = self._conn()
        try:
            if table not in self._tables(conn):
                return
            conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
            conn.commit()
        finally:
            conn.close()

    def count(self, table: str) -> int:
        conn = self._conn()
        try:
            if table not in self._tables(conn):
                return 0
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            conn.close()

    def table_counts(self) -> dict:
        conn = self._conn()
        try:
            return db.table_counts(conn)
        finally:
            conn.close()

    @staticmethod
    def _tables(conn) -> set[str]:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}

    # -- file bytes (local stand-in for the Storage bucket) ----------
    def put_file(self, path: str, data: bytes, mime: str = "") -> str:
        self._files_dir.mkdir(parents=True, exist_ok=True)
        safe = path.replace("/", "_").replace("\\", "_")
        (self._files_dir / safe).write_bytes(data)
        return path

    def get_file(self, path: str) -> bytes:
        safe = path.replace("/", "_").replace("\\", "_")
        return (self._files_dir / safe).read_bytes()

    def del_file(self, path: str) -> None:
        safe = path.replace("/", "_").replace("\\", "_")
        target = self._files_dir / safe
        if target.exists():
            target.unlink()


# ------------------------------------------------------------ dispatcher
def using_stub() -> bool:
    """True only in offline/test mode (FINCOACH_DATA is set)."""
    return bool(os.environ.get("FINCOACH_DATA"))


def get_client(uid: str, access_token: str | None = None):
    """Data client for one user: stub offline, Supabase in production.

    Tests set FINCOACH_DATA (or inject app.config["DATA_CLIENT"]) and get
    the SQLite stub; production leaves it unset and gets PostgREST with
    the user's access token. CLI admin work may use SUPABASE_SERVICE_KEY.
    """
    if using_stub():
        return StubDataClient(uid)
    token = access_token or os.environ.get("SUPABASE_SERVICE_KEY", "")
    key = SUPABASE_ANON_KEY
    if not access_token and os.environ.get("SUPABASE_SERVICE_KEY"):
        key = os.environ["SUPABASE_SERVICE_KEY"]
    return SupabaseRestClient(SUPABASE_URL, key, token, uid)
