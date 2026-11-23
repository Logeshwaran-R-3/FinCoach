"""Idempotent demo seed: 1 CSV source, 60 txns over 4 months, 2 rules, 1 goal."""

from __future__ import annotations

from fincoach.intelligence import goals, learn, profile as profile_mod
from fincoach.storage import sb_data
from fincoach.intelligence import recurring as recurring_mod
from fincoach.storage import source_repo
from fincoach.intelligence.categorize import categorize
from fincoach.config import USERS_DIR, safe_uid
from fincoach.ingest import run_ingest

DEMO_FILENAME = "demo.csv"
DEMO_MONTHS = ["2026-08", "2026-09", "2026-10", "2026-11"]
GOAL_NAME = "Emergency fund"
DEMO_EMAIL = "demo@fincoach.local"
DEMO_PASSWORD = "demo1234"


def _month_rows(month: str, extra: tuple[str, str]) -> list[tuple[str, str, str]]:
    d = min(20, 28) if month == "2026-11" else 28
    rows = [
        (f"{month}-01", "Acme Corp salary credit", "52000"),
        (f"{month}-02", "Flat rent landlord transfer", "-15000"),
        (f"{month}-03", "BigBasket groceries", "-2400"),
        (f"{month}-05", "Swiggy food order", "-450"),
        (f"{month}-07", "Uber cab ride", "-350"),
        (f"{month}-08", "Electricity bill payment", "-1400"),
        (f"{month}-10", "Kirana store vegetables", "-900"),
        (f"{month}-12", "Restaurant dinner", "-1200"),
        (f"{month}-14", "Petrol fuel station", "-2000"),
        (f"{month}-15", "Jio mobile recharge", "-299"),
        (f"{month}-17", "Amazon purchase", "-1800"),
        (f"{month}-19", "HDFC home loan EMI", "-8500"),
        (f"{month}-21", "Netflix subscription", "-499"),
        (f"{month}-23", "Apollo pharmacy medicine", "-650"),
    ]
    day = f"{d:02d}"
    rows.append((f"{month}-{day}", extra[0], extra[1]))
    return rows


def _demo_csv() -> bytes:
    lines = ["date,description,amount"]
    extras = [("Zerodha mutual fund SIP", "-5000"),
              ("Freelance client payment", "6000"),
              ("Myntra clothing", "-2200"),
              ("Zerodha mutual fund SIP", "-5000")]
    for month, extra in zip(DEMO_MONTHS, extras):
        for day, desc, amt in _month_rows(month, extra):
            lines.append(f"{day},{desc},{amt}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _client(uid: str, access_token: str | None = None):
    try:
        from flask import current_app, has_app_context

        if has_app_context() and current_app:
            configured = current_app.config.get("DATA_CLIENT")
            if configured is not None:
                if callable(configured):
                    return configured(uid, access_token)
                return configured
    except ImportError:
        pass
    return sb_data.get_client(uid, access_token)


def seed(uid: str, access_token: str | None = None) -> dict:
    """Create demo data for one user if missing; refresh categories/profile.

    Idempotent per user. Accounts live in Supabase Auth, so the demo login
    must be created once via the Sign up page (or the Supabase dashboard);
    the seed only loads financial data for that user's id.
    """
    client = _client(uid, access_token)

    have_source = client.find("sources", filename=DEMO_FILENAME)
    if not have_source:
        src = source_repo.add(uid, "csv", DEMO_FILENAME, _demo_csv(),
                              mime="text/csv", access_token=access_token)
        run_ingest(uid, src.id, access_token=access_token)

    for merchant, category in (("Swiggy", "dining"),
                               ("BigBasket", "groceries")):
        if not client.find("category_rules", category=category):
            learn.learn_rule(client, merchant, category)
    for row in client.rows("transactions"):
        if (row.get("category") or "") != "":
            continue
        hit = learn.apply_rules_for(client, row["description"],
                                    row.get("merchant", ""))
        if hit is None:
            hit = categorize(row["description"], row.get("merchant", ""))
        client.set("transactions", row["id"], {"category": hit["category"]})
    if not client.find("goals", name=GOAL_NAME):
        goals.create_for(client, GOAL_NAME, 120000, deadline="2027-11-30",
                         current=20000)
    txns = client.rows("transactions")
    rec = recurring_mod.detect(txns)
    prof = profile_mod.build_profile(txns, rec)
    profile_mod.save_profile_for(client, prof)
    counts = client.table_counts()
    return {"months": DEMO_MONTHS, "recurring_found": len(rec),
            "profile": prof["name"], "counts": counts}


def list_users() -> list[str]:
    """User ids that already have local stub data (offline test mode only)."""
    if not USERS_DIR.is_dir():
        return []
    return sorted(p.name for p in USERS_DIR.iterdir()
                  if p.is_dir() and (p / "fincoach.db").exists())


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="fincoach.storage.seed")
    parser.add_argument("--user", default="",
                        help="Supabase user id to seed (see --list). "
                             "Easiest alternative: log in and use the "
                             "'Load demo data' button in the app. Against "
                             "live Supabase this needs SUPABASE_SERVICE_KEY.")
    parser.add_argument("--list", action="store_true",
                        help="list user ids that have local stub data")
    args = parser.parse_args(argv)
    if args.list or not args.user:
        users = list_users()
        if users:
            print("users with local data:")
            for u in users:
                print(f"  {u}")
        else:
            print("no users have local data yet (log in via the app first).")
        if not args.list:
            parser.print_help()
            return 2
        return 0
    uid = args.user if not sb_data.using_stub() else safe_uid(args.user)
    summary = seed(uid)
    print(f"user: {uid}")
    for name, total in summary["counts"].items():
        print(f"{name}: {total}")
    print(f"months: {','.join(summary['months'])}")
    print(f"recurring_found: {summary['recurring_found']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
