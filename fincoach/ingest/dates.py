"""Shared date parsing for ingest extractors (stdlib only)."""

from __future__ import annotations

import re
from datetime import datetime

_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d",
            "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y")

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}")

_MONTH_RE = re.compile(
    r"\b\d{1,2}\s+[A-Za-z]{3,9}\s*,?\s*\d{2,4}\b"  # 12 Nov 2026
    r"|\b[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}\b"  # Nov 12, 2026
)


def parse_date(text: str) -> str:
    """Parse yyyy-mm-dd, dd-mm-yyyy, mm/dd/yyyy, or month-name dates to ISO."""
    text = " ".join(text.strip().replace(",", " ").split())
    for fmt in _FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"unparseable date: {text!r}")


def _spans(text: str) -> list:
    """All (iso_date, start, end) date matches in text order."""
    spans = []
    for m in _DATE_RE.finditer(text):
        try:
            spans.append((parse_date(m.group(0)), m.start(), m.end()))
        except ValueError:
            continue
    for m in _MONTH_RE.finditer(text):
        try:
            spans.append((parse_date(m.group(0)), m.start(), m.end()))
        except ValueError:
            continue
    spans.sort(key=lambda s: s[1])
    return spans


def find_date(text: str) -> str | None:
    m = _spans(text)
    return m[0][0] if m else None


def strip_dates(text: str) -> tuple:
    """Blank date substrings (length preserved) so amounts never eat them.

    Returns (cleaned_text, first_iso_date_or_None).
    """
    spans = _spans(text)
    if not spans:
        return text, None
    chars = list(text)
    for _, start, end in spans:
        for i in range(start, end):
            chars[i] = " "
    return "".join(chars), spans[0][0]
