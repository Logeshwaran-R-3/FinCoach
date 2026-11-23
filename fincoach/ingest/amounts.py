"""Shared amount + direction detection for statement/screenshot/SMS lines."""

from __future__ import annotations

import re

from fincoach.ingest.dates import strip_dates

# Rs/INR-marked amounts of ANY size (Rs 250 must match, not just 4+ digits).
MARKED_RE = re.compile(
    r"(?:Rs\.?|INR|₹)\s*([+-]?[\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)
DECIMAL_RE = re.compile(r"[+-]?\d[\d,]*\.\d{1,2}")
BARE_RE = re.compile(r"[+-]?\d[\d,]*")

CREDIT_WORDS = ("credited", "credit", "received", "receive", "got",
                "refund", "cashback", "collected", "collect",
                "deposit", "deposited", "paid in", "inflow", "income")
DEBIT_WORDS = ("debited", "debit", "paid", "pay", "sent", "send",
               "gave", "give", "spent", "spend", "purchase", "payment",
               "withdrawal", "withdrawn", "paid out", "outflow")
# Strong money-out verbs: these veto a credit reading in SMS text.
STRONG_DEBIT_WORDS = ("debited", "debit", "paid", "sent", "send",
                      "gave", "give", "spent", "spend",
                      "withdrawal", "withdrawn")


def _word_re(words) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(map(re.escape, words)) + r")\b",
                      re.IGNORECASE)


_CREDIT_RE = _word_re(CREDIT_WORDS)
_DEBIT_RE = _word_re(DEBIT_WORDS)
_STRONG_DEBIT_RE = _word_re(STRONG_DEBIT_WORDS)
_DRCR_RE = re.compile(r"\b(dr|cr)\b", re.IGNORECASE)


def direction_hint(line: str) -> str:
    """'debit', 'credit', or '' when the line is ambiguous/unmarked."""
    credit = bool(_CREDIT_RE.search(line))
    debit = bool(_DEBIT_RE.search(line))
    for m in _DRCR_RE.finditer(line):
        if m.group(1).lower() == "cr":
            credit = True
        else:
            debit = True
    if credit and not debit:
        return "credit"
    if debit and not credit:
        return "debit"
    return ""


def sms_is_credit(line: str) -> bool:
    """SMS direction: a credit word wins unless a strong money-out verb
    (paid/sent/gave/...) is also present. Bare 'pay/payment/upi' alone
    does not veto a credit reading."""
    credit = bool(_CREDIT_RE.search(line))
    if not credit:
        for m in _DRCR_RE.finditer(line):
            if m.group(1).lower() == "cr":
                credit = True
    if not credit:
        return False
    return not bool(_STRONG_DEBIT_RE.search(line))


def _is_year(text: str) -> bool:
    digits = text.lstrip("+-")
    return (len(digits) == 4 and digits.isdigit()
            and 1900 <= int(digits) <= 2100)


def find_amount(line: str):
    """Primary amount on a line: (value, start, end) or None.

    Dates are blanked first so years/fragments never match. Preference:
    Rs-marked, then decimals, then the first bare number that is not a
    standalone year (non-zero preferred).
    """
    cleaned, _ = strip_dates(line)
    m = MARKED_RE.search(cleaned)
    if m:
        return m.group(1).replace(",", ""), m.start(), m.end()
    d = DECIMAL_RE.search(cleaned)
    if d:
        return d.group(0).replace(",", ""), d.start(), d.end()
    zero = None
    for b in BARE_RE.finditer(cleaned):
        text = b.group(0).replace(",", "")
        if not text.lstrip("+-"):
            continue
        if _is_year(text):
            continue
        try:
            number = float(text)
        except ValueError:
            continue
        if number == 0:
            zero = (text, b.start(), b.end())
            continue
        return text, b.start(), b.end()
    return zero
