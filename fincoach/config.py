"""Application configuration: data paths and safety disclaimer."""

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from a .env file into os.environ (stdlib only).

    Existing environment variables win; a missing file is silently ignored.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(_PROJECT_ROOT / ".env")

# FINCOACH_DATA is test-only: when set, the offline SQLite stub transport
# is used under that directory instead of Supabase. Production leaves it
# unset and talks to Supabase Postgres + Storage.
DATA_DIR = Path(os.environ.get("FINCOACH_DATA", "./data"))
DB_PATH = DATA_DIR / "fincoach.db"
SOURCES_DIR = DATA_DIR / "sources"
USERS_DIR = DATA_DIR / "users"


def safe_uid(uid: str) -> str:
    """Sanitize a user id for use as a directory name.

    Supabase ids are UUIDs (already safe); this also neutralizes emails
    and path-traversal attempts by keeping only [A-Za-z0-9_-].
    """
    cleaned = "".join(c if c.isalnum() or c in "-_" else "_"
                      for c in str(uid or "").strip())
    return cleaned[:64] or "anon"


def user_db_path(uid: str) -> Path:
    """SQLite file for one user; each user starts from an empty DB."""
    return USERS_DIR / safe_uid(uid) / "fincoach.db"


def user_sources_dir(uid: str) -> Path:
    """Uploaded-file directory for one user."""
    return USERS_DIR / safe_uid(uid) / "sources"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

HELP_EMAIL = os.environ.get("HELP_EMAIL", "support@fincoach.local")

# Outgoing mail for bill reminders (stdlib smtplib; see fincoach/services/mailer.py).
# Empty SMTP_HOST means email is not configured: the app shows a setup hint
# instead of sending. See README "Bill reminders by email" for Gmail
# app-password setup.
SMTP_HOST = os.environ.get("SMTP_HOST", "")
try:
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or 587)
except ValueError:
    SMTP_PORT = 587
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "")

# Google Gemini for the assistant (optional; see fincoach/assistant/llm.py). Read
# dynamically so tests can toggle the key via os.environ. Without a key
# the assistant uses its built-in deterministic rule engine.
GEMINI_MODEL_DEFAULT = "gemini-1.5-flash"


def gemini_api_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip()


def gemini_model() -> str:
    return os.environ.get("GEMINI_MODEL", "").strip() or GEMINI_MODEL_DEFAULT


SAFETY_DISCLAIMER = (
    "FinCoach is for educational purposes only. It provides general financial "
    "literacy information and does not provide securities advice or buy/sell "
    "recommendations. Consult a qualified professional before making decisions."
)
