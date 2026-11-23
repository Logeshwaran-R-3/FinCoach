"""Dedupe: exact sha256 plus fuzzy same-date/amount/similar-text."""

from __future__ import annotations

import hashlib
from datetime import date
from difflib import SequenceMatcher

FUZZY_THRESHOLD = 0.85


def txn_hash(txn: dict) -> str:
    key = f"{txn['date']}|{float(txn['amount']):.2f}|{txn['description'].strip().lower()}"
    return hashlib.sha256(key.encode()).hexdigest()


def _same_day(a: str, b: str) -> bool:
    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days) <= 1
    except ValueError:
        return a == b


def _fuzzy(a: dict, b: dict) -> bool:
    if not _same_day(a["date"], b["date"]):
        return False
    if abs(float(a["amount"]) - float(b["amount"])) > 0.005:
        return False
    ratio = SequenceMatcher(
        None, a["description"].lower(), b["description"].lower()
    ).ratio()
    return ratio >= FUZZY_THRESHOLD


def dedupe(new_txns: list, existing_txns: list) -> tuple:
    seen_hashes = {t.get("hash") for t in existing_txns if t.get("hash")}
    seen_hashes |= {txn_hash(t) for t in existing_txns if not t.get("hash")}
    unique, dupes = [], []
    for txn in new_txns:
        txn = dict(txn)
        txn["hash"] = txn_hash(txn)
        if txn["hash"] in seen_hashes:
            dupes.append(txn)
            continue
        if any(_fuzzy(txn, e) for e in existing_txns + unique):
            dupes.append(txn)
            continue
        seen_hashes.add(txn["hash"])
        unique.append(txn)
    return unique, dupes
