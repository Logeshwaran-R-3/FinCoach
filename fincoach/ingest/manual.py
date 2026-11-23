"""Manual entry: validate a form dict into one raw transaction."""

from __future__ import annotations

from fincoach.ingest.errors import IngestError


def manual_txn(form: dict) -> list:
    date = str(form.get("date", "")).strip()
    description = str(form.get("description", "")).strip()
    amount = str(form.get("amount", "")).strip()
    if not date:
        raise IngestError("manual entry needs a date")
    if not description:
        raise IngestError("manual entry needs a description")
    if not amount:
        raise IngestError("manual entry needs an amount")
    return [{"date": date, "description": description, "amount": amount}]
