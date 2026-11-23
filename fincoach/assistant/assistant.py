"""Deterministic chat assistant over the Living Money Model.

Turns typed questions into status lookups and what-if scenarios. No LLM is
involved: every figure comes from profile/simulate/review, with assumptions.
Expected ctx: {"profile", "recurring", "goals", "txns", "months"} where goals
carry a "progress" dict each (see app._goals_with_progress).
"""

from __future__ import annotations

import re

from fincoach.intelligence import review as review_mod
from fincoach import safety
from fincoach.intelligence import simulate as simulate_mod

HELP = (
    "Ask me things like:\n"
    "- how am i doing?  (income, expenses, savings)\n"
    "- how much did i spend on dining?\n"
    "- are my goals on track?\n"
    "- list my subscriptions\n"
    "- review 2026-11\n"
    "- what if i cut dining by 30%?\n"
    "- what if rent goes up by 2000?\n"
    "- what if my income drops by 5000?"
)

_CUT_WORDS = ("cut", "reduce", "lower", "drop", "save", "less", "fewer", "-")
_RAISE_WORDS = ("raise", "increase", "up", "more", "extra", "add", "+")


def _rs(value: float) -> str:
    return f"Rs {float(value):,.0f}"


def _num(text: str) -> float:
    return float(text.replace(",", ""))


def _dining_pct(question: str) -> float | None:
    """Percent change for dining, e.g. 'cut dining by 30%' -> -30."""
    m = (re.search(r"dining[^0-9%]{0,20}?(\d+(?:\.\d+)?)\s*%", question)
         or re.search(r"(\d+(?:\.\d+)?)\s*%[^.]{0,20}?dining", question))
    if not m:
        return None
    pct = float(m.group(1))
    if any(w in question for w in _RAISE_WORDS):
        return pct
    return -pct


def _flat_delta(question: str, keywords: tuple[str, ...]) -> float | None:
    """Signed Rs/month delta after rent/income/save keywords."""
    for word in keywords:
        m = re.search(word + r"\s*(?:extra\s+)?(?:goes\s+|drops?\s+|falls?\s+|"
                      r"rises?\s+)?(up|down|by|\+|-)?\s*(?:by\s+|rs\.?\s*)?"
                      r"(\d[\d,]*)", question)
        if not m:
            continue
        amount = _num(m.group(2))
        marker = (m.group(1) or "").lower()
        surrounding = question[max(0, m.start() - 12):m.end() + 4]
        if marker in ("-", "down") or any(
                w in surrounding for w in ("cut", "drop", "fall", "less", "down")):
            return -amount
        return amount
    return None


def _parse_scenario(question: str) -> dict:
    """Collect simulate() kwargs mentioned in free text. Empty when none."""
    found: dict = {}
    dining = _dining_pct(question)
    if dining:
        found["dining_pct"] = dining
    rent = _flat_delta(question, ("rent",))
    if rent:
        found["rent_delta"] = rent
    income = _flat_delta(question, ("income", "salary", "pay"))
    if income:
        found["income_delta"] = income
    save = _flat_delta(question, ("save", "savings"))
    if save:
        found["savings_delta"] = abs(save)
    return found


def _status_answer(profile: dict, goals: list[dict]) -> str:
    lines = [
        f"Income {_rs(profile.get('avg_monthly_income', 0))}/month, "
        f"expenses {_rs(profile.get('avg_monthly_expenses', 0))}/month, "
        f"savings {_rs(profile.get('savings', 0))}/month "
        f"({profile.get('savings_rate', 0)}%).",
        f"Fixed {_rs(profile.get('fixed_expenses', 0))}, "
        f"variable {_rs(profile.get('variable_expenses', 0))}, "
        f"trend {profile.get('trend', 'unknown')}.",
    ]
    top = profile.get("top_categories") or []
    if top:
        lines.append("Top spend: " + ", ".join(
            f"{c['category']} {_rs(c.get('total', 0))}" for c in top[:3]) + ".")
    if goals:
        bad = [g["name"] for g in goals
               if not (g.get("progress") or {}).get("on_track")]
        lines.append("Goals on track." if not bad
                     else "Off track: " + ", ".join(bad) + ".")
    return "\n".join(lines)


