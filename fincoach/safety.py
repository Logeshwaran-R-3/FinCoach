"""Safety: disclaimer, advice-request guard, and origin labels."""

from __future__ import annotations

import re

from fincoach.config import SAFETY_DISCLAIMER

ORIGIN_LABELS = {
    "verified": "from your uploaded statements",
    "estimated": "estimated from partial data",
    "inferred": "inferred by FinCoach rules",
    "projected": "projected scenario, not actual",
}

_ADVICE_PATTERNS = [
    r"\bbuy\b", r"\bsell\b", r"\bshort\b", r"\bstock tip", r"\bmultibagger\b",
    r"\bwhich stock\b", r"\bwhich fund\b", r"\bshould i (buy|sell|invest)\b",
    r"\bguaranteed return", r"\btip\b.*\bstock\b", r"\bstock\b.*\btip\b",
    r"\bprice target\b", r"\binsider\b",
]
_ADVICE_RE = re.compile("|".join(_ADVICE_PATTERNS), re.IGNORECASE)

REFUSAL = ("I can't recommend specific stocks or buy/sell actions. FinCoach "
           "shows spending patterns and savings projections for education only. "
           "Please consult a qualified professional.")


def get_disclaimer() -> str:
    return SAFETY_DISCLAIMER


def origin_label(origin: str) -> str:
    return ORIGIN_LABELS.get(origin, ORIGIN_LABELS["inferred"])


def is_advice_request(text: str) -> bool:
    """True when the user asks for buy/sell or stock-tip advice."""
    return bool(_ADVICE_RE.search(text or ""))


def guard(text: str) -> tuple[bool, str | None]:
    """Return (allowed, refusal_or_None). Refused requests get the refusal text."""
    if is_advice_request(text):
        return False, REFUSAL
    return True, None
