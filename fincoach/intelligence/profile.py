"""Build and store a monthly money snapshot (profile)."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone

_MONTHS_WINDOW = 6
_MIN_MONTHS_WINDOW = 3

_CADENCE_DIVISOR = {"monthly": 1, "quarterly": 3, "yearly": 12}


def _month(iso: str) -> str:
    return str(iso)[:7]


def build_profile(transactions: list[dict], recurring: list[dict] | None = None,
                  name: str = "default") -> dict:
    """Snapshot: income avg, fixed/variable expenses, savings, top cats, trend.

    Each figure is labelled verified (from txns) or inferred (from detection).
    """
    recurring = recurring or []
    income_by_month: dict[str, float] = defaultdict(float)
    expense_by_month: dict[str, float] = defaultdict(float)
    cat_totals: dict[str, float] = defaultdict(float)
    cat_by_month: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    verified = sum(1 for t in transactions if t.get("origin", "verified") == "verified")
    total = len(transactions)

    for txn in transactions:
        amt = float(txn["amount"])
        month = _month(str(txn["date"]))
        if amt > 0:
            income_by_month[month] += amt
        elif amt < 0:
            expense_by_month[month] -= amt
            cat = txn.get("category") or "other"
            cat_totals[cat] -= amt
            cat_by_month[cat][month] -= amt

    months = sorted(set(income_by_month) | set(expense_by_month))
    window = months[-_MONTHS_WINDOW:] if months else []
    n = len(window) if window else 1
    avg_income = sum(income_by_month[m] for m in window) / n
    avg_expense = sum(expense_by_month[m] for m in window) / n

    fixed = 0.0
    for item in recurring:
        if item.get("status") == "inactive":
            continue
        if item.get("direction", "expense") == "income":
            continue
        divisor = _CADENCE_DIVISOR.get(item.get("cadence", "monthly"), 1)
        fixed += float(item.get("amount", 0)) / divisor
    fixed = min(fixed, avg_expense) if avg_expense else 0.0
    variable = max(avg_expense - fixed, 0.0)
    savings = avg_income - avg_expense
    rate = (savings / avg_income * 100) if avg_income else 0.0

    top_cats = sorted(cat_totals.items(), key=lambda kv: -kv[1])[:5]
    if len(window) >= 2:
        last, prev = window[-1], window[-2]
        last_net = income_by_month[last] - expense_by_month[last]
        prev_net = income_by_month[prev] - expense_by_month[prev]
        if last_net > prev_net * 1.05:
            trend = "improving"
        elif last_net < prev_net * 0.95:
            trend = "declining"
        else:
            trend = "stable"
    else:
        trend = "unknown"

    return {
        "name": name,
        "months": window,
        "months_used": len(window),
        "avg_monthly_income": round(avg_income, 2),
        "avg_monthly_expenses": round(avg_expense, 2),
        "fixed_expenses": round(fixed, 2),
        "variable_expenses": round(variable, 2),
        "savings": round(savings, 2),
        "savings_rate": round(rate, 1),
        "top_categories": [{"category": c, "total": round(v, 2)}
                           for c, v in top_cats],
        "spend_by_category": {
            c: round(sum(m.get(w, 0.0) for w in window) / n, 2)
            for c, m in cat_by_month.items()
        },
        "trend": trend,
        "txn_count": total,
        "verified_pct": round(verified / total * 100, 1) if total else 0.0,
        "inferred_pct": round((total - verified) / total * 100, 1) if total else 0.0,
        "origin": {
            "verified": ["avg_monthly_income", "avg_monthly_expenses",
                         "savings", "savings_rate", "top_categories", "trend"],
            "inferred": ["fixed_expenses", "variable_expenses"],
        },
    }


def save_profile(conn: sqlite3.Connection, profile: dict) -> int:
    """Upsert profile JSON into profiles by name. Returns profile id."""
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute("SELECT id FROM profiles WHERE name = ?",
                       (profile.get("name", "default"),)).fetchone()
    data = json.dumps(profile)
    if row is None:
        cur = conn.execute(
            "INSERT INTO profiles (name, data, created_at) VALUES (?, ?, ?)",
            (profile.get("name", "default"), data, now),
        )
        conn.commit()
        return int(cur.lastrowid)
    conn.execute("UPDATE profiles SET data = ? WHERE id = ?", (data, row["id"]))
    conn.commit()
    return int(row["id"])


def load_profile(conn: sqlite3.Connection, name: str = "default") -> dict | None:
    row = conn.execute("SELECT data FROM profiles WHERE name = ?",
                       (name,)).fetchone()
    return json.loads(row["data"]) if row else None


def save_profile_for(client, profile: dict) -> int:
    """Upsert profile into profiles by name. Returns profile id."""
    now = datetime.now(timezone.utc).isoformat()
    name = profile.get("name", "default")
    # Postgres stores jsonb (send the object); the stub stores text.
    from fincoach.storage.sb_data import SupabaseRestClient
    if isinstance(client, SupabaseRestClient):
        data = dict(profile)
    else:
        data = json.dumps(profile)
    found = client.find("profiles", name=name)
    if not found:
        row = client.add("profiles", {
            "name": name, "data": data, "created_at": now})
        return int(row["id"])
    client.set("profiles", found[0]["id"], {"data": data})
    return int(found[0]["id"])


def load_profile_for(client, name: str = "default") -> dict | None:
    found = client.find("profiles", name=name)
    if not found:
        return None
    data = found[0].get("data", "{}")
    return data if isinstance(data, dict) else json.loads(data or "{}")