def _category_answer(question: str, profile: dict) -> str | None:
    """Answer 'how much on <category>' when a known category is named."""
    spend = profile.get("spend_by_category") or {}
    names = set(spend) | {c["category"] for c in profile.get("top_categories", [])}
    named = [c for c in names if c and re.search(r"\b" + re.escape(c) + r"\b", question)]
    if not named or not re.search(r"how much|spen[dt]|cost|total", question):
        return None
    cat = sorted(named)[0]
    monthly = float(spend.get(cat, 0))
    months = profile.get("months_used", 0)
    return (f"{cat}: {_rs(monthly)}/month on average"
            + (f" across {months} months." if months else "."))


def _scenario_answer(profile: dict, goals: list[dict], deltas: dict) -> str:
    result = simulate_mod.run_scenario(profile, goals=goals, **deltas)
    lines = [f"New savings {_rs(result['new_savings'])}/month "
             f"({result['delta_vs_base']:+,.0f} vs base)."]
    for g in result["goal_impacts"]:
        lines.append(f"{g['name']}: on_track={g['on_track']}, "
                     f"projected {g['projected_date'] or 'n/a'}.")
    if result["warning"]:
        lines.append(result["warning"])
    lines.append("Assumes: " + "; ".join(result["assumptions"]) + ".")
    return "\n".join(lines)


def _goals_answer(goals: list[dict]) -> str:
    if not goals:
        return "No goals yet. Create one on the Goals page with a target and date."
    lines = []
    for g in goals:
        prog = g.get("progress") or {}
        lines.append(
            f"{g.get('name')}: saved {_rs(prog.get('saved', 0))} of "
            f"{_rs(g.get('target', 0))}, on_track={prog.get('on_track')}, "
            f"needs {_rs(prog.get('required_monthly', 0))}/month, "
            f"projected {prog.get('projected_date') or 'n/a'}.")
    return "\n".join(lines)


def _recurring_answer(recurring: list[dict]) -> str:
    items = [r for r in recurring if r.get("status") != "inactive"]
    if not items:
        return "No recurring payments detected yet."
    lines = [f"{r['merchant']}: {_rs(r['amount'])} {r['cadence']}, "
             f"next {r.get('next_date', '?')}"
             + (f" [{','.join(r.get('flags', []))}]" if r.get("flags") else "")
             for r in items[:8]]
    if len(items) > 8:
        lines.append(f"...and {len(items) - 8} more on the Recurring page.")
    return "\n".join(lines)


def _review_answer(month: str, txns: list[dict], recurring: list[dict],
                   goals: list[dict]) -> str:
    rows = [{"name": g.get("name"), "progress": g.get("progress", {})} for g in goals]
    data = review_mod.monthly_review(txns, month, recurring, rows)
    lines = [f"{month}: income {_rs(data['income'])}, expenses "
             f"{_rs(data['expenses'])}, savings {_rs(data['savings'])} "
             f"({data['savings_rate']}%)."]
    lines.extend(data["highlights"])
    return "\n".join(lines)


def answer(question: str, ctx: dict) -> str:
    """Answer a free-text question from ctx. Always deterministic."""
    q = (question or "").strip()
    if not q:
        return HELP
    allowed, refusal = safety.guard(q)
    if not allowed:
        return refusal or safety.REFUSAL
    low = q.lower()
    profile = ctx.get("profile") or {}
    goals = ctx.get("goals") or []
    rec = ctx.get("recurring") or []
    txns = ctx.get("txns") or []
    months = ctx.get("months") or []

    deltas = _parse_scenario(low)
    if deltas:
        return _scenario_answer(profile, goals, deltas)
    if re.search(r"\breview\b|\bmonth\b", low):
        m = re.search(r"(20\d\d-[01]\d)", low)
        month = m.group(1) if m else (months[-1] if months else "")
        if not month:
            return "No months of data yet. Upload a source first."
        return _review_answer(month, txns, rec, goals)
    if re.search(r"\bgoal", low):
        return _goals_answer(goals)
    if re.search(r"subscri|recurring|bills?\b|\bemi\b", low):
        return _recurring_answer(rec)
    category = _category_answer(low, profile)
    if category:
        return category
    if re.search(r"sav|income|expense|spen[dt]|status|overview|doing|"
                  r"summary|health", low):
        return _status_answer(profile, goals)
    return "I didn't catch that.\n" + HELP
