"""Rule-based nudges, max 8, ordered by severity. Each carries numbers."""

from __future__ import annotations

MAX_NUDGES = 8
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _nudge(severity: str, kind: str, text: str, numbers: dict,
           source: str) -> dict:
    return {"severity": severity, "kind": kind, "text": text,
            "numbers": numbers, "source": source}


def generate_nudges(profile: dict, recurring: list[dict] | None = None,
                    goals: list[dict] | None = None,
                    monthly: dict | None = None) -> list[dict]:
    """Build up to 8 nudges: overspend, goals at risk, recurring changes."""
    recurring = recurring or []
    goals = goals or []
    monthly = monthly or {}
    nudges: list[dict] = []

    avg_by_cat = {c["category"]: float(c.get("total", 0))
                  for c in profile.get("top_categories", [])}
    for cat, current in (monthly.get("by_category") or {}).items():
        baseline = avg_by_cat.get(cat, 0.0)
        if baseline > 0 and current > baseline * 1.25:
            pct = (current - baseline) / baseline * 100
            nudges.append(_nudge(
                "high" if pct >= 50 else "medium", "overspend",
                f"{cat} spend Rs {current:,.0f} is {pct:.0f}% above your "
                f"Rs {baseline:,.0f} average.",
                {"category": cat, "current": current, "average": baseline,
                 "pct_over": round(pct, 1)},
                source="profile:top_categories"))

    for goal in goals:
        prog = goal.get("progress") or {}
        if prog.get("overdue"):
            nudges.append(_nudge(
                "high", "goal_overdue",
                f"Goal '{goal.get('name')}' is past due with "
                f"Rs {prog.get('remaining', 0):,.0f} remaining.",
                {"goal": goal.get("name"), "remaining": prog.get("remaining", 0)},
                source="goals:goal_progress"))
        elif prog and not prog.get("on_track", True):
            nudges.append(_nudge(
                "medium", "goal_at_risk",
                f"Goal '{goal.get('name')}' is off track: needs "
                f"Rs {prog.get('required_monthly', 0):,.0f}/month.",
                {"goal": goal.get("name"),
                 "required_monthly": prog.get("required_monthly", 0)},
                source="goals:goal_progress"))

    for item in recurring:
        merch = item.get("merchant", "?")
        if item.get("status") == "missing":
            nudges.append(_nudge(
                "high", "missing_payment",
                f"Missing expected {item.get('cadence', 'monthly')} payment to "
                f"{merch} (~Rs {item.get('amount', 0):,.0f}).",
                {"merchant": merch, "amount": item.get("amount", 0),
                 "last_date": item.get("last_date")},
                source="recurring:detect"))
        elif item.get("status") == "changed":
            nudges.append(_nudge(
                "medium", "recurring_increase",
                f"{merch} changed by {item.get('amount_change_pct', 0):+.1f}% "
                f"to ~Rs {item.get('amount', 0):,.0f}.",
                {"merchant": merch, "amount": item.get("amount", 0),
                 "change_pct": item.get("amount_change_pct", 0)},
                source="recurring:detect"))
        elif item.get("status") == "inactive":
            nudges.append(_nudge(
                "low", "inactive_subscription",
                f"{merch} (~Rs {item.get('amount', 0):,.0f}) shows no payment "
                f"in 90+ days; cancel if unused.",
                {"merchant": merch, "amount": item.get("amount", 0),
                 "last_date": item.get("last_date")},
                source="recurring:detect"))

    nudges.sort(key=lambda n: (_SEVERITY_RANK[n["severity"]], n["kind"]))
    return nudges[:MAX_NUDGES]
