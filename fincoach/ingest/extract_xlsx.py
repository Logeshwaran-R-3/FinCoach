"""XLSX extraction from the first sheet (same columns as CSV)."""

from __future__ import annotations

import io

from fincoach.ingest.errors import IngestError


def extract_xlsx(data: bytes, filename: str = "") -> list:
    try:
        import openpyxl
    except ImportError as exc:
        raise IngestError(
            "xlsx extraction needs the 'openpyxl' pip package; run: pip install openpyxl"
        ) from exc
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [(str(c).strip() if c is not None else "") for c in rows[0]]
    lowered = [h.lower() for h in headers]

    def col(*names: str) -> int | None:
        for name in names:
            if name in lowered:
                return lowered.index(name)
        for i, h in enumerate(lowered):
            if any(n in h for n in names):
                return i
        return None

    i_date = col("date", "txn date", "transaction date")
    i_desc = col("description", "narrative", "particulars", "details")
    i_amt = col("amount", "amt", "value")
    i_dr = col("debit", "dr", "withdrawal", "paid out")
    i_cr = col("credit", "cr", "deposit", "paid in")
    i_type = col("type", "dr/cr", "drcr", "txn type",
                 "transaction type", "debit/credit")
    out = []
    for row in rows[1:]:
        vals = [(str(c).strip() if c is not None else "") for c in row]

        def cell(i: int | None) -> str:
            return vals[i] if i is not None and i < len(vals) else ""

        if not any(vals):
            continue
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
