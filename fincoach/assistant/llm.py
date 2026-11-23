"""Gemini-powered assistant answers (stdlib-only HTTP, optional).

When GEMINI_API_KEY is set, the assistant sends the question plus a small
capped slice of the user's own data to Google Gemini and returns its
answer, labelled engine "gemini". Otherwise — no key, or any API error —
it falls back to the deterministic fincoach.assistant rule engine
(labelled "rules"), so the chat always works offline.

The context builder gathers only the slice relevant to the question:
profile snapshot, goals, recurring items, recent transactions, and — when
the question asks for them — a monthly review and/or a what-if
simulation. Everything is capped so the prompt stays small.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from fincoach.assistant import assistant as assistant_mod
from fincoach.intelligence import review as review_mod
from fincoach import safety
from fincoach.intelligence import simulate as simulate_mod
from fincoach.config import gemini_api_key, gemini_model

_TIMEOUT = 20
MAX_TXNS = 30
MAX_GOALS = 10
MAX_RECURRING = 10
MAX_MONTHS = 6
MAX_CONTEXT_CHARS = 6000


class LlmError(ValueError):
    """Gemini call failed (transport, auth, or unusable response)."""


def is_configured() -> bool:
    """True when a Gemini API key is available."""
    return bool(gemini_api_key())


def _goal_rows(goals: list[dict]) -> list[dict]:
    rows = []
    for g in (goals or [])[:MAX_GOALS]:
        prog = g.get("progress") or {}
        rows.append({
            "name": g.get("name"), "target": g.get("target"),
            "saved": prog.get("saved", g.get("current", 0)),
            "deadline": g.get("deadline", ""),
            "on_track": prog.get("on_track"),
            "required_monthly": prog.get("required_monthly"),
            "projected_date": prog.get("projected_date"),
        })
    return rows


def _recurring_rows(recurring: list[dict]) -> list[dict]:
    rows = []
    for r in recurring or []:
        if r.get("status") == "inactive":
            continue
        rows.append({
            "merchant": r.get("merchant"), "amount": r.get("amount"),
            "cadence": r.get("cadence", "monthly"),
            "next_date": r.get("next_date", ""),
            "status": r.get("status", "active"),
        })
        if len(rows) >= MAX_RECURRING:
            break
    return rows


def _txn_rows(txns: list[dict], limit: int) -> list[dict]:
    rows = []
    for t in (txns or [])[:limit]:
        rows.append({
            "date": t.get("date"), "description": t.get("description"),
            "merchant": t.get("merchant", ""),
            "amount": t.get("amount"), "category": t.get("category", ""),
        })
    return rows


def build_context(question: str, ctx: dict,
                  max_txns: int = MAX_TXNS) -> dict:
    """Relevant slice of the user's data for one question (capped).

    Always includes the profile snapshot, goals, recurring items, and
    recent transactions; adds a `review` block when the question names a
    month and a `simulation` block when it carries what-if deltas.
    """
    low = (question or "").lower()
    profile = ctx.get("profile") or {}
    goals = ctx.get("goals") or []
    rec = ctx.get("recurring") or []
    txns = ctx.get("txns") or []
    months = (ctx.get("months") or [])[-MAX_MONTHS:]

    context: dict = {
        "profile": {
            k: profile.get(k) for k in (
                "avg_monthly_income", "avg_monthly_expenses", "savings",
                "savings_rate", "fixed_expenses", "variable_expenses",
                "trend", "months_used", "top_categories",
                "spend_by_category")},
        "goals": _goal_rows(goals),
        "recurring": _recurring_rows(rec),
        "months": months,
        "transactions": _txn_rows(txns, max_txns),
    }

    if re.search(r"\breview\b|\bmonth\b", low):
        m = re.search(r"(20\d\d-[01]\d)", low)
        month = m.group(1) if m else (months[-1] if months else "")
        if month:
            grows = [{"name": g.get("name"),
                      "progress": g.get("progress", {})} for g in goals]
            data = review_mod.monthly_review(txns, month, rec, grows)
            data.pop("llm_prompt", None)
            context["review"] = data

    deltas = assistant_mod._parse_scenario(low)
    if deltas:
        context["simulation"] = simulate_mod.run_scenario(
            profile, goals=goals, **deltas)

    # Shrink the transaction slice until the whole context fits the cap.
    limit = max_txns
    while len(json.dumps(context, default=str)) > MAX_CONTEXT_CHARS and limit > 0:
        limit //= 2
        context["transactions"] = _txn_rows(txns, limit)
    return context


def build_prompt(question: str, context: dict,
                 history: list[dict] | None = None) -> str:
    """System instructions + context JSON + recent chat + the question."""
    lines = [
        "You are FinCoach, a friendly money coach. Answer ONLY from the",
        "context below, which holds the user's own figures. Show every",
        "figure with its assumption, format rupees like 'Rs 15,002', and",
        "never recommend stocks or buy/sell actions. Keep it short.",
        "",
        "Context (JSON):",
        json.dumps(context, default=str)[:MAX_CONTEXT_CHARS],
    ]
    for turn in (history or [])[-6:]:
        role = "User" if turn.get("role") == "user" else "Assistant"
        lines.append(f"{role}: {str(turn.get('content', ''))[:500]}")
    lines += ["", f"Question: {(question or '').strip()[:500]}"]
    return "\n".join(lines)


def call_gemini(prompt: str, api_key: str = "", model: str = "",
                timeout: int = _TIMEOUT, urlopen=None) -> str:
    """One generateContent call. Returns the answer text or raises LlmError."""
    key = (api_key or gemini_api_key()).strip()
    if not key:
        raise LlmError("GEMINI_API_KEY is not set.")
    name = (model or gemini_model()).strip()
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{urllib.parse.quote(name, safe='')}:generateContent"
           f"?key={urllib.parse.quote(key, safe='')}")
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    opener = urlopen or urllib.request.urlopen
    try:
        with opener(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        raise LlmError(f"Gemini request failed (HTTP {exc.code}).")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LlmError(f"Could not reach Gemini ({exc}).")
    except ValueError:
        raise LlmError("Gemini returned an unreadable response.")
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        text = ""
    if not text:
        raise LlmError("Gemini returned an empty answer.")
    return text


def answer_with_engine(question: str, ctx: dict,
                       history: list[dict] | None = None,
                       call_fn=None, api_key: str | None = None,
                       model: str | None = None) -> tuple[str, str]:
    """Answer a question, returning (text, engine).

    engine is "gemini" for live answers and "rules" for the deterministic
    fallback. Securities-advice questions are refused without ever
    calling the model. call_fn(prompt, context) stubs the network in
    tests; any exception from it (or the real call) falls back to rules.
    """
    allowed, refusal = safety.guard(question or "")
    if not allowed:
        return refusal or safety.REFUSAL, "rules"
    key = gemini_api_key() if api_key is None else (api_key or "").strip()
    if not key and call_fn is None:
        return assistant_mod.answer(question, ctx), "rules"
    context = build_context(question, ctx)
    prompt = build_prompt(question, context, history)
    try:
        if call_fn is not None:
            text = call_fn(prompt, context)
        else:
            text = call_gemini(prompt, api_key=key,
                               model=model or gemini_model())
    except Exception:
        return assistant_mod.answer(question, ctx), "rules"
    text = (text or "").strip()
    if not text:
        return assistant_mod.answer(question, ctx), "rules"
    return text, "gemini"
