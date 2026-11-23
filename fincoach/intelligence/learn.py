"""Learn user corrections into category_rules; apply them to future txns."""

from __future__ import annotations

import re
import sqlite3

_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "ltd", "pvt", "pvt.", "inc",
    "payment", "paid", "via", "upi", "ltd.",
})


def _ensure_hits_column(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(category_rules)")}
    if "hits" not in cols:
        conn.execute("ALTER TABLE category_rules ADD COLUMN hits INTEGER NOT NULL DEFAULT 0")
        conn.commit()


def _keywords(merchant: str, description: str = "") -> list[str]:
    text = f"{merchant} {description}".lower()
    words = re.findall(r"[a-z0-9]{3,}", text)
    seen: list[str] = []
    for word in words:
        if word in _STOPWORDS or word.isdigit():
            continue
        if word not in seen:
            seen.append(word)
    return seen[:5]


def learn_from_correction(conn: sqlite3.Connection, merchant: str,
                          category: str, description: str = "") -> list[dict]:
    """Store one category_rules row per merchant keyword. Returns rows written.

    Re-learning the same pattern bumps its hit count instead of duplicating.
    """
    _ensure_hits_column(conn)
    written: list[dict] = []
    for keyword in _keywords(merchant, description):
        row = conn.execute(
            "SELECT id, hits FROM category_rules WHERE pattern = ? AND category = ?",
            (keyword, category),
        ).fetchone()
        if row is not None:
            conn.execute("UPDATE category_rules SET hits = hits + 1 WHERE id = ?",
                         (row["id"],))
            written.append({"pattern": keyword, "category": category,
                            "hits": row["hits"] + 1, "updated": True})
        else:
            cur = conn.execute(
                "INSERT INTO category_rules (pattern, category, priority, hits)"
                " VALUES (?, ?, ?, ?)",
                (keyword, category, 10, 1),
            )
            written.append({"pattern": keyword, "category": category,
                            "hits": 1, "updated": False, "id": cur.lastrowid})
    conn.commit()
    return written


def apply_rules(conn: sqlite3.Connection, description: str = "",
                merchant: str = "") -> dict | None:
    """Match learned rules against a txn. Returns {category, rule, confidence}."""
    _ensure_hits_column(conn)
    text = f"{description} {merchant}".strip().lower()
    if not text:
        return None
    rows = conn.execute(
        "SELECT pattern, category, priority, hits FROM category_rules"
        " ORDER BY priority DESC, hits DESC, id ASC"
    ).fetchall()
    for row in rows:
        if row["pattern"] and row["pattern"].lower() in text:
            confidence = "high" if (row["hits"] or 0) >= 3 else "medium"
            return {"category": row["category"], "rule": row["pattern"],
                    "confidence": confidence}
    return None


def _match_rules(rules: list[dict], description: str = "",
                 merchant: str = "") -> dict | None:
    text = f"{description} {merchant}".strip().lower()
    if not text:
        return None
    ordered = sorted(rules,
                     key=lambda r: (-int(r.get("priority") or 0),
                                    -int(r.get("hits") or 0),
                                    int(r.get("id") or 0)))
    for row in ordered:
        if row.get("pattern") and str(row["pattern"]).lower() in text:
            confidence = "high" if int(row.get("hits") or 0) >= 3 else "medium"
            return {"category": row["category"], "rule": row["pattern"],
                    "confidence": confidence}
    return None


def learn_rule(client, merchant: str, category: str,
               description: str = "") -> list[dict]:
    """Data-client version of learn_from_correction (Supabase or stub)."""
    written: list[dict] = []
    existing = {(str(r.get("pattern")), str(r.get("category"))): r
                for r in client.rows("category_rules")}
    for keyword in _keywords(merchant, description):
        row = existing.get((keyword, category))
        if row is not None:
            updated = client.set("category_rules", row["id"],
                                 {"hits": int(row.get("hits") or 0) + 1})
            written.append({"pattern": keyword, "category": category,
                            "hits": updated.get("hits", 0), "updated": True})
        else:
            new = client.add("category_rules", {
                "pattern": keyword, "category": category,
                "priority": 10, "hits": 1})
            written.append({"pattern": keyword, "category": category,
                            "hits": 1, "updated": False, "id": new["id"]})
            existing[(keyword, category)] = new
    return written


def apply_rules_for(client, description: str = "",
                    merchant: str = "") -> dict | None:
    """Data-client version of apply_rules (Supabase or stub)."""
    return _match_rules(client.rows("category_rules"), description, merchant)
