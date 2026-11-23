"""Monthly review: deterministic numbers plus an LLM-ready prompt."""

from __future__ import annotations

from collections import defaultdict


def monthly_review(transactions: list[dict], month: str,
                   recurring: list[dict] | None = None,
                   goals: list[dict] | None = None) -> dict:
    """Aggregate one YYYY-MM month. LLM prompt carries numbers only; no math by LLM."""
    recurring = recurring or []
    goals = goals or []
    month_txns = [t for t in transactions if str(t.get("date", ""))[:7] == month]

    income = sum(float(t["amount"]) for t in month_txns if float(t["amount"]) > 0)
    expenses = sum(-float(t["amount"]) for t in month_txns if float(t["amount"]) < 0)
    by_category: dict[str, float] = defaultdict(float)
    for txn in month_txns:
        if float(txn["amount"]) < 0:
            by_category[txn.get("category") or "other"] += -float(txn["amount"])
    savings = income - expenses
    rate = (savings / income * 100) if income else 0.0

    deviations = [r for r in recurring if r.get("status") in ("changed", "missing")]
    goal_rows = [{"name": g.get("name"), "on_track": (g.get("progress") or {}).get("on_track"),
                  "remaining": (g.get("progress") or {}).get("remaining")}
                 for g in goals]

    verified = sum(1 for t in month_txns if t.get("origin", "verified") == "verified")
    n = len(month_txns)

    highlights: list[str] = []
    if income:
        highlights.append(f"Income Rs {income:,.0f}; expenses Rs {expenses:,.0f}.")
    if rate >= 20:
        highlights.append(f"Savings rate {rate:.1f}% meets the 20% guideline.")
    elif income:
        highlights.append(f"Savings rate {rate:.1f}% is below the 20% guideline.")
    if by_category:
        top = max(by_category.items(), key=lambda kv: kv[1])
        highlights.append(f"Top spend: {top[0]} Rs {top[1]:,.0f}.")
    if deviations:
        highlights.append(f"{len(deviations)} recurring item(s) changed or missed.")

    explanations = [
        f"In {month} you earned Rs {income:,.0f} and spent Rs {expenses:,.0f}, "
        f"saving Rs {savings:,.0f} ({rate:.1f}%).",
        "Top categories: " + (", ".join(
            f"{c} Rs {v:,.0f}" for c, v in
            sorted(by_category.items(), key=lambda kv: -kv[1])[:3]) or "none") + ".",
    ]

    lines = [f"Month: {month}", f"Income: Rs {income:,.0f}",
             f"Expenses: Rs {expenses:,.0f}",
             f"Savings: Rs {savings:,.0f} ({rate:.1f}%)",
             "By category: " + (", ".join(f"{c}={v:,.0f}" for c, v in
                                           sorted(by_category.items())) or "none"),
             f"Recurring deviations: {len(deviations)}",
             f"Goals: " + (", ".join(
                 f"{g['name']} on_track={g['on_track']}" for g in goal_rows) or "none"),
             "",
             "Explain these exact numbers in friendly plain language. Do not "
             "recompute, round differently, or invent figures. End with one "
             "practical suggestion tied to the top category."]
    return {"month": month, "income": round(income, 2),
            "expenses": round(expenses, 2), "savings": round(savings, 2),
            "savings_rate": round(rate, 1),
            "by_category": {k: round(v, 2) for k, v in by_category.items()},
            "recurring_deviations": deviations, "goals": goal_rows,
            "highlights": highlights,
            "data_quality": {"txn_count": n,
                             "verified_pct": round(verified / n * 100, 1) if n else 0.0,
                             "inferred_pct": round((n - verified) / n * 100, 1) if n else 0.0},
            "explanations": explanations, "llm_prompt": "\n".join(lines)}
