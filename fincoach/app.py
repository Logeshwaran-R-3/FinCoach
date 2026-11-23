"""FinCoach Flask app: dashboard, sources, transactions, recurring, goals, simulate, review."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone

from flask import Flask, Response, flash, g, jsonify, redirect, render_template, request, session, url_for

from fincoach.assistant import assistant as assistant_mod
from fincoach.storage import supabase_auth as sb_auth
from fincoach.assistant import chat as chat_mod
from fincoach.assistant import chat_actions as chat_actions_mod
from fincoach.intelligence import goals, learn
from fincoach.services import mailer
from fincoach.storage import sb_data, seed as seed_mod, source_repo
from fincoach.assistant import llm as llm_mod
from fincoach.intelligence import nudges as nudges_mod
from fincoach.intelligence import profile as profile_mod
from fincoach.intelligence import recurring as recurring_mod
from fincoach.intelligence import reminders as reminders_mod
from fincoach.intelligence import review as review_mod
from fincoach.intelligence import simulate as simulate_mod
from fincoach.config import HELP_EMAIL
from fincoach.intelligence.categorize import DEFAULT_RULES, categorize
from fincoach.ingest import run_ingest
from fincoach.ingest.dedupe import dedupe
from fincoach.ingest.errors import IngestError
from fincoach.ingest.manual import manual_txn
from fincoach.ingest.normalize import normalize
from fincoach.ingest.validate import validate
from fincoach.safety import ORIGIN_LABELS, get_disclaimer, guard

CATEGORIES = sorted(set(DEFAULT_RULES) | {"other"})
FILE_KINDS = ("pdf", "docx", "csv", "xlsx", "image")

app = Flask(__name__, template_folder="frontend/templates",
            static_folder="frontend/static")
app.secret_key = os.environ.get("SECRET_KEY", "fincoach-local-dev")

_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@app.template_filter("inr")
def _inr(value) -> str:
    """Format a number as Indian rupees, e.g. 15002 -> 'Rs 15,002'."""
    try:
        return f"Rs {float(value):,.0f}"
    except (TypeError, ValueError):
        return "Rs 0"


@app.template_filter("inr_exact")
def _inr_exact(value) -> str:
    try:
        return f"Rs {float(value):,.2f}"
    except (TypeError, ValueError):
        return "Rs 0.00"


@app.template_filter("inr_signed")
def _inr_signed(value) -> str:
    """Signed rupees for deltas, e.g. 'Rs +1,650' or 'Rs -2,800'."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "Rs 0"
    sign = "+" if v > 0 else ("-" if v < 0 else "")
    return f"Rs {sign}{abs(v):,.0f}"


@app.template_filter("monthname")
def _monthname(value) -> str:
    """'2026-11' -> 'Nov 2026' (passes anything else through)."""
    try:
        year, month = str(value).split("-")[:2]
        return f"{_MONTH_NAMES[int(month) - 1]} {year}"
    except (ValueError, IndexError):
        return str(value)


@app.context_processor
def _inject_safety():
    return {"disclaimer": get_disclaimer(), "origin_labels": ORIGIN_LABELS}


_OPEN_ENDPOINTS = {"health", "login", "signup", "static"}


def _sb_client():
    """Supabase client: test stub when configured, else real env client."""
    configured = app.config.get("SUPABASE_CLIENT")
    if configured is not None:
        return configured
    return sb_auth.client_from_env()


def _user_dict(user: dict) -> dict:
    """Request-local user: id, email, and display name from user_metadata."""
    user = user or {}
    return {"id": user.get("id", ""), "email": user.get("email", ""),
            "name": sb_auth.display_name(user)}


def _store_sb_session(session_data: dict, user: dict) -> None:
    session["sb_access"] = session_data.get("access_token", "")
    session["sb_refresh"] = session_data.get("refresh_token", "")
    session["sb_email"] = user.get("email", "")
    session["sb_uid"] = user.get("id", "")
    session["sb_name"] = sb_auth.display_name(user)


def _clear_sb_session() -> None:
    for key in ("sb_access", "sb_refresh", "sb_email", "sb_uid", "sb_name"):
        session.pop(key, None)


