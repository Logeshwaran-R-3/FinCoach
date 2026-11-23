"""SQLite schema and migration.

SQLite is the offline stub backend only (tests set FINCOACH_DATA; see
fincoach.storage.sb_data). Production data lives in Supabase Postgres.
The conn-based helpers below stay for the stub and for direct tests.
"""

import sqlite3
from pathlib import Path

from fincoach.config import DB_PATH

TABLES = [
    "users",
    "sources",
    "transactions",
    "category_rules",
    "recurring",
    "goals",
    "goal_contribs",
    "profiles",
    "nudges",
    "reviews",
    "reminders",
    "chat_sessions",
    "chat_messages",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT NOT NULL DEFAULT '',
    txn_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    merchant TEXT NOT NULL DEFAULT '',
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    type TEXT NOT NULL DEFAULT 'other',
    category TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'verified'
        CHECK (origin IN ('verified', 'estimated', 'inferred', 'projected')),
    note TEXT NOT NULL DEFAULT '',
    hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS category_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,
    category TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    hits INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS recurring (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant TEXT NOT NULL,
    amount REAL NOT NULL,
    frequency TEXT NOT NULL DEFAULT 'monthly',
    next_date TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    target REAL NOT NULL,
    current REAL NOT NULL DEFAULT 0,
    deadline TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS goal_contribs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    amount REAL NOT NULL,
    date TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS nudges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER REFERENCES profiles(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT '',
    read INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL DEFAULT '',
    due_date TEXT NOT NULL DEFAULT '',
    amount REAL NOT NULL DEFAULT 0,
    lead TEXT NOT NULL DEFAULT '1day',
    kind TEXT NOT NULL DEFAULT 'manual',
    email TEXT NOT NULL DEFAULT '',
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'user',
    content TEXT NOT NULL DEFAULT '',
    engine TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);
"""


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = connect(db_path)
    conn.executescript(_SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chat_messages)")}
    if "engine" not in cols:
        conn.execute("ALTER TABLE chat_messages "
                     "ADD COLUMN engine TEXT NOT NULL DEFAULT ''")
    conn.commit()
    return conn


def table_counts(conn: sqlite3.Connection) -> dict:
    return {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in TABLES
    }
