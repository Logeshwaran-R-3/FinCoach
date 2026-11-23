"""Savings goals: create, contribute, and honest progress math."""

from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime, timedelta, timezone


def create(conn: sqlite3.Connection, name: str, target: float,
           deadline: str = "", current: float = 0.0) -> int:
    """Insert a goal row. Returns goal id."""
    if target <= 0:
        raise ValueError("target must be positive")
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO goals (name, target, current, deadline, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (name, float(target), float(current), deadline, now),
    )
    conn.commit()
    return int(cur.lastrowid)


def record_contribution(conn: sqlite3.Connection, goal_id: int, amount: float,
                        contrib_date: str = "", note: str = "") -> float:
    """Add a contribution and bump the goal's current total. Returns new saved."""
    if amount <= 0:
        raise ValueError("contribution must be positive")
    day = contrib_date or date.today().isoformat()
    conn.execute(
        "INSERT INTO goal_contribs (goal_id, amount, date, note)"
        " VALUES (?, ?, ?, ?)",
        (goal_id, float(amount), day, note),
    )
    conn.execute("UPDATE goals SET current = current + ? WHERE id = ?",
                 (float(amount), goal_id))
    conn.commit()
    row = conn.execute("SELECT current FROM goals WHERE id = ?",
                       (goal_id,)).fetchone()
    return float(row["current"]) if row else 0.0


def get(conn: sqlite3.Connection, goal_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    return dict(row) if row else None


def create_for(client, name: str, target: float,
               deadline: str = "", current: float = 0.0) -> int:
    """Data-client version of create (Supabase or stub). Returns goal id."""
    if target <= 0:
        raise ValueError("target must be positive")
    now = datetime.now(timezone.utc).isoformat()
    row = client.add("goals", {
        "name": name, "target": float(target), "current": float(current),
        "deadline": deadline, "created_at": now})
    return int(row["id"])


def record_contribution_for(client, goal_id: int, amount: float,
                            contrib_date: str = "", note: str = "") -> float:
    """Data-client version of record_contribution. Returns new saved."""
    if amount <= 0:
        raise ValueError("contribution must be positive")
    day = contrib_date or date.today().isoformat()
    client.add("goal_contribs", {
        "goal_id": goal_id, "amount": float(amount), "date": day, "note": note})
    goal = client.get("goals", goal_id) or {}
    saved = float(goal.get("current", 0)) + float(amount)
    client.set("goals", goal_id, {"current": saved})
    return saved


def get_for(client, goal_id: int) -> dict | None:
    return client.get("goals", goal_id)


def _months_between(start: date, end: date) -> int:
    return max((end.year - start.year) * 12 + (end.month - start.month), 0)


def goal_progress(goal: dict, monthly_savings: float = 0.0,
                  today: str | None = None) -> dict:
    """Return saved/remaining/months_left/days_to_goal/required_monthly/
    on_track/projected.

    The projection is day-granular (daily pace = monthly savings / 30),
    so even a small income bump visibly shortens the timeline. Only
    contributions raise 'saved'; income only quickens the pace.
    Honest edge handling: past-due goals report overdue; zero savings with a
    remaining balance reports no projected date instead of a fake one.
    """
    now = date.fromisoformat(today) if today else date.today()
    saved = float(goal.get("current", 0))
    target = float(goal.get("target", 0))
    remaining = max(target - saved, 0.0)
    deadline_raw = str(goal.get("deadline") or "")
    assumptions: list[str] = []

    months_left: int | None = None
    overdue = False
    if deadline_raw:
        deadline = date.fromisoformat(deadline_raw[:10])
        months_left = _months_between(now, deadline)
        overdue = deadline < now and remaining > 0
        if overdue:
            assumptions.append(f"deadline {deadline_raw} has passed with "
                               f"Rs {remaining:,.0f} still remaining")
    else:
        assumptions.append("no deadline set; projection uses savings pace only")

    if monthly_savings <= 0:
        assumptions.append("monthly savings is zero or negative; no pace projection")
    else:
        daily = monthly_savings / 30
        assumptions.append(
            f"assumes steady savings of Rs {monthly_savings:,.0f}/month"
            f" (~Rs {daily:,.0f}/day)")

    if remaining <= 0:
        return {"saved": saved, "remaining": 0.0, "months_left": months_left,
                "days_to_goal": 0, "required_monthly": 0.0, "on_track": True,
                "projected_date": now.isoformat(), "overdue": False,
                "assumptions": assumptions + ["goal already reached"]}

    required_monthly = (remaining / months_left) if months_left else remaining
    if monthly_savings > 0:
        need_days = max(math.ceil(remaining / (monthly_savings / 30)), 1)
        projected = (now + timedelta(days=need_days)).isoformat()
        days_to_goal: int | None = need_days
    else:
        projected = None
        days_to_goal = None

    if overdue:
        on_track = False
    elif months_left is not None and months_left > 0:
        on_track = monthly_savings >= required_monthly
    else:
        on_track = monthly_savings > 0

    return {"saved": round(saved, 2), "remaining": round(remaining, 2),
            "months_left": months_left, "days_to_goal": days_to_goal,
            "required_monthly": round(required_monthly, 2),
            "on_track": on_track, "projected_date": projected,
            "overdue": overdue, "assumptions": assumptions}
