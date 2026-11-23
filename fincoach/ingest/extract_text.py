"""SMS / pasted-text extraction: Rs/INR amounts, Dr/Cr/UPI markers, verbs."""

from __future__ import annotations

import re
from datetime import date

from fincoach.ingest.amounts import (CREDIT_WORDS, DEBIT_WORDS, MARKED_RE,
                                    find_amount, sms_is_credit)
from fincoach.ingest.dates import find_date
from fincoach.ingest.errors import IngestError

AMOUNT_RE = MARKED_RE
CREDIT_HINTS = CREDIT_WORDS
DEBIT_HINTS = DEBIT_WORDS

# Everyday verbs that let a BARE amount (no Rs marker) count as money.
VERB_RE = re.compile(
    r"\b(sent|send|paid|pay|gave|give|received|receive|got|get|spent|spend|"
    r"transferred|transfer|deposited|deposit|withdrawn|withdrew|credited|"
    r"debited|credit|debit|collected|cashback|refund)\b", re.IGNORECASE)


def extract_text(data: bytes, filename: str = "") -> list:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IngestError(f"cannot decode text source: {exc}") from exc
    today = date.today().isoformat()
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = MARKED_RE.search(line)
        if m:
            raw_value = m.group(1).replace(",", "")
        elif VERB_RE.search(line):
            found = find_amount(line)
            if not found:
                continue
            raw_value = found[0]
        else:
            continue
        try:
            number = float(raw_value.replace(",", ""))
        except ValueError:
            continue
        signed = abs(number) if sms_is_credit(line) else -abs(number)
        out.append(
            {
                "date": find_date(line) or today,
                "description": line,
                "amount": signed,
            }
        )
    return out
