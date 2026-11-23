"""Detect recurring merchants: cadence, status, and change flags."""

from __future__ import annotations

from datetime import date, timedelta
from statistics import median

AMOUNT_TOLERANCE = 0.02   # group amounts within 2%
CHANGE_THRESHOLD = 0.05  # flag amount change over 5%
MIN_OCCURRENCES = 3

_INTERVAL_DAYS = {"monthly": 30, "quarterly": 91, "yearly": 365}


def _norm_merchant(merchant: str) -> str:
    return " ".join(merchant.lower().split())


def _month_key(iso: str) -> str:
    return iso[:7]


def _parse(iso: str) -> date:
    return date.fromisoformat(iso[:10])


def _cadence(dates: list[date]) -> str:
    if len(dates) < 2:
        return "monthly"
    ordered = sorted(dates)
    gaps = [(b - a).days for a, b in zip(ordered, ordered[1:])]
    med = median(gaps)
    if med >= 300:
        return "yearly"
    if med >= 75:
        return "quarterly"
    return "monthly"


def detect(transactions: list[dict], today: str | None = None) -> list[dict]:
    """Group by merchant + amount (2% tolerance).

    Keeps groups with 3+ occurrences across distinct months. Each result has
    merchant, amount, category, direction, cadence, occurrences, months,
    status, flags, last_date, next_date, and amount_change_pct.
    """
    now = _parse(today) if today else date.today()
    by_merchant: dict[str, list[dict]] = {}
    for txn in transactions:
        key = _norm_merchant(str(txn.get("merchant") or txn.get("description") or ""))
        if key:
            by_merchant.setdefault(key, []).append(txn)

    results: list[dict] = []
    for merchant, txns in by_merchant.items():
        # Split into amount clusters within 2% of the cluster median.
        clusters: list[list[dict]] = []
        for txn in sorted(txns, key=lambda t: abs(float(t["amount"]))):
            placed = False
            for cluster in clusters:
                med = median([abs(float(t["amount"])) for t in cluster])
                if med and abs(abs(float(txn["amount"])) - med) / med <= AMOUNT_TOLERANCE:
                    cluster.append(txn)
                    placed = True
                    break
            if not placed:
                clusters.append([txn])
        clusters.sort(key=lambda c: min(str(t["date"]) for t in c))
        # Merge chronologically adjacent clusters (clean price change);
        # interleaved clusters stay separate (distinct concurrent charges).
        series: list[list[dict]] = []
        for cluster in clusters:
            if series and min(str(t["date"]) for t in cluster) > max(
                    str(t["date"]) for t in series[-1]):
                series[-1] = series[-1] + cluster
            else:
                series.append(list(cluster))
        for cluster in series:
            months = {_month_key(str(t["date"])) for t in cluster}
            if len(cluster) < MIN_OCCURRENCES or len(months) < MIN_OCCURRENCES:
                continue
            ordered = sorted(cluster, key=lambda t: str(t["date"]))
            dates = [_parse(str(t["date"])) for t in ordered]
            amounts = [abs(float(t["amount"])) for t in ordered]
            latest_month = max(months)
            latest = [abs(float(t["amount"])) for t in ordered
                      if _month_key(str(t["date"])) == latest_month]
            med_amount = float(median(latest)) if latest else float(median(amounts))
            base_amount = float(median(amounts))
            cadence = _cadence(dates)
            interval = _INTERVAL_DAYS[cadence]
            last = dates[-1]
            days_since = (now - last).days
            category = ordered[-1].get("category", "") or ""
            signed = [float(t["amount"]) for t in ordered]
            direction = "income" if median(signed) > 0 else "expense"

            flags: list[str] = []
            change_pct = ((med_amount - base_amount) / base_amount * 100) if base_amount else 0.0
            if abs(change_pct) > CHANGE_THRESHOLD * 100:
                flags.append(f"amount_changed:{change_pct:+.1f}%")

            if days_since > max(90, interval * 3) or (
                    cadence == "monthly" and days_since > 90):
                status = "inactive"
                flags.append("inactive_subscription")
            elif days_since > interval * 1.5:
                status = "missing"
                flags.append("missing_expected_payment")
            elif abs(change_pct) > CHANGE_THRESHOLD * 100:
                status = "changed"
            else:
                status = "active"

            results.append({
                "merchant": merchant,
                "amount": round(med_amount, 2),
                "category": category,
                "direction": direction,
                "cadence": cadence,
                "frequency": cadence,  # schema column alias
                "occurrences": len(cluster),
                "months": sorted(months),
                "status": status,
                "flags": flags,
                "last_date": last.isoformat(),
                "next_date": (last + timedelta(days=interval)).isoformat(),
                "amount_change_pct": round(change_pct, 1),
            })
    results.sort(key=lambda r: (-r["occurrences"], r["merchant"]))
    return results
