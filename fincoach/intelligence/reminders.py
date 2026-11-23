"""Bill reminders: auto-detected regular bills + manual reminders with leads.

Auto-detection reuses recurring.detect (merchants repeating across 3+
months); each detected expense bill becomes a reminder candidate with its
projected next due date. Manual reminders carry an amount and a lead time
(1 day / 2 days / 1 week) and are stored per user in the reminders table.
Due reminders are emailed via fincoach.services.mailer (stdlib smtplib).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from fincoach.intelligence import recurring as recurring_mod

LEADS = {"1day": 1, "2day": 2, "3day": 3, "4day": 4,"5day": 5, "6day": 6, "1week": 7}
LEAD_LABELS = {"1day": "1 day before", "2day": "2 days before", "3day": "3 days before", "4day": "4 days before",
                "5day": "5 days before", "6day": "6 days before",
               "1week": "1 week before"}
KINDS = ("manual", "bill")


def _today(today: str | None) -> date:
    return date.fromisoformat(today) if today else date.today()


def auto_detect(transactions: list[dict],
                today: str | None = None) -> list[dict]:
    """Regular bills detected from history: merchant, amount, next due date.

    Only expense-direction items with a live status (active/changed/missing)
    qualify; quiet 90+ day subscriptions are left out.
    """
    out = []
    for item in recurring_mod.detect(transactions, today=today):
        if item.get("direction", "expense") != "expense":
            continue
        if item.get("status") not in ("active", "changed", "missing"):
            continue
        out.append({
            "merchant": item["merchant"],
            "amount": item["amount"],
            "cadence": item.get("cadence", "monthly"),
            "category": item.get("category", ""),
            "status": item.get("status", "active"),
            "last_date": item.get("last_date", ""),
            "next_date": item.get("next_date", ""),
        })
    return out


def days_until(due_date: str, today: str | None = None) -> int | None:
    """Days from today until due_date (negative = overdue; None if blank)."""
    if not (due_date or "").strip():
        return None
    try:
        return (date.fromisoformat(due_date[:10]) - _today(today)).days
    except ValueError:
        return None


def is_due(reminder: dict, today: str | None = None) -> bool:
    """A reminder is due when it is not done and its due date falls within
    its lead window (overdue counts as due)."""
    if reminder.get("done"):
        return False
    left = days_until(str(reminder.get("due_date", "")), today)
    if left is None:
        return False
    lead_days = LEADS.get(str(reminder.get("lead", "1day")), 1)
    return left <= lead_days


def due_reminders(reminders: list[dict],
                  today: str | None = None) -> list[dict]:
    """Stored reminders that should trigger an email today, oldest first."""
    due = [r for r in reminders if is_due(r, today)]
    due.sort(key=lambda r: str(r.get("due_date", "")))
    return due


def validate(text: str, amount: float, due_date: str, lead: str) -> dict:
    """Validate manual-reminder input; returns the cleaned row (no id)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Describe the reminder (e.g. 'Rent').")
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise ValueError("Amount must be a number.")
    if amount < 0:
        raise ValueError("Amount cannot be negative.")
    due_date = (due_date or "").strip()[:10]
    if not due_date:
        raise ValueError("Pick a due date.")
    try:
        date.fromisoformat(due_date)
    except ValueError:
        raise ValueError("Due date must be YYYY-MM-DD.")
    lead = (lead or "").strip()
    if lead not in LEADS:
        raise ValueError(f"Lead must be one of {sorted(LEADS)}.")
    return {"text": text, "amount": round(amount, 2), "due_date": due_date,
            "lead": lead}


def list_for(client) -> list[dict]:
    """Stored reminders for this user, oldest due date first."""
    rows = client.rows("reminders")
    rows.sort(key=lambda r: (str(r.get("due_date", "") or "9999"),
                             int(r.get("id") or 0)))
    return rows


def create_for(client, text: str, amount: float, due_date: str,
               lead: str = "1day", kind: str = "manual",
               email: str = "") -> dict:
    """Store a manual (or bill-derived) reminder for this user."""
    row = validate(text, amount, due_date, lead)
    if kind not in KINDS:
        raise ValueError(f"Kind must be one of {KINDS}.")
    row["kind"] = kind
    row["email"] = (email or "").strip()
    row["done"] = False
    row["created_at"] = datetime.now(timezone.utc).isoformat()
    return client.add("reminders", row)


def mark_done_for(client, reminder_id: int, done: bool = True) -> dict:
    return client.set("reminders", reminder_id, {"done": bool(done)})


def delete_for(client, reminder_id: int) -> None:
    client.remove("reminders", reminder_id)
