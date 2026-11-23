"""PDF extraction: date-plus-amount line regex with page numbers."""

from __future__ import annotations

import io
import re

from fincoach.ingest.amounts import MARKED_RE, direction_hint, find_amount
from fincoach.ingest.dates import strip_dates
from fincoach.ingest.errors import IngestError

# Backwards-compatible alias: marked (Rs/INR) amounts of any size.
AMOUNT_RE = MARKED_RE


def txns_from_lines(lines: list, page: int | None = None) -> list:
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        _, iso = strip_dates(line)
        if not iso:
            continue
        found = find_amount(line)
        if not found:
            continue
        value, start, end = found
        desc = re.sub(r"\s+", " ",
                      (line[:start] + " " + line[end:]).strip(" -\t|")).strip()
        txn = {"date": iso, "description": desc or line, "amount": value}
        hint = direction_hint(line)
        if hint:
            txn["dr_cr"] = hint
        if page is not None:
            txn["page"] = page
        out.append(txn)
    return out


def extract_pdf(data: bytes, filename: str = "") -> list:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise IngestError(
            "pdf extraction needs the 'pypdf' pip package; run: pip install pypdf"
        ) from exc
    reader = PdfReader(io.BytesIO(data))
    out = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        out.extend(txns_from_lines(text.splitlines(), page=i))
    return out
