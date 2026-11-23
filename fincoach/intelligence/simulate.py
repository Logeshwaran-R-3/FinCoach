"""What-if scenarios over a profile snapshot. Deterministic arithmetic."""

from __future__ import annotations

from fincoach.intelligence.goals import goal_progress


def run_scenario(profile: dict, dining_pct: float = 0.0, rent_delta: float = 0.0,
                 income_delta: float = 0.0, savings_delta: float = 0.0,
                 custom: dict | None = None, goals: list[dict] | None = None,
                 today: str | None = None) -> dict:
    """Apply deltas to a profile and report new savings + goal impacts.

    dining_pct: percent change to dining spend, e.g. -20 cuts 20%.
    rent_delta/income_delta/savings_delta: flat Rs/month changes.
    custom: {category: flat Rs/month delta} applied on top.
    """
    custom = custom or {}
    goals = goals or []
    base_income = float(profile.get("avg_monthly_income", 0))
    base_expenses = float(profile.get("avg_monthly_expenses", 0))
    base_savings = base_income - base_expenses

    by_cat = {c["category"]: float(c.get("monthly_avg", c.get("total", 0)))
              for c in profile.get("top_categories", [])}
    spend = profile.get("spend_by_category") or {}
    dining_base = float(spend.get("dining", by_cat.get("dining", 0.0)))
    dining_change = dining_base * dining_pct / 100

    expense_delta = rent_delta + dining_change + sum(float(v) for v in custom.values())
    new_income = base_income + income_delta
    new_expenses = base_expenses + expense_delta
    new_savings = new_income - new_expenses + savings_delta

    assumptions = [
        f"base income Rs {base_income:,.0f}/month, base expenses Rs {base_expenses:,.0f}/month",
        f"dining baseline Rs {dining_base:,.0f}/month x {dining_pct:+.0f}%",
        f"rent {rent_delta:+,.0f}, income {income_delta:+,.0f}, extra savings {savings_delta:+,.0f}",
    ]
    if custom:
        assumptions.append("custom: " + ", ".join(
            f"{k} {v:+,.0f}" for k, v in custom.items()))
    assumptions.append("goal impacts assume new savings pace stays steady")

    impacts = []
    for goal in goals:
        prog = goal_progress(goal, monthly_savings=new_savings, today=today)
        impacts.append({"name": goal.get("name", f"goal {goal.get('id', '?')}"),
                        "on_track": prog["on_track"],
                        "projected_date": prog["projected_date"],
                        "days_to_goal": prog["days_to_goal"],
                        "required_monthly": prog["required_monthly"]})

    warning = None
    if new_expenses > new_income:
        warning = (f"expenses Rs {new_expenses:,.0f} exceed income "
                   f"Rs {new_income:,.0f} by Rs {new_expenses - new_income:,.0f}/month")

    return {"new_income": round(new_income, 2),
            "new_expenses": round(new_expenses, 2),
            "new_savings": round(new_savings, 2),
            "delta_vs_base": round(new_savings - base_savings, 2),
            "goal_impacts": impacts,
            "assumptions": assumptions,
            "warning": warning}
