"""Chat tool actions: data changes the assistant can make on request.

Four deterministic actions — add a manual transaction, create a goal,
record a contribution, recategorize a transaction — parsed from plain
words. Parsing never touches data: the app shows the proposal and only
executes it after the user presses Confirm (see the assistant route).
"""

from __future__ import annotations

import re
from datetime import date

from fincoach.intelligence import goals as goals_mod
from fincoach.intelligence import learn as learn_mod
from fincoach.intelligence.categorize import DEFAULT_RULES, categorize
from fincoach.ingest.dedupe import dedupe
from fincoach.ingest.normalize import normalize

KNOWN_CATEGORIES = sorted(set(DEFAULT_RULES) | {"other"})

_AMOUNT_RE = re.compile(r"(?:rs\.?\s*)?(\d[\d,]*\.?\d*)", re.IGNORECASE)
_DATE_RE = re.compile(r"(20\d\d-[01]\d-[0-3]\d)")
_TXN_ID_RE = re.compile(r"(?:transaction|txn|#)\s*(\d+)", re.IGNORECASE)

_INCOME_HINTS = ("income", "salary", "credit", "received", "refund",
                 "freelance", "pay")


def _amount(text: str) -> float | None:
    m = _AMOUNT_RE.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _strip_noise(text: str, *bits: str) -> str:
    out = text or ""
    for bit in bits:
        out = re.sub(bit, " ", out, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip(" .,'\"")


def _parse_contribution(question: str) -> dict | None:
    """'contribute 5000 to emergency fund' / 'add 2000 to Goa trip'."""
    m = re.search(
        r"\b(contribute|contribution|add|put|record)\b.{0,40}?"
        r"(?:rs\.?\s*)?(\d[\d,]*\.?\d*).{0,10}?"
        r"\b(to|towards|toward|into|for)\b\s+(.+)",
        question, re.IGNORECASE)
    if not m:
        m = re.search(r"\bcontribute\b\s+(?:rs\.?\s*)?(\d[\d,]*\.?\d*)\s+(.+)",
                      question, re.IGNORECASE)
        if not m:
            return None
        amount, name = m.group(1), m.group(2)
    else:
        amount, name = m.group(2), m.group(4)
    try:
        value = float(amount.replace(",", ""))
    except ValueError:
        return None
    name = _strip_noise(name)
    if value <= 0 or not name:
        return None
    return {"kind": "record_contribution",
            "params": {"goal": name[:60], "amount": value},
            "summary": (f"Record a contribution of Rs {value:,.0f} "
                        f"to goal '{name[:60]}'.")}


def _parse_create_goal(question: str) -> dict | None:
    """'create goal Goa trip 50000 by 2027-06-30' (target required)."""
    m = re.search(r"\b(create|add|start|new)\b.{0,12}?\bgoal\b\s*"
                  r"(?:called|named)?\s*(.+)", question, re.IGNORECASE)
    if not m:
        return None
    rest = m.group(2)
    value = _amount(rest)
    if not value or value <= 0:
        return None
    day = _DATE_RE.search(rest)
    deadline = day.group(1) if day else ""
    name = _strip_noise(rest, r"20\d\d-[01]\d-[0-3]\d",
                        r"(?:rs\.?\s*)?\d[\d,]*\.?\d*",
                        r"\b(target|of|by|deadline|goal|create|add|start|new"
                        r"|called|named|for|a|an|the)\b")
    if not name:
        return None
    summary = (f"Create goal '{name[:60]}' with target Rs {value:,.0f}"
               + (f", deadline {deadline}" if deadline else "") + ".")
    return {"kind": "create_goal",
            "params": {"name": name[:60], "target": value,
                       "deadline": deadline},
            "summary": summary}


def _parse_recategorize(question: str) -> dict | None:
    """'recategorize transaction 12 to dining' / 'change Swiggy to groceries'."""
    m = re.search(r"\b(recategorize|recategorise|change category|"
                  r"set category|change|move)\b\s+(.+?)\s+\bto\b\s+"
                  r"([a-z][a-z _-]*)\s*$", question, re.IGNORECASE)
    if not m:
        return None
    category = m.group(3).strip().lower().replace("-", " ").replace("_", " ")
    category = re.sub(r"\s+", " ", category)
    if category not in KNOWN_CATEGORIES:
        return None
    ref = _strip_noise(m.group(2), r"\b(transaction|txn|category|the|a|an)\b")
    params: dict = {"category": category}
    tid = _TXN_ID_RE.search(question)
    if tid:
        params["txn_id"] = int(tid.group(1))
        what = f"transaction {params['txn_id']}"
    elif ref and not ref.isdigit():
        params["match"] = ref[:60]
        what = f"transactions matching '{ref[:60]}'"
    elif ref.isdigit():
        params["txn_id"] = int(ref)
        what = f"transaction {params['txn_id']}"
    else:
        return None
    return {"kind": "recategorize", "params": params,
            "summary": f"Set {what} to category '{category}' "
                       f"(and learn it for next time)."}


def _parse_add_transaction(question: str) -> dict | None:
    """'add expense 250 for coffee on 2026-11-20' (amount required)."""
    if not re.search(r"\b(add|record|log)\b", question, re.IGNORECASE):
        return None
    if not re.search(r"\b(transaction|expense|spending|entry|purchase"
                     r"|income)\b", question, re.IGNORECASE):
        return None
    value = _amount(question)
    if not value or value <= 0:
        return None
    day = _DATE_RE.search(question)
    day_str = day.group(1) if day else date.today().isoformat()
    desc = _strip_noise(question, r"20\d\d-[01]\d-[0-3]\d",
                        r"(?:rs\.?\s*)?\d[\d,]*\.?\d*",
                        r"\b(add|record|log|transaction|expense|spending"
                        r"|entry|purchase|income|for|a|an|the|on|of|my)\b")
    if len(desc) < 3:
        return None
    low = question.lower()
    signed = abs(value) if any(w in low for w in _INCOME_HINTS) else -abs(value)
    kind_word = "income" if signed > 0 else "expense"
    return {"kind": "add_transaction",
            "params": {"date": day_str, "description": desc[:80],
                       "amount": signed},
            "summary": (f"Add {kind_word} {day_str} '{desc[:80]}' "
                        f"Rs {signed:+,.0f}.")}


def parse_action(question: str, ctx: dict | None = None) -> dict | None:
    """Detect a data-change request. Returns the action or None.

    Pure parsing — never writes. Returns None for ordinary questions so
    they flow to the normal answer path.
    """
    if not (question or "").strip():
        return None
    # add_transaction runs before create_goal/contribution so "add expense
    # 250 for coffee" (which also carries a preposition) is not mistaken
    # for a goal contribution; those have no transaction keyword.
    for parser in (_parse_recategorize, _parse_add_transaction,
                   _parse_create_goal, _parse_contribution):
        found = parser(question.strip())
        if found is not None:
            return found
    return None


def describe(action: dict) -> str:
    """Proposal text shown with Confirm/Cancel; nothing is applied yet."""
    return ((action or {}).get("summary") or "No-op.") + (
        "\nPress Confirm to apply it, or Cancel to discard.")


def _resolve_goal(client, name: str) -> dict | None:
    rows = client.rows("goals")
    for g in rows:
        if str(g.get("name", "")).strip().lower() == name.strip().lower():
            return g
    hits = [g for g in rows if name.strip().lower()
            in str(g.get("name", "")).lower()]
    return hits[0] if len(hits) == 1 else None


def _resolve_txn(client, params: dict) -> dict | None:
    if params.get("txn_id"):
        return client.get("transactions", int(params["txn_id"]))
    needle = str(params.get("match", "")).lower()
    hits = [t for t in client.rows("transactions")
            if needle and needle in
            f"{t.get('description', '')} {t.get('merchant', '')}".lower()]
    hits.sort(key=lambda t: int(t.get("id") or 0))
    return hits[-1] if hits else None


def execute_action(client, action: dict) -> str:
    """Apply a confirmed action. Returns the user-facing result message."""
    kind = (action or {}).get("kind", "")
    params = (action or {}).get("params") or {}
    if kind == "add_transaction":
        raw = normalize({"date": params["date"],
                         "description": params["description"],
                         "amount": str(params["amount"])})
        unique, _ = dedupe([raw], client.rows("transactions"))
        if not unique:
            return ("That transaction is already in your ledger — "
                    "no duplicate added.")
        txn = unique[0]
        hit = learn_mod.apply_rules_for(client, txn["description"],
                                        txn.get("merchant", ""))
        if hit is None:
            hit = categorize(txn["description"], txn.get("merchant", ""))
        from datetime import datetime, timezone
        row = client.add("transactions", {
            "source_id": None, "date": txn["date"],
            "description": txn["description"],
            "merchant": txn.get("merchant", ""), "amount": txn["amount"],
            "currency": txn.get("currency", "INR"),
            "type": txn.get("type", "other"),
            "category": hit["category"],
            "origin": txn.get("origin", "verified"), "note": "",
            "hash": txn.get("hash", ""),
            "created_at": datetime.now(timezone.utc).isoformat()})
        return (f"Added transaction {row['id']}: {txn['date']} "
                f"'{txn['description']}' Rs {float(txn['amount']):+,.0f} "
                f"({hit['category']}).")
    if kind == "create_goal":
        gid = goals_mod.create_for(client, params["name"],
                                   float(params["target"]),
                                   deadline=params.get("deadline", ""))
        return (f"Created goal '{params['name']}' "
                f"(Rs {float(params['target']):,.0f}). Goal id {gid}.")
    if kind == "record_contribution":
        goal = _resolve_goal(client, str(params.get("goal", "")))
        if goal is None:
            raise ValueError(f"No goal named '{params.get('goal', '')}' yet — "
                             "create it first.")
        saved = goals_mod.record_contribution_for(
            client, int(goal["id"]), float(params["amount"]))
        return (f"Recorded Rs {float(params['amount']):,.0f} to "
                f"'{goal['name']}' (saved Rs {saved:,.0f} of "
                f"Rs {float(goal.get('target', 0)):,.0f}).")
    if kind == "recategorize":
        txn = _resolve_txn(client, params)
        if txn is None:
            raise ValueError("No matching transaction found.")
        category = params["category"]
        client.set("transactions", int(txn["id"]), {"category": category})
        learned = learn_mod.learn_rule(
            client, txn.get("merchant") or "", category,
            txn.get("description") or "")
        pats = ", ".join(w["pattern"] for w in learned) or "no new keywords"
        return (f"Transaction {txn['id']} set to {category}. "
                f"Learned: {pats}.")
    raise ValueError(f"Unknown action {kind!r}.")