def _resolve_user():
    token = session.get("sb_access")
    if not token:
        return None
    try:
        client = _sb_client()
    except sb_auth.ConfigError:
        return None
    try:
        user = client.get_user(token)
        return _user_dict(user)
    except sb_auth.Unauthorized:
        pass  # try the refresh token below
    except sb_auth.AuthError:
        return None  # transport error: fail closed, keep tokens for retry
    refresh = session.get("sb_refresh")
    if not refresh:
        _clear_sb_session()
        return None
    try:
        data = client.refresh_session(refresh)
        user = data.get("user") or {}
        if not user.get("id"):
            user = client.get_user(data["session"]["access_token"])
        _store_sb_session(data["session"], user)
        return _user_dict(user)
    except sb_auth.AuthError:
        _clear_sb_session()
        return None


def _current_user():
    if not hasattr(g, "_sb_user"):
        g._sb_user = _resolve_user()
    return g._sb_user


def _uid() -> str:
    """Id of the logged-in user; owns this request's rows and files.

    Every protected route serves one user's Supabase rows (RLS-scoped),
    so each account starts empty and stays isolated from others.
    """
    user = _current_user() or {}
    return user.get("id") or user.get("email") or "anon"


def _token() -> str | None:
    return session.get("sb_access")


def _data():
    """Data client for the current user (Supabase, or the offline stub
    in tests via app.config["DATA_CLIENT"] / FINCOACH_DATA)."""
    configured = app.config.get("DATA_CLIENT")
    if configured is not None:
        if callable(configured):
            return configured(_uid(), _token())
        return configured
    return sb_data.get_client(_uid(), _token())


@app.context_processor
def _inject_user():
    return {"user": _current_user()}


@app.before_request
def _require_login():
    if request.endpoint in _OPEN_ENDPOINTS or request.endpoint is None:
        return None
    if _current_user() is None:
        return redirect(url_for("login", next=request.path))
    return None


def _active_txns(client) -> list[dict]:
    """This user's transactions, excluding archived sources' transactions.

    Every page's calculations build on this helper, so archiving a source
    removes its figures from the dashboard, transactions, recurring, goals,
    simulator, review, reminders, and assistant until it is restored.
    """
    archived = {s["id"] for s in client.rows("sources")
                if s.get("status") == "archived"}
    return [t for t in client.rows("transactions")
            if t.get("source_id") not in archived]


def _txns(client) -> list[dict]:
    by_source = {s["id"]: s for s in client.rows("sources")}
    txns = []
    for txn in _active_txns(client):
        src = by_source.get(txn.get("source_id")) or {}
        txn["source_name"] = src.get("filename", "")
        txn["source_kind"] = src.get("kind", "")
        txns.append(txn)
    txns.sort(key=lambda t: (str(t.get("date", "")),
                             int(t.get("id") or 0)), reverse=True)
    return txns


def _goals_with_progress(client, monthly_savings: float) -> list[dict]:
    out = []
    for goal in client.rows("goals"):
        goal["progress"] = goals.goal_progress(goal, monthly_savings=monthly_savings)
        out.append(goal)
    return out


def _categorize_new(client) -> int:
    n = 0
    for row in client.rows("transactions"):
        if (row.get("category") or "") != "":
            continue
        hit = learn.apply_rules_for(client, row["description"],
                                    row.get("merchant", ""))
        if hit is None:
            hit = categorize(row["description"], row.get("merchant", ""))
        client.set("transactions", row["id"], {"category": hit["category"]})
        n += 1
    return n


def _monthly_series(txns: list[dict]) -> list[dict]:
    """Per-month income/expenses/savings for charts, oldest first."""
    income: dict[str, float] = {}
    expenses: dict[str, float] = {}
    for t in txns:
        month = str(t.get("date", ""))[:7]
        if not month:
            continue
        amt = float(t["amount"])
        if amt > 0:
            income[month] = income.get(month, 0.0) + amt
        elif amt < 0:
            expenses[month] = expenses.get(month, 0.0) - amt
    out = []
    for month in sorted(set(income) | set(expenses)):
        inc = round(income.get(month, 0.0), 2)
        exp = round(expenses.get(month, 0.0), 2)
        out.append({"month": month, "income": inc, "expenses": exp,
                    "savings": round(inc - exp, 2)})
    return out


