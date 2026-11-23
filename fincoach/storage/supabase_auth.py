"""Supabase Auth (GoTrue) login for FinCoach. Stdlib-only HTTP, no new deps.

Accounts and financial data live in the user's Supabase project (Auth plus
Postgres/Storage, RLS-scoped per user). The Flask session holds Supabase
tokens; each request validates the access token (refreshing once when it
expired).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from fincoach.config import SUPABASE_ANON_KEY, SUPABASE_URL

_TIMEOUT = 10
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LEN = 8

CONFIG_HELP = ("Supabase login is not configured. "
               "Set SUPABASE_URL and SUPABASE_ANON_KEY.")


class AuthError(ValueError):
    """User-facing auth failure (message safe to flash)."""


class Unauthorized(AuthError):
    """Bad/expired credentials or token."""


class ConfigError(AuthError):
    """Supabase credentials are missing."""


def validate_new_account(email: str, password: str) -> str:
    """Normalize + validate signup input. Returns the normalized email."""
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address.")
    if len(password or "") < MIN_PASSWORD_LEN:
        raise AuthError("Password needs at least 8 characters.")
    return email


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def display_name(user: dict) -> str:
    """Best display name for a GoTrue user: user_metadata name, else ''."""
    meta = (user or {}).get("user_metadata") or {}
    return str(meta.get("name") or (user or {}).get("name") or "").strip()


def user_details(user: dict) -> dict:
    """Profile-page details from a GoTrue user object.

    Tolerates missing fields (test stubs return a thin user); unknown
    values come back as "" and the template renders a dash.
    """
    user = user or {}
    meta = user.get("user_metadata") or {}

    def stamp(value) -> str:
        text = str(value or "").strip().replace("T", " ")[:16]
        return text if text else ""

    return {
        "id": str(user.get("id") or ""),
        "email": str(user.get("email") or ""),
        "name": display_name(user),
        "phone": str(meta.get("phone") or "").strip(),
        "currency": str(meta.get("currency") or "").strip() or "INR",
        "created": stamp(user.get("created_at")),
        "confirmed": stamp(user.get("email_confirmed_at")
                           or user.get("confirmed_at")),
        "last_sign_in": stamp(user.get("last_sign_in_at")),
    }


class SupabaseAuthClient:
    """Thin GoTrue REST client. Raises AuthError/Unauthorized/ConfigError."""

    def __init__(self, base_url: str, anon_key: str):
        if not base_url or not anon_key:
            raise ConfigError(CONFIG_HELP)
        self.base = base_url.rstrip("/")
        self.key = anon_key

    def _request(self, method: str, path: str, body: dict | None = None,
                 token: str | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("apikey", self.key)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8", "replace") or "{}")
            except ValueError:
                payload = {}
            msg = str(payload.get("msg") or payload.get("error_description")
                      or payload.get("error") or f"Login failed (HTTP {exc.code}).")
            if exc.code in (401, 403):
                raise Unauthorized(msg)
            raise AuthError(msg)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AuthError(f"Could not reach the login service ({exc}). "
                            "Check SUPABASE_URL and your connection.")

    def sign_up(self, email: str, password: str, name: str = "",
                phone: str = "", currency: str = "") -> dict:
        """Register. Returns {"user", "session"}; session is None when the
        project requires email confirmation.

        Name/phone/currency are stored in the Supabase user's user_metadata
        (GoTrue `data` field) so the profile menu can greet by name.
        """
        email = validate_new_account(email, password)
        metadata = {k: v for k, v in
                    (("name", (name or "").strip()),
                     ("phone", (phone or "").strip()),
                     ("currency", (currency or "").strip() or "INR"))
                    if v}
        body: dict = {"email": email, "password": password}
        if metadata:
            body["data"] = metadata
        try:
            data = self._request("POST", "/auth/v1/signup", body)
        except AuthError as exc:
            if "already registered" in str(exc).lower():
                raise AuthError("That email is already registered. Try logging in.")
            raise
        return {"user": data.get("user") or {}, "session": data.get("session")}

    def sign_in(self, email: str, password: str) -> dict:
        """Log in. Returns {"user", "session"}. Raises Unauthorized on bad
        credentials, AuthError('Email not confirmed') when pending."""
        email = normalize_email(email)
        try:
            data = self._request("POST", "/auth/v1/token?grant_type=password",
                                 {"email": email, "password": password or ""})
        except AuthError as exc:
            # GoTrue reports bad passwords as HTTP 400; normalize so the
            # route maps it to "Wrong email or password." like the stub.
            if "invalid login credentials" in str(exc).lower():
                raise Unauthorized(str(exc))
            raise
        return {"user": data.get("user") or {"email": email}, "session": data}

    def update_user(self, access_token: str, name: str = "",
                    phone: str = "", currency: str = "") -> dict:
        """Update the logged-in user's metadata (name/phone/currency).

        Returns the updated GoTrue user object.
        """
        metadata = {k: v for k, v in
                    (("name", (name or "").strip()),
                     ("phone", (phone or "").strip()),
                     ("currency", (currency or "").strip() or "INR"))
                    if v}
        data = self._request("PUT", "/auth/v1/user", {"data": metadata},
                             token=access_token)
        return data.get("user") or data

    def get_user(self, access_token: str) -> dict:
        """Validate a token, returning the user. Raises Unauthorized."""
        return self._request("GET", "/auth/v1/user", token=access_token)

    def refresh_session(self, refresh_token: str) -> dict:
        """Mint fresh tokens. Returns {"user", "session"}."""
        data = self._request("POST", "/auth/v1/token?grant_type=refresh_token",
                             {"refresh_token": refresh_token})
        return {"user": data.get("user") or {}, "session": data}

    def sign_out(self, access_token: str) -> None:
        """Revoke a token. Best-effort: the local session clears regardless."""
        try:
            self._request("POST", "/auth/v1/logout", token=access_token)
        except AuthError:
            pass


def client_from_env() -> SupabaseAuthClient:
    return SupabaseAuthClient(SUPABASE_URL, SUPABASE_ANON_KEY)
