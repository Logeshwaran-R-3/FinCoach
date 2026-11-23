"""CSV extraction with stdlib delimiter sniffing."""

from __future__ import annotations

import csv
import io


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("utf-8", errors="replace")


def _col(headers: list, *names: str) -> int | None:
    lowered = [h.strip().lower() for h in headers]
    for name in names:
        if name in lowered:
            return lowered.index(name)
    for i, h in enumerate(lowered):
        if any(n in h for n in names):
            return i
    return None


def extract_csv(data: bytes, filename: str = "") -> list:
    text = _decode(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        return []
    headers = rows[0]
    i_date = _col(headers, "date", "txn date", "transaction date")
    i_desc = _col(headers, "description", "narrative", "particulars", "details")
    i_amt = _col(headers, "amount", "amt", "value")
    i_dr = _col(headers, "debit", "dr", "withdrawal", "paid out")
    i_cr = _col(headers, "credit", "cr", "deposit", "paid in")
    i_type = _col(headers, "type", "dr/cr", "drcr", "txn type",
                  "transaction type", "debit/credit")
    out = []
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue

        def cell(i: int | None) -> str:
            return row[i].strip() if i is not None and i < len(row) else ""

        txn = {"date": cell(i_date), "description": cell(i_desc)}
        if cell(i_type):
            txn["dr_cr"] = cell(i_type)
        if i_amt is not None and cell(i_amt):
            txn["amount"] = cell(i_amt)
        else:
            dr, cr = cell(i_dr), cell(i_cr)
            if dr or cr:
                txn["debit"] = dr
                txn["credit"] = cr
            else:
                continue
        out.append(txn)
    return out