def _dashboard_data() -> dict:
    client = _data()
    txns = _txns(client)
    rec = recurring_mod.detect(txns)
    prof = profile_mod.build_profile(txns, rec)
    profile_mod.save_profile_for(client, prof)
    goals_list = _goals_with_progress(client, prof["savings"])
    months = sorted({str(t["date"])[:7] for t in txns if t.get("date")})
    latest = months[-1] if months else ""
    by_cat: dict[str, float] = {}
    for t in txns:
        if str(t.get("date", ""))[:7] == latest and float(t["amount"]) < 0:
            by_cat[t.get("category") or "other"] = (
                by_cat.get(t.get("category") or "other", 0.0) - float(t["amount"]))
    nudge_goals = [{"name": g["name"], "progress": g["progress"]} for g in goals_list]
    nudges = nudges_mod.generate_nudges(prof, rec, nudge_goals, {"by_category": by_cat})
    monthly = _monthly_series(txns)
    cat_sorted = sorted(({"category": c, "total": round(v, 2)}
                         for c, v in by_cat.items()),
                        key=lambda r: -r["total"])
    return {"profile": prof, "recurring": rec, "goals": goals_list,
            "nudges": nudges, "txn_count": len(txns), "latest_month": latest,
            "monthly": monthly, "by_category": cat_sorted,
            "recent": txns[:8]}


@app.route("/")
def dashboard():
    data = _dashboard_data()
    return render_template("dashboard.html", **data)


@app.route("/sources")
def sources():
    q = request.args.get("q", "").strip().lower()
    items = source_repo.list(_uid(), access_token=_token())
    if q:
        items = [s for s in items
                 if q in f"{s.filename} {s.kind} {s.status}".lower()]
    active = [s for s in items if s.status != "archived"]
    archived = [s for s in items if s.status == "archived"]
    return render_template("sources.html", sources=active, archived=archived,
                           kinds=source_repo.KINDS, q=request.args.get("q", ""))


@app.route("/sources/upload", methods=["POST"])
def upload():
    kind = request.form.get("kind", "csv")
    if kind not in source_repo.KINDS:
        flash(f"Unknown kind {kind!r}.", "error")
        return redirect(url_for("sources"))
    try:
        if kind in FILE_KINDS:
            f = request.files.get("file")
            if f is None or not f.filename:
                flash("Choose a file to upload.", "error")
                return redirect(url_for("sources"))
            src = source_repo.add(_uid(), kind, f.filename, f.read(),
                                  mime=f.mimetype or "",
                                  access_token=_token())
        elif kind == "sms_text":
            text = request.form.get("sms_text", "").strip()
            if not text:
                flash("Paste SMS text first.", "error")
                return redirect(url_for("sources"))
            src = source_repo.add(_uid(), kind, "sms.txt",
                                  text.encode("utf-8"), mime="text/plain",
                                  access_token=_token())
        else:  # manual
            raw = manual_txn({k: request.form.get(k, "")
                              for k in ("date", "description", "amount")})
            label = f"{raw[0]['date']} {raw[0]['description']} {raw[0]['amount']}"
            src = source_repo.add(_uid(), kind, "manual.txt",
                                  label.encode("utf-8"), mime="text/plain",
                                  access_token=_token())
            normed = validate([normalize(r) for r in raw])[0]
            client = _data()
            unique, _ = dedupe(normed, client.rows("transactions"))
            created = datetime.now(timezone.utc).isoformat()
            client.add_many("transactions", [{
                "source_id": src.id, "date": txn["date"],
                "description": txn["description"],
                "merchant": txn.get("merchant", ""),
                "amount": txn["amount"],
                "currency": txn.get("currency", "INR"),
                "type": txn.get("type", "other"), "category": "",
                "origin": txn.get("origin", "verified"), "note": "",
                "hash": txn.get("hash", ""), "created_at": created,
            } for txn in unique])
            source_repo.set_status(_uid(), src.id, "done",
                                   txn_count=len(unique),
                                   access_token=_token())
            _categorize_new(client)
            flash(f"Manual entry saved ({len(unique)} transaction).", "ok")
            return redirect(url_for("sources"))
    except (IngestError, ValueError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("sources"))
    if request.form.get("ingest_now", "on") == "on":
        try:
            result = run_ingest(_uid(), src.id, access_token=_token())
            _categorize_new(_data())
            flash(f"Ingested {result['inserted']} transactions"
                  f" ({result['duplicates']} duplicates skipped).", "ok")
        except IngestError as exc:
            flash(f"Ingest failed: {exc}", "error")
    else:
        flash(f"Uploaded {src.filename}; ingest pending.", "ok")
    return redirect(url_for("sources"))


@app.route("/sources/<int:source_id>/ingest", methods=["POST"])
def ingest_source(source_id: int):
    try:
        result = run_ingest(_uid(), source_id, access_token=_token())
        _categorize_new(_data())
        flash(f"Ingested {result['inserted']} transactions.", "ok")
    except (IngestError, KeyError) as exc:
        flash(f"Ingest failed: {exc}", "error")
    return redirect(url_for("sources"))


