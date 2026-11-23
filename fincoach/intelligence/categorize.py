"""Deterministic keyword categorization. No LLM, no arithmetic guessing."""

from __future__ import annotations

import re

# Category -> keywords (lowercase substrings matched against description+merchant).
DEFAULT_RULES: dict[str, list[str]] = {
    "groceries": ["grocery", "groceries", "supermarket", "bigbasket", "blinkit",
                  "zepto", "dmart", "fresh", "vegetable", "kirana", "mart"],
    "dining": ["restaurant", "swiggy", "zomato", "cafe", "coffee", "pizza",
               "burger", "hotel", "food", "bakery", "dhaba", "eatery"],
    "rent": ["rent", "landlord", "housing", "flat rent", "house rent"],
    "utilities": ["electricity", "water bill", "gas bill", "broadband", "wifi",
                  "mobile recharge", "recharge", "utility", "bsnl", "jio", "airtel"],
    "transport": ["uber", "ola", "metro", "railway", "irctc", "petrol", "diesel",
                  "fuel", "toll", "parking", "flight", "indigo", "cab", "bus"],
    "shopping": ["amazon", "flipkart", "myntra", "mall", "retail", "store",
                 "clothing", "shoes", "electronics", "purchase"],
    "entertainment": ["cinema", "movie", "pvr", "inox", "netflix", "spotify",
                      "prime video", "hotstar", "concert", "game", "subscription"],
    "health": ["pharmacy", "medical", "hospital", "doctor", "clinic", "apollo",
               "lab", "diagnostic", "insurance hospital", "medicine"],
    "education": ["school", "college", "university", "course", "udemy",
                  "coursera", "tuition", "fees", "books", "exam"],
    "travel": ["hotel", "airbnb", "makemytrip", "goibibo", "yatra", "holiday",
               "vacation", "trip", "airline"],
    "emi": ["emi", "loan", "hdfc loan", "sbi loan", "icici loan", "car loan",
            "home loan", "personal loan", "repayment"],
    "insurance": ["lic", "insurance", "premium", "policy"],
    "investment": ["mutual fund", "sip", "zerodha", "groww", "upstox", "demat",
                   "ppf", "nps", "fixed deposit", "fd ", "stocks", "etf", "gold"],
    "salary": ["salary", "payroll", "wages", "employer"],
    "freelance": ["freelance", "upwork", "fiverr", "contract payment",
                  "consulting", "honorarium", "client payment"],
    "transfer": ["upi", "neft", "rtgs", "imps", "transfer", "atm", "cash"],
}

# Checked first so income/transfer keywords win over generic merchant words.
_PRIORITY_ORDER = ("salary", "freelance", "emi", "rent", "insurance",
                   "investment", "transfer")


def _ordered_categories() -> list[str]:
    rest = [c for c in DEFAULT_RULES if c not in _PRIORITY_ORDER]
    return list(_PRIORITY_ORDER) + rest


def categorize(description: str = "", merchant: str = "",
               amount: float = 0.0, txn_type: str = "") -> dict:
    """Return {category, rule, confidence} for one transaction.

    confidence: high (whole-word hit), medium (substring hit), low (fallback).
    """
    text = f"{description} {merchant}".strip().lower()
    best: tuple[str, str, str] | None = None
    for category in _ordered_categories():
        for keyword in DEFAULT_RULES[category]:
            if keyword in text:
                if re.search(r"\b" + re.escape(keyword) + r"\b", text):
                    return {"category": category, "rule": keyword,
                            "confidence": "high"}
                if best is None:
                    best = (category, keyword, "medium")
    if best is not None:
        category, keyword, conf = best
        return {"category": category, "rule": keyword, "confidence": conf}
    return {"category": "other", "rule": "fallback", "confidence": "low"}
