"""Chat sessions: stored conversations in Supabase (or the offline stub).

Each user owns chat_sessions rows plus their chat_messages; the
assistant page lists sessions in a navigator so any conversation can be
revisited and continued with its context intact. Works through the same
data-client API as everything else, so the offline stub covers tests.
"""

from __future__ import annotations

from datetime import datetime, timezone


def list_sessions(client) -> list[dict]:
    """This user's chat sessions, newest first."""
    rows = client.rows("chat_sessions")
    rows.sort(key=lambda r: int(r.get("id") or 0), reverse=True)
    return rows


def get_session(client, session_id: int) -> dict | None:
    if not session_id:
        return None
    return client.get("chat_sessions", int(session_id))


def make_title(text: str) -> str:
    """Short navigator title from the first question."""
    clean = " ".join((text or "").split())
    if len(clean) <= 42:
        return clean or "New chat"
    return clean[:42].rstrip() + "…"


def create_session(client, title: str = "") -> dict:
    return client.add("chat_sessions", {
        "title": (title or "").strip()[:80] or "New chat",
        "created_at": datetime.now(timezone.utc).isoformat()})


def ensure_session(client, session_id: int | None,
                   first_question: str = "") -> dict:
    """Existing session, or a new one titled from the first question."""
    current = get_session(client, session_id) if session_id else None
    if current is not None:
        return current
    return create_session(client, make_title(first_question))


def add_message(client, session_id: int, role: str, content: str,
                engine: str = "") -> dict:
    """Append one message; assistant rows carry their engine label."""
    return client.add("chat_messages", {
        "session_id": int(session_id), "role": role,
        "content": content or "", "engine": engine or "",
        "created_at": datetime.now(timezone.utc).isoformat()})


def messages_for(client, session_id: int) -> list[dict]:
    """One session's messages, oldest first."""
    rows = client.find("chat_messages", session_id=int(session_id))
    rows.sort(key=lambda r: int(r.get("id") or 0))
    return rows


def history_pairs(messages: list[dict]) -> list[dict]:
    """Pair rows into [{q, a, engine}] for the template.

    Follow-up assistant rows (proposal, then Confirm result) attach to
    the latest question so nothing is dropped.
    """
    pairs: list[dict] = []
    last_q = ""
    for m in messages or []:
        if m.get("role") == "user":
            last_q = m.get("content", "")
            pairs.append({"q": last_q, "a": "", "engine": ""})
        else:
            item = {"q": last_q, "a": m.get("content", ""),
                    "engine": m.get("engine", "") or "rules"}
            if pairs and not pairs[-1]["a"]:
                pairs[-1] = item
            else:
                pairs.append(item)
    return [p for p in pairs if p["a"] or p["q"]]


def recent_history(client, session_id: int, limit: int = 6) -> list[dict]:
    """Last messages as [{role, content}] for the LLM prompt."""
    return [{"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in messages_for(client, session_id)[-limit:]]