@app.route("/transactions")
def transactions():
    category = request.args.get("category", "")
    q = request.args.get("q", "").strip().lower()
    client = _data()
    txns = _txns(client)
    try:
        rules = sorted(client.rows("category_rules"),
                       key=lambda r: (-int(r.get("hits") or 0),
                                      str(r.get("pattern", ""))))[:12]
        rules = [{"pattern": r.get("pattern", ""),
                  "category": r.get("category", ""),
                  "priority": r.get("priority", 0),
                  "hits": r.get("hits", 0)} for r in rules]
    except sb_data.SupabaseDataError:
        rules = []
    if category:
        txns = [t for t in txns if t.get("category") == category]
    if q:
        txns = [t for t in txns
                if q in f"{t.get('description', '')} {t.get('merchant', '')} "
                   f"{t.get('amount', '')} {t.get('category', '')}".lower()]
    used = sorted({t.get("category") or "other" for t in _all_txns_cached(txns)})
    origins = sorted({t.get("origin") or "verified" for t in txns})
    return render_template("transactions.html", transactions=txns,
                           categories=CATEGORIES, active=category, used=used,
                           rules=rules, origins=origins,
                           q=request.args.get("q", ""))


def _all_txns_cached(txns: list[dict]) -> list[dict]:
    return txns


@app.route("/transactions/<int:txn_id>/category", methods=["POST"])
def correct_category(txn_id: int):
    category = request.form.get("category", "").strip()
    if category not in CATEGORIES:
        flash(f"Unknown category {category!r}.", "error")
        return redirect(url_for("transactions"))
    client = _data()
    row = client.get("transactions", txn_id)
    if row is None:
        flash(f"No transaction {txn_id}.", "error")
        return redirect(url_for("transactions"))
    client.set("transactions", txn_id, {"category": category})
    learned = learn.learn_rule(
        client, row.get("merchant") or "", category,
        row.get("description") or "")
    pats = ", ".join(w["pattern"] for w in learned) or "no new keywords"
    flash(f"Transaction {txn_id} set to {category}. Learned: {pats}.", "ok")
    return redirect(url_for("transactions"))


@app.route("/recurring")
def recurring():
    txns = _active_txns(_data())
    items = recurring_mod.detect(txns)
    divisor = {"monthly": 1, "quarterly": 3, "yearly": 12}
    monthly_cost = sum(float(i["amount"]) / divisor.get(i.get("cadence", "monthly"), 1)
                       for i in items
                       if i.get("status") != "inactive"
                       and i.get("direction", "expense") == "expense")
    counts = {"active": 0, "changed": 0, "missing": 0, "inactive": 0}
    for i in items:
        counts[i.get("status", "active")] = counts.get(i.get("status"), 0) + 1
    flagged = [i for i in items if i.get("status") in ("changed", "missing")]
    return render_template("recurring.html", items=items,
                           monthly_cost=round(monthly_cost, 2),
                           counts=counts, flagged=flagged)


@app.route("/goals", methods=["GET", "POST"])
def goals_page():
    client = _data()
    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "create":
            try:
                goals.create_for(client, request.form.get("name", "goal").strip(),
                                 float(request.form.get("target", 0)),
                                 deadline=request.form.get("deadline", "").strip(),
                                 current=float(request.form.get("current", 0) or 0))
                flash("Goal created.", "ok")
            except ValueError as exc:
                flash(str(exc), "error")
        elif action == "contribute":
            try:
                goals.record_contribution_for(client, int(request.form.get("goal_id", 0)),
                                              float(request.form.get("amount", 0)))
                flash("Contribution recorded.", "ok")
            except ValueError as exc:
                flash(str(exc), "error")
        elif action == "delete":
            client.remove("goals", int(request.form.get("goal_id", 0)))
            flash("Goal deleted.", "ok")
        return redirect(url_for("goals_page"))
    txns = _active_txns(client)
    rec = recurring_mod.detect(txns)
    prof = profile_mod.build_profile(txns, rec)
    goals_list = _goals_with_progress(client, prof["savings"])
    savings = prof["savings"]
    return render_template("goals.html", goals=goals_list,
                           monthly_savings=savings)


