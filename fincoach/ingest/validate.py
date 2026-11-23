"""Validate normalized transactions into valid + rejected lists."""

from __future__ import annotations

from datetime import date, timedelta

ALLOWED_ORIGINS = ("verified", "estimated", "inferred", "projected")


def validate(txns: list) -> tuple:
    valid, rejected = [], []
    horizon = date.today() + timedelta(days=7)
    for txn in txns:
        txn = dict(txn)
        reason = None
        if not txn.get("date"):
            reason = "missing date"
        elif not txn.get("description"):
            reason = "missing description"
        elif txn.get("amount") is None:
            reason = "missing amount"
        else:
            try:
                txn_date = date.fromisoformat(str(txn["date"]))
            except ValueError:
                reason = f"bad date {txn['date']!r}"
            else:
                try:
                    txn["amount"] = float(txn["amount"])
                except (TypeError, ValueError):
                    reason = f"bad amount {txn['amount']!r}"
        if reason:
            rejected.append({"txn": txn, "reason": reason})
            continue
        if txn_date > horizon:
            txn["origin"] = "estimated"
            txn["note"] = "future-dated beyond 7 days"
        else:
            txn.setdefault("origin", "verified")
        if txn["origin"] not in ALLOWED_ORIGINS:
            rejected.append(
                {"txn": txn, "reason": f"bad origin {txn['origin']!r}"}
            )
            continue
        valid.append(txn)
    return valid, rejected
