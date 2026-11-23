"""Normalize raw dicts into canonical transactions."""

from __future__ import annotations

import re

from fincoach.ingest.dates import parse_date

_WS_RE = re.compile(r"\s+")
_JUNK_RE = re.compile(r"^(?:Rs\.?|INR|₹)\s*", re.IGNORECASE)

# Statement "type"/"Dr-Cr" column values (also emitted by PDF/image/SMS
# extractors as dr_cr hints): these decide the final sign.
_DEBIT_HINTS = frozenset({"debit", "debited", "dr", "d", "withdrawal",
                          "withdrawn", "paid out", "outflow", "expense"})
_CREDIT_HINTS = frozenset({"credit", "credited", "cr", "c", "deposit",
                           "deposited", "paid in", "inflow", "income"})


def _apply_dr_cr(amount: float, raw: dict) -> float:
    hint = " ".join(str(raw.get("dr_cr", "") or "").strip()
                    .lower().rstrip(".").split())
    if hint in _DEBIT_HINTS:
        return -abs(amount)
    if hint in _CREDIT_HINTS:
        return abs(amount)
    return amount


def parse_amount(raw: str | float | int) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace(",", "").replace("₹", "")
    text = re.sub(r"(?i)\b(rs\.?|inr)\b", "", text).strip()
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    trailing_crdr = re.search(r"(?i)\s*(cr|dr)\s*$", text)
    suffix = ""
    if trailing_crdr:
        suffix = trailing_crdr.group(1).lower()
        text = text[: trailing_crdr.start()].strip()
    value = float(text)
    if suffix == "cr":
        value = abs(value)
    elif suffix == "dr":
        value = -abs(value)
    return value


def clean_merchant(description: str) -> str:
    text = _JUNK_RE.sub("", description).strip()
    text = re.sub(r"\b\d{4,}\b", "", text)
    return _WS_RE.sub(" ", text).strip(" -|")[:120]


def normalize(raw: dict) -> dict:
    date = parse_date(str(raw.get("date", "")).strip())
    description = _WS_RE.sub(" ", str(raw.get("description", "")).strip())
    if "amount" in raw and str(raw["amount"]).strip() not in ("", None):
        amount = parse_amount(raw["amount"])
    else:
        debit = parse_amount(raw.get("debit") or 0)
        credit = parse_amount(raw.get("credit") or 0)
        amount = credit - abs(debit) if debit else credit
        if not debit and not credit:
            raise ValueError("missing amount")
    amount = _apply_dr_cr(amount, raw)
    if amount > 0:
        kind = "income"
    elif amount < 0:
        kind = "debit"
    else:
        kind = "other"
    return {
        "date": date,
        "description": description,
        "merchant": clean_merchant(description),
        "amount": amount,
        "currency": str(raw.get("currency", "INR") or "INR").upper(),
        "type": kind,
    }


def normalize_all(raws: list) -> list:
    return [normalize(r) for r in raws]