@app.route("/simulate", methods=["GET", "POST"])
def simulate():
    client = _data()
    txns = _active_txns(client)
    rec = recurring_mod.detect(txns)
    prof = profile_mod.build_profile(txns, rec)
    goals_list = client.rows("goals")

    def num(name: str) -> float:
        try:
            return float(request.form.get(name, 0) or 0)
        except ValueError:
            return 0.0

    result = None
    inputs = {"dining_pct": 0.0, "rent_delta": 0.0, "income_delta": 0.0,
              "savings_delta": 0.0}
    if request.method == "POST":
        inputs = {k: num(k) for k in inputs}
        result = simulate_mod.run_scenario(prof, goals=goals_list, **inputs)
    return render_template("simulate.html", profile=prof, result=result,
                           inputs=inputs, goals=goals_list)


@app.route("/review")
def review():
    month = request.args.get("month", "")
    client = _data()
    txns = _active_txns(client)
    rec = recurring_mod.detect(txns)
    prof = profile_mod.build_profile(txns, rec)
    goals_list = _goals_with_progress(client, prof["savings"])
    months = sorted({str(t["date"])[:7] for t in txns if t.get("date")}, reverse=True)
    if not month and months:
        month = months[0]
    nudge_goals = [{"name": g["name"], "progress": g["progress"]} for g in goals_list]
    data = review_mod.monthly_review(txns, month, rec, nudge_goals) if month else None
    prev = None
    if month and month in months:
        older = sorted(m for m in months if m < month)
        if older:
            prev = review_mod.monthly_review(txns, older[-1], rec, nudge_goals)
    # Custom timeline viewer: a month range with per-month rows + totals.
    start = request.args.get("start", "").strip()[:7]
    end = request.args.get("end", "").strip()[:7]
    range_months: list[str] = []
    if (start or end) and months:
        ordered = sorted(months)
        lo, hi = start or ordered[0], end or ordered[-1]
        if lo > hi:
            lo, hi = hi, lo
        range_months = [m for m in ordered if lo <= m <= hi]
    range_rows = [review_mod.monthly_review(txns, m, rec, nudge_goals)
                  for m in range_months]
    range_totals = {"income": round(sum(r["income"] for r in range_rows), 2),
                    "expenses": round(sum(r["expenses"] for r in range_rows), 2),
                    "savings": round(sum(r["savings"] for r in range_rows), 2)}
    return render_template("review.html", review=data, months=months,
                           active_month=month, prev=prev,
                           monthly=_monthly_series(txns),
                           range_rows=range_rows, range_totals=range_totals,
                           range_start=start, range_end=end)


@app.route("/review/report.csv")
def review_csv():
    """Downloadable per-month report (CSV) for a month range."""
    import csv
    import io

    client = _data()
    txns = _active_txns(client)
    rec = recurring_mod.detect(txns)
    prof = profile_mod.build_profile(txns, rec)
    goals_list = _goals_with_progress(client, prof["savings"])
    nudge_goals = [{"name": g["name"], "progress": g["progress"]}
                   for g in goals_list]
    months = sorted({str(t["date"])[:7] for t in txns if t.get("date")})
    start = request.args.get("start", "").strip()[:7] or (months[0] if months else "")
    end = request.args.get("end", "").strip()[:7] or (months[-1] if months else "")
    if start and end and start > end:
        start, end = end, start
    picked = [m for m in months if (not start or m >= start)
              and (not end or m <= end)]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["month", "income", "expenses", "savings",
                     "savings_rate_pct", "txn_count"])
    for m in picked:
        r = review_mod.monthly_review(txns, m, rec, nudge_goals)
        writer.writerow([m, f"{r['income']:.2f}", f"{r['expenses']:.2f}",
                         f"{r['savings']:.2f}", f"{r['savings_rate']:.1f}",
                         r["data_quality"]["txn_count"]])
    buf.write("\n")
    writer.writerow(["month", "category", "spending"])
    for m in picked:
        r = review_mod.monthly_review(txns, m, rec, nudge_goals)
        for cat, total in sorted(r["by_category"].items()):
            writer.writerow([m, cat, f"{total:.2f}"])
    name = f"fincoach-report-{start or 'all'}-to-{end or 'all'}.csv"
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":
                             f"attachment; filename={name}"})


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/simulate", methods=["POST"])
def api_simulate():
    """Live what-if endpoint for the simulator sliders (same math as the page)."""
    payload = request.get_json(silent=True) or {}

    def num(name: str) -> float:
        try:
            return float(payload.get(name, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    client = _data()
    txns = _active_txns(client)
    rec = recurring_mod.detect(txns)
    prof = profile_mod.build_profile(txns, rec)
    goals_list = client.rows("goals")
    result = simulate_mod.run_scenario(
        prof, goals=goals_list, dining_pct=num("dining_pct"),
        rent_delta=num("rent_delta"), income_delta=num("income_delta"),
        savings_delta=num("savings_delta"))
    return jsonify(result)


@app.route("/demo/load", methods=["POST"])
def load_demo():
    """Load the demo dataset into the current user's own data.

    New accounts start empty; this is an optional tour of the dashboard
    with sample figures. Idempotent: re-running only refreshes the demo.
    """
    summary = seed_mod.seed(_uid(), access_token=_token())
    flash(f"Demo data loaded ({summary['counts']['transactions']} "
          "transactions). Upload your own statements any time.", "ok")
    return redirect(url_for("dashboard"))


@app.route("/sources/<int:source_id>/delete", methods=["POST"])
def delete_source(source_id: int):
    try:
        unlinked = source_repo.delete(_uid(), source_id, access_token=_token())
        flash(f"Source {source_id} deleted from the database "
              f"({unlinked} extracted transaction(s) kept, unlinked).", "ok")
    except KeyError:
        flash(f"No source {source_id}.", "error")
    except (OSError, sb_data.SupabaseDataError) as exc:
        flash(f"Delete failed: {exc}", "error")
    return redirect(url_for("sources"))


@app.route("/sources/<int:source_id>/archive", methods=["POST"])
def archive_source(source_id: int):
    try:
        source_repo.archive(_uid(), source_id, access_token=_token())
        flash(f"Source {source_id} archived. Its transactions are excluded "
              "from all calculations until you restore it.", "ok")
    except KeyError:
        flash(f"No source {source_id}.", "error")
    except (OSError, sb_data.SupabaseDataError) as exc:
        flash(f"Archive failed: {exc}", "error")
    return redirect(url_for("sources"))


@app.route("/sources/<int:source_id>/restore", methods=["POST"])
def restore_source(source_id: int):
    try:
        source_repo.restore(_uid(), source_id, access_token=_token())
        flash(f"Source {source_id} restored to Sources.", "ok")
    except KeyError:
        flash(f"No source {source_id}.", "error")
    except (OSError, sb_data.SupabaseDataError) as exc:
        flash(f"Restore failed: {exc}", "error")
    return redirect(url_for("sources"))


@app.route("/help")
def help_page():
    faqs = [
        ("Where does my data live?",
         "In your own Supabase project (Postgres + Storage), isolated per "
         "account by Row Level Security. Your original files are kept "
         "byte-for-byte so every figure stays auditable."),
        ("How do I add statements?",
         "Open Sources and upload a PDF, spreadsheet, screenshot, or Word "
         "file; paste bank SMS text; or add a manual entry. Ingest runs "
         "automatically unless you uncheck 'Ingest immediately'."),
        ("Why do some numbers carry badges?",
         "Every figure is labeled verified (from your statements), "
         "estimated, inferred (FinCoach rules, e.g. recurring detection), "
         "or projected (what-if scenarios). Projected numbers are "
         "scenarios, not predictions."),
        ("What is the difference between Delete and Archive?",
         "Archive hides a source's transactions from every calculation "
         "without deleting anything — restore it any time from Archived "
         "Sources. Delete removes the source row and its file from the "
         "database; extracted transactions stay in your ledger unlinked."),
        ("How do bill reminders work?",
         "FinCoach auto-detects regular bills from merchants repeating "
         "across 3+ months. Add a manual reminder (or adopt a detected "
         "bill) with a 1-day, 2-day, or 1-week lead, then send due "
         "reminders by email from the Reminders page."),
        ("Does FinCoach give investment advice?",
         "No. FinCoach shows spending patterns and savings projections "
         "for education only, and refuses stock or buy/sell questions. "
         "Consult a qualified professional before making decisions."),
    ]
    return render_template("help.html", faqs=faqs, help_email=HELP_EMAIL)


def _safe_next(value: str | None) -> str | None:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        try:
            client = _sb_client()
        except sb_auth.ConfigError as exc:
            flash(str(exc), "error")
            return render_template("login.html",
                                   next=request.form.get("next", ""))
        try:
            data = client.sign_in(request.form.get("email", ""),
                                  request.form.get("password", ""))
        except sb_auth.Unauthorized as exc:
            if "confirm" in str(exc).lower():
                flash("Please confirm your email first, then log in.", "error")
            else:
                flash("Wrong email or password.", "error")
            return render_template("login.html",
                                   next=request.form.get("next", ""))
        except sb_auth.AuthError as exc:
            flash(str(exc), "error")
            return render_template("login.html",
                                   next=request.form.get("next", ""))
        user = data.get("user") or {}
        _store_sb_session(data["session"], user)
        flash(f"Welcome back, {user.get('email', '')}.", "ok")
        return redirect(_safe_next(request.form.get("next"))
                        or url_for("dashboard"))
    if _current_user() is not None:
        return redirect(url_for("dashboard"))
    return render_template("login.html", next=request.args.get("next", ""))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        try:
            client = _sb_client()
        except sb_auth.ConfigError as exc:
            flash(str(exc), "error")
            return render_template("signup.html")
        try:
            data = client.sign_up(request.form.get("email", ""),
                                  request.form.get("password", ""),
                                  name=request.form.get("name", ""),
                                  phone=request.form.get("phone", ""),
                                  currency=request.form.get("currency", ""))
        except sb_auth.AuthError as exc:
            flash(str(exc), "error")
            return render_template("signup.html")
        if data.get("session"):
            _store_sb_session(data["session"], data.get("user") or {})
            flash("Account created. Welcome to FinCoach.", "ok")
            return redirect(url_for("dashboard"))
        flash("Account created. Check your email to confirm it, then log in.",
              "ok")
        return redirect(url_for("login"))
    if _current_user() is not None:
        return redirect(url_for("dashboard"))
    return render_template("signup.html")


@app.route("/logout", methods=["POST"])
def logout():
    token = session.get("sb_access")
    if token:
        try:
            _sb_client().sign_out(token)
        except sb_auth.AuthError:
            pass
    _clear_sb_session()
    flash("Logged out.", "ok")
    return redirect(url_for("login"))


@app.route("/profile", methods=["GET", "POST"])
def profile_page():
    """Account profile: identity, account facts, data overview, edit name."""
    client = _sb_client()
    token = _token() or ""
    if request.method == "POST":
        try:
            updated = client.update_user(
                token, request.form.get("name", ""),
                request.form.get("phone", ""),
                request.form.get("currency", ""))
        except sb_auth.AuthError as exc:
            flash(str(exc), "error")
            return redirect(url_for("profile_page"))
        session["sb_name"] = sb_auth.display_name(updated)
        if hasattr(g, "_sb_user"):
            delattr(g, "_sb_user")
        flash("Profile updated.", "ok")
        return redirect(url_for("profile_page"))
    try:
        full_user = client.get_user(token)
    except sb_auth.AuthError:
        full_user = {}
    details = sb_auth.user_details(full_user)
    data = _data()
    counts = {
        "sources": sum(1 for s in data.rows("sources")
                       if s.get("status") != "archived"),
        "transactions": len(_active_txns(data)),
        "goals": len(data.rows("goals")),
        "reminders": len(data.rows("reminders")),
        "chats": len(data.rows("chat_sessions")),
    }
    return render_template("profile.html", details=details, counts=counts)


@app.route("/reminders", methods=["GET", "POST"])
def reminders_page():
    client = _data()
    if request.method == "POST":
        action = request.form.get("action", "create")
        try:
            if action == "create":
                reminders_mod.create_for(
                    client, request.form.get("text", ""),
                    request.form.get("amount", "0"),
                    request.form.get("due_date", ""),
                    lead=request.form.get("lead", "1day"))
                flash("Reminder saved.", "ok")
            elif action == "from_bill":
                reminders_mod.create_for(
                    client, request.form.get("merchant", ""),
                    request.form.get("amount", "0"),
                    request.form.get("due_date", ""),
                    lead=request.form.get("lead", "1day"), kind="bill")
                flash("Bill reminder saved.", "ok")
            elif action == "done":
                reminders_mod.mark_done_for(
                    client, int(request.form.get("reminder_id", 0)),
                    done=request.form.get("done", "1") == "1")
                flash("Reminder updated.", "ok")
            elif action == "delete":
                reminders_mod.delete_for(
                    client, int(request.form.get("reminder_id", 0)))
                flash("Reminder deleted.", "ok")
            else:
                flash(f"Unknown action {action!r}.", "error")
        except (ValueError, sb_data.SupabaseDataError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("reminders_page"))
    txns = _active_txns(client)
    bills = reminders_mod.auto_detect(txns)
    stored = reminders_mod.list_for(client)
    due = reminders_mod.due_reminders(stored)
    return render_template("reminders.html", bills=bills, stored=stored,
                           due=due, leads=reminders_mod.LEAD_LABELS,
                           mail_configured=mailer.is_configured(),
                           capture_mode=app.config.get("MAIL_CAPTURE")
                           is not None)


@app.route("/reminders/send", methods=["POST"])
def reminders_send():
    client = _data()
    user = _current_user() or {}
    stored = reminders_mod.list_for(client)
    due = reminders_mod.due_reminders(stored)
    if not due:
        flash("Nothing due right now — no email sent.", "ok")
        return redirect(url_for("reminders_page"))
    capture = app.config.get("MAIL_CAPTURE")
    try:
        mailer.send_due_digest(user.get("email", ""), due, capture=capture)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("reminders_page"))
    if capture is not None:
        flash(f"Test mode: {len(due)} reminder(s) captured, none sent.", "ok")
    else:
        flash(f"Emailed {len(due)} due reminder(s) to "
              f"{user.get('email', '')}.", "ok")
    return redirect(url_for("reminders_page"))


def _assistant_ctx(client) -> dict:
    txns = _active_txns(client)
    rec = recurring_mod.detect(txns)
    prof = profile_mod.build_profile(txns, rec)
    return {"profile": prof, "recurring": rec,
            "goals": _goals_with_progress(client, prof["savings"]), "txns": txns,
            "months": sorted({str(t["date"])[:7] for t in txns if t.get("date")})}


@app.route("/assistant", methods=["GET", "POST"])
def assistant():
    """Chat page: stored sessions, Gemini-or-rules answers, confirmed actions.

    Conversations live in chat_sessions/chat_messages (per user), so any
    session can be revisited from the navigator. Data-change requests
    only show a proposal; the change runs solely after Confirm.
    """
    client = _data()
    if request.method == "POST":
        form_action = request.form.get("action", "ask")
        if form_action == "new":
            created = chat_mod.create_session(client, "New chat")
            return redirect(url_for("assistant", session=created["id"]))
        if form_action == "cancel":
            pending = session.pop("assistant_pending", None)
            if pending:
                chat_mod.add_message(
                    client, pending["session_id"], "assistant",
                    "Cancelled — no changes were made.", engine="rules")
                return redirect(url_for("assistant",
                                        session=pending["session_id"]))
            return redirect(url_for("assistant"))
        if form_action == "confirm":
            pending = session.pop("assistant_pending", None)
            if not pending:
                flash("Nothing to confirm.", "error")
                return redirect(url_for("assistant"))
            try:
                result = chat_actions_mod.execute_action(
                    client, pending["action"])
            except (ValueError, sb_data.SupabaseDataError) as exc:
                result = f"Couldn't do that: {exc}"
            chat_mod.add_message(client, pending["session_id"], "assistant",
                                 result, engine="rules")
            return redirect(url_for("assistant",
                                    session=pending["session_id"]))
        question = request.form.get("q", "").strip()
        sid = request.form.get("session_id", type=int)
        if not question:
            return redirect(url_for("assistant", session=sid)
                            if sid else url_for("assistant"))
        current = chat_mod.ensure_session(client, sid, question)
        chat_mod.add_message(client, current["id"], "user", question)
        allowed, refusal = guard(question)
        if not allowed:
            chat_mod.add_message(client, current["id"], "assistant",
                                 refusal, engine="rules")
            return redirect(url_for("assistant", session=current["id"]))
        ctx = _assistant_ctx(client)
        parsed = chat_actions_mod.parse_action(question, ctx)
        if parsed is not None:
            chat_mod.add_message(client, current["id"], "assistant",
                                 chat_actions_mod.describe(parsed),
                                 engine="rules")
            session["assistant_pending"] = {"action": parsed,
                                            "session_id": current["id"]}
            return redirect(url_for("assistant", session=current["id"]))
        history = chat_mod.recent_history(client, current["id"])
        text, engine = llm_mod.answer_with_engine(
            question, ctx, history=history,
            call_fn=app.config.get("LLM_CALL"))
        chat_mod.add_message(client, current["id"], "assistant", text,
                             engine=engine)
        return redirect(url_for("assistant", session=current["id"]))
    sessions = chat_mod.list_sessions(client)
    current = chat_mod.get_session(client,
                                   request.args.get("session", type=int))
    if current is None and sessions:
        current = sessions[0]
    messages = chat_mod.messages_for(client, current["id"]) if current else []
    pending = session.get("assistant_pending")
    if pending and current and pending.get("session_id") != current["id"]:
        pending = None
    return render_template(
        "assistant.html", history=chat_mod.history_pairs(messages),
        sessions=sessions, current=current, pending=pending,
        gemini_configured=llm_mod.is_configured()
        or app.config.get("LLM_CALL") is not None)


if __name__ == "__main__":
    app.run(debug=True)
