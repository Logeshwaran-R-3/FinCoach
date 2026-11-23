"""FinCoach test suite: normalize/validate, dedupe, categorize, learn, goals, scenario, nudges."""

import atexit
import itertools
import os
import shutil
import tempfile

_temp_data = tempfile.mkdtemp(prefix="fincoach-test-")
os.environ["FINCOACH_DATA"] = _temp_data
atexit.register(shutil.rmtree, _temp_data, True)

import unittest  # noqa: E402

from fincoach.intelligence import categorize, goals, learn
from fincoach.storage import db# noqa: E402
from fincoach.config import safe_uid, user_db_path  # noqa: E402
from fincoach.intelligence import nudges as nudges_mod# noqa: E402
from fincoach.intelligence import profile as profile_mod# noqa: E402
from fincoach.intelligence import recurring as recurring_mod# noqa: E402
from fincoach.storage import seed
from fincoach.intelligence import simulate# noqa: E402
from fincoach.ingest.dedupe import dedupe  # noqa: E402
from fincoach.ingest.normalize import normalize, normalize_all  # noqa: E402
from fincoach.ingest.validate import validate  # noqa: E402


class TestNormalizeValidate(unittest.TestCase):
    def test_three_rows(self):
        raws = [
            {"date": "2026-10-01", "description": "Acme salary", "amount": "52000"},
            {"date": "2026-10-02", "description": "Flat rent", "amount": "-15000"},
            {"date": "2026-10-03", "description": "Groceries",
             "debit": "2400", "credit": ""},
        ]
        valid, rejected = validate(normalize_all(raws))
        self.assertEqual(len(valid), 3)
        self.assertEqual(rejected, [])
        self.assertEqual(valid[0]["date"], "2026-10-01")
        self.assertEqual(valid[1]["amount"], -15000.0)
        self.assertEqual(valid[2]["amount"], -2400.0)


class TestDedupe(unittest.TestCase):
    def test_skips_exact_dup(self):
        first = normalize({"date": "2026-10-01", "description": "Rent",
                           "amount": "-15000"})
        unique, _ = dedupe([first], [])
        dup = normalize({"date": "2026-10-01", "description": "Rent",
                         "amount": "-15000"})
        fresh = normalize({"date": "2026-10-02", "description": "Milk",
                           "amount": "-60"})
        unique2, dupes = dedupe([dup, fresh], unique)
        self.assertEqual(len(unique2), 1)
        self.assertEqual(unique2[0]["description"], "Milk")
        self.assertEqual(len(dupes), 1)


class TestCategorize(unittest.TestCase):
    def test_salary_is_income(self):
        txn = normalize({"date": "2026-10-01", "description": "Acme Corp salary",
                         "amount": "52000"})
        self.assertEqual(txn["type"], "income")
        hit = categorize.categorize(txn["description"], txn["merchant"])
        self.assertEqual(hit["category"], "salary")


class TestLearn(unittest.TestCase):
    def test_correction_creates_rule(self):
        conn = db.migrate()
        try:
            written = learn.learn_from_correction(conn, "Zylophone Cafe", "dining")
            self.assertTrue(written)
            hit = learn.apply_rules(conn, "Zylophone Cafe bill", "Zylophone Cafe")
            self.assertIsNotNone(hit)
            self.assertEqual(hit["category"], "dining")
        finally:
            conn.close()


class TestGoals(unittest.TestCase):
    def test_on_track_true_and_false(self):
        goal = {"name": "g", "target": 12000, "current": 0,
                "deadline": "2027-11-30"}
        fast = goals.goal_progress(goal, monthly_savings=2000,
                                   today="2026-11-21")
        slow = goals.goal_progress(goal, monthly_savings=100,
                                   today="2026-11-21")
        self.assertTrue(fast["on_track"])
        self.assertFalse(slow["on_track"])


class TestScenario(unittest.TestCase):
    def test_cutting_dining_improves_savings(self):
        profile = {"avg_monthly_income": 52000, "avg_monthly_expenses": 35000,
                   "top_categories": [{"category": "dining", "total": 2000}]}
        result = simulate.run_scenario(profile, dining_pct=-50)
        self.assertGreater(result["new_savings"], 17000)
        self.assertGreater(result["delta_vs_base"], 0)


class TestNudgesSeeded(unittest.TestCase):
    def test_nudges_list_on_seeded_data(self):
        summary = seed.seed("test-nudges")
        self.assertEqual(summary["counts"]["transactions"], 60)
        conn = db.migrate(user_db_path("test-nudges"))
        try:
            txns = [dict(r) for r in conn.execute("SELECT * FROM transactions")]
            rec = recurring_mod.detect(txns)
            prof = profile_mod.build_profile(txns, rec)
            grows = [dict(r) for r in conn.execute("SELECT * FROM goals")]
            for g in grows:
                g["progress"] = goals.goal_progress(g, prof["savings"])
            nudges = nudges_mod.generate_nudges(prof, rec, grows, {})
        finally:
            conn.close()
        self.assertIsInstance(nudges, list)
        self.assertLessEqual(len(nudges), 8)
        for n in nudges:
            self.assertIn(n["severity"], ("high", "medium", "low"))
            self.assertTrue(n["text"])
            self.assertTrue(n["source"])


try:
    from fincoach.app import app as flask_app
    HAS_FLASK = True
except ImportError:
    flask_app = None
    HAS_FLASK = False


class StubSupabase:
    """In-memory Supabase Auth stand-in (no network), preloaded demo user."""

    def __init__(self):
        from fincoach.storage import supabase_auth as sb
        self._sb = sb
        self.users = {"demo@fincoach.local": {
            "id": "uid-demo", "email": "demo@fincoach.local",
            "password": "demo1234", "name": "Demo User",
            "phone": "", "currency": "INR"}}
        self.tokens = {}  # access token -> email
        self.refresh = {}  # refresh token -> email
        self.counter = 0

    def _user(self, email):
        """GoTrue-shaped user dict, incl. user_metadata like Supabase."""
        user = self.users[email]
        return {"id": user["id"], "email": email,
                "user_metadata": {"name": user.get("name", ""),
                                  "phone": user.get("phone", ""),
                                  "currency": user.get("currency", "INR")}}

    def _mint(self, email):
        self.counter += 1
        access, refresh = f"tok-{self.counter}", f"ref-{self.counter}"
        self.tokens[access] = email
        self.refresh[refresh] = email
        return {"access_token": access, "refresh_token": refresh,
                "user": self._user(email)}

    def sign_up(self, email, password, name="", phone="", currency=""):
        email = self._sb.validate_new_account(email, password)
        if email in self.users:
            raise self._sb.AuthError(
                "That email is already registered. Try logging in.")
        # Globally unique like real Supabase UUIDs: tests share one temp
        # data dir, so sequential per-stub ids would collide across tests.
        self.users[email] = {"id": f"uid-t{next(_test_uid_seq)}",
                             "email": email, "password": password,
                             "name": (name or "").strip(),
                             "phone": (phone or "").strip(),
                             "currency": (currency or "").strip() or "INR"}
        session = self._mint(email)
        return {"user": session["user"], "session": session}

    def sign_in(self, email, password):
        email = self._sb.normalize_email(email)
        user = self.users.get(email)
        if user is None or user["password"] != (password or ""):
            raise self._sb.Unauthorized("Invalid login credentials")
        session = self._mint(email)
        return {"user": session["user"], "session": session}

    def get_user(self, access_token):
        email = self.tokens.get(access_token)
        if email is None:
            raise self._sb.Unauthorized("invalid token")
        return self._user(email)

    def refresh_session(self, refresh_token):
        email = self.refresh.get(refresh_token)
        if email is None:
            raise self._sb.Unauthorized("invalid refresh token")
        session = self._mint(email)
        return {"user": session["user"], "session": session}

    def sign_out(self, access_token):
        self.tokens.pop(access_token, None)

    def update_user(self, access_token, name="", phone="", currency=""):
        email = self.tokens.get(access_token)
        if email is None:
            raise self._sb.Unauthorized("invalid token")
        user = self.users[email]
        user["name"] = (name or "").strip()
        user["phone"] = (phone or "").strip()
        user["currency"] = (currency or "").strip() or "INR"
        return self._user(email)


_test_uid_seq = itertools.count(1)


def _use_stub_auth():
    flask_app.config["SUPABASE_CLIENT"] = StubSupabase()


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestAuthPages(unittest.TestCase):
    def setUp(self):
        seed.seed("uid-demo")
        flask_app.config["TESTING"] = True
        _use_stub_auth()
        self.client = flask_app.test_client()

    def test_protected_redirects_to_login(self):
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])
        self.assertIn("next=", r.headers["Location"])

    def test_signup_login_logout(self):
        r = self.client.post("/signup", data={
            "email": "new@fincoach.local", "password": "password123"},
            follow_redirects=True)
        body = r.get_data(as_text=True)
        self.assertIn("Account created", body)
        self.assertIn("Dashboard", body)
        r = self.client.post("/logout", follow_redirects=True)
        self.assertIn("Logged out", r.get_data(as_text=True))
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        r = self.client.post("/login", data={
            "email": "new@fincoach.local", "password": "password123"},
            follow_redirects=True)
        self.assertIn("Welcome back", r.get_data(as_text=True))
        r = self.client.post("/login", data={
            "email": "new@fincoach.local", "password": "wrongpass1"})
        self.assertIn("Wrong email or password", r.get_data(as_text=True))

    def test_signup_rejects_duplicates_and_short_passwords(self):
        self.client.post("/signup", data={
            "email": "dupe@fincoach.local", "password": "password123"})
        r = self.client.post("/signup", data={
            "email": "dupe@fincoach.local", "password": "password123"})
        self.assertIn("already registered", r.get_data(as_text=True))
        r = self.client.post("/signup", data={
            "email": "short@fincoach.local", "password": "abc"})
        self.assertIn("at least 8 characters", r.get_data(as_text=True))

    def test_demo_login_shows_sidebar(self):
        self.client.post("/login", data={
            "email": "demo@fincoach.local", "password": "demo1234"})
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn('class="side"', body)
        self.assertIn("Assistant", body)
        self.assertIn("demo@fincoach.local", body)

    def test_login_next_redirect(self):
        self.client.post("/signup", data={
            "email": "next@fincoach.local", "password": "password123"})
        self.client.post("/logout")
        r = self.client.post("/login", data={
            "email": "next@fincoach.local", "password": "password123",
            "next": "/goals"}, follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], "/goals")

    def test_stale_token_refreshes_silently(self):
        self.client.post("/login", data={
            "email": "demo@fincoach.local", "password": "demo1234"})
        with self.client.session_transaction() as sess:
            sess["sb_access"] = "stale-token"
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertIn("demo@fincoach.local", r.get_data(as_text=True))

    def test_unconfigured_supabase_shows_setup_message(self):
        from fincoach.storage import supabase_auth as sb
        flask_app.config.pop("SUPABASE_CLIENT", None)
        old_url, old_key = sb.SUPABASE_URL, sb.SUPABASE_ANON_KEY
        sb.SUPABASE_URL, sb.SUPABASE_ANON_KEY = "", ""
        try:
            r = self.client.post("/signup", data={
                "email": "x@y.co", "password": "password123"})
            self.assertIn("not configured", r.get_data(as_text=True))
            r = self.client.post("/login", data={
                "email": "x@y.co", "password": "password123"})
            self.assertIn("not configured", r.get_data(as_text=True))
        finally:
            sb.SUPABASE_URL, sb.SUPABASE_ANON_KEY = old_url, old_key
            _use_stub_auth()

    def test_health_open_and_theme(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.get_json(), {"status": "ok"})
        css = self.client.get("/static/style.css").get_data(as_text=True)
        self.assertIn("--amber-glow: #ff9f1c", css)
        self.assertIn("--light-sea-green: #2ec4b6", css)
        login = self.client.get("/login").get_data(as_text=True)
        self.assertIn("demo@fincoach.local", login)
        self.assertIn("Create an account", login)


class TestAssistant(unittest.TestCase):
    def _ctx(self):
        seed.seed("test-assistant")
        conn = db.migrate(user_db_path("test-assistant"))
        try:
            txns = [dict(r) for r in conn.execute("SELECT * FROM transactions")]
            rec = recurring_mod.detect(txns)
            prof = profile_mod.build_profile(txns, rec)
            grows = []
            for row in conn.execute("SELECT * FROM goals ORDER BY id"):
                g = dict(row)
                g["progress"] = goals.goal_progress(g, prof["savings"])
                grows.append(g)
        finally:
            conn.close()
        return {"profile": prof, "recurring": rec, "goals": grows, "txns": txns,
                "months": sorted({str(t["date"])[:7] for t in txns
                                  if t.get("date")})}

    def test_status_and_category(self):
        from fincoach.assistant import assistant as assistant_mod
        ctx = self._ctx()
        status = assistant_mod.answer("how am i doing?", ctx)
        self.assertIn("Rs 15,002", status)
        cat = assistant_mod.answer("how much did i spend on dining?", ctx)
        self.assertIn("dining:", cat)
        self.assertIn("1,650", cat)

    def test_whatif_has_real_baseline(self):
        from fincoach.assistant import assistant as assistant_mod
        ctx = self._ctx()
        out = assistant_mod.answer("what if i cut dining by 30%?", ctx)
        self.assertIn("New savings", out)
        self.assertIn("1,650", out)
        self.assertNotIn("Rs 0/month x", out)
        rent = assistant_mod.answer("what if rent goes up by 2000?", ctx)
        self.assertIn("New savings", rent)

    def test_goals_review_and_refusal(self):
        from fincoach.assistant import assistant as assistant_mod
        ctx = self._ctx()
        self.assertIn("Emergency fund",
                      assistant_mod.answer("are my goals on track?", ctx))
        self.assertIn("2026-11", assistant_mod.answer("review 2026-11", ctx))
        self.assertIn("can't recommend", assistant_mod.answer(
            "which stock should i buy?", ctx))
        self.assertIn("Ask me things like", assistant_mod.answer(
            "blargh wibble?", ctx))

    @unittest.skipUnless(HAS_FLASK, "flask not installed")
    def test_assistant_page_flow(self):
        seed.seed("uid-demo")
        flask_app.config["TESTING"] = True
        _use_stub_auth()
        client = flask_app.test_client()
        client.post("/login", data={"email": "demo@fincoach.local",
                                    "password": "demo1234"})
        r = client.post("/assistant", data={"q": "are my goals on track?"},
                        follow_redirects=True)
        body = r.get_data(as_text=True)
        self.assertIn("Emergency fund", body)
        self.assertIn("msg-a", body)
        self.assertIn("are my goals on track?", client.get("/assistant")
                      .get_data(as_text=True))


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestUserIsolation(unittest.TestCase):
    """Each account starts empty and stays private to that account."""

    def setUp(self):
        flask_app.config["TESTING"] = True
        _use_stub_auth()
        self.client = flask_app.test_client()

    def test_fresh_user_starts_empty(self):
        self.client.post("/signup", data={
            "email": "fresh@x.co", "password": "password123"})
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("your space starts empty", body)
        self.assertIn("0 transactions", body)
        sources = self.client.get("/sources").get_data(as_text=True)
        self.assertIn("No sources yet", sources)

    def test_users_cannot_see_each_other(self):
        other = flask_app.test_client()
        self.client.post("/signup", data={
            "email": "a@x.co", "password": "password123"})
        other.post("/signup", data={
            "email": "b@x.co", "password": "password123"})
        r = self.client.post("/sources/upload", data={
            "kind": "manual", "date": "2026-11-05",
            "description": "A private rent", "amount": "-1000"},
            follow_redirects=True)
        self.assertIn("Manual entry saved", r.get_data(as_text=True))
        mine = self.client.get("/transactions").get_data(as_text=True)
        self.assertIn("A private rent", mine)
        theirs = other.get("/transactions").get_data(as_text=True)
        self.assertNotIn("A private rent", theirs)
        self.assertIn("0 transactions",
                      other.get("/").get_data(as_text=True))
        stub = flask_app.config["SUPABASE_CLIENT"]
        path_a = user_db_path(stub.users["a@x.co"]["id"])
        path_b = user_db_path(stub.users["b@x.co"]["id"])
        self.assertNotEqual(path_a, path_b)
        self.assertTrue(path_a.exists())
        self.assertTrue(path_b.exists())

    def test_demo_load_fills_only_current_user(self):
        other = flask_app.test_client()
        self.client.post("/signup", data={
            "email": "demo1@x.co", "password": "password123"})
        other.post("/signup", data={
            "email": "demo2@x.co", "password": "password123"})
        r = self.client.post("/demo/load", follow_redirects=True)
        body = r.get_data(as_text=True)
        self.assertIn("Demo data loaded", body)
        self.assertIn("60 transactions", body)
        self.assertIn("0 transactions",
                      other.get("/").get_data(as_text=True))
        again = self.client.post("/demo/load",
                                 follow_redirects=True).get_data(as_text=True)
        self.assertIn("60 transactions", again)

    def test_safe_uid_blocks_traversal(self):
        self.assertEqual(safe_uid("demo@fincoach.local"),
                         "demo_fincoach_local")
        nasty = safe_uid("../../etc/passwd")
        self.assertNotIn("/", nasty)
        self.assertNotIn(".", nasty)
        self.assertNotIn("\\", nasty)
        self.assertEqual(safe_uid(""), "anon")


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestAppEntryPoint(unittest.TestCase):
    def test_app_importable_from_package_dir(self):
        import subprocess
        import sys

        import fincoach
        pkg_dir = os.path.dirname(os.path.abspath(fincoach.__file__))
        proc = subprocess.run(
            [sys.executable, "-c", "import app; print('entry-ok')"],
            cwd=pkg_dir, capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertIn("entry-ok", proc.stdout)


class TestSupabaseSchema(unittest.TestCase):
    """supabase/schema.sql defines the full Postgres + RLS + Storage setup."""

    @classmethod
    def _sql(cls):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "supabase", "schema.sql"),
                  encoding="utf-8") as fh:
            return fh.read()

    def test_all_tables_with_user_id(self):
        sql = self._sql()
        for table in ("sources", "transactions", "category_rules", "goals",
                      "goal_contribs", "profiles", "chat_sessions",
                      "chat_messages", "reminders"):
            self.assertIn(f"create table if not exists public.{table}", sql)
            self.assertIn(f"{table}_user_id_idx", sql)
        self.assertIn("user_id uuid not null", sql)

    def test_sources_status_includes_archived(self):
        self.assertIn("'archived'", self._sql())

    def test_rls_owner_policies(self):
        sql = self._sql()
        self.assertIn("enable row level security", sql)
        self.assertIn("auth.uid() = user_id", sql)

    def test_storage_bucket_with_owner_policies(self):
        sql = self._sql()
        self.assertIn("'sources'", sql)
        self.assertIn("storage.buckets", sql)
        self.assertIn("storage.objects", sql)
        self.assertIn("auth.uid()::text", sql)


class TestStubTransport(unittest.TestCase):
    """Offline stub transport: same API as Supabase, zero network."""

    def test_dispatcher_uses_stub_when_fincoach_data_set(self):
        from fincoach.storage import sb_data
        self.assertTrue(os.environ.get("FINCOACH_DATA"))
        client = sb_data.get_client("stub-u1")
        self.assertIsInstance(client, sb_data.StubDataClient)

    def test_stub_crud_and_counts(self):
        from fincoach.storage import sb_data
        client = sb_data.StubDataClient("stub-crud")
        row = client.add("goals", {"name": "g", "target": 100,
                                   "current": 0, "deadline": ""})
        self.assertEqual(row["name"], "g")
        self.assertEqual(client.count("goals"), 1)
        updated = client.set("goals", row["id"], {"current": 40})
        self.assertEqual(float(updated["current"]), 40.0)
        self.assertEqual(client.get("goals", row["id"])["name"], "g")
        self.assertEqual(len(client.rows("goals")), 1)
        self.assertEqual(len(client.find("goals", name="g")), 1)
        self.assertEqual(client.find("goals", name="nope"), [])
        self.assertIn("transactions", client.table_counts())
        client.remove("goals", row["id"])
        self.assertEqual(client.count("goals"), 0)
        self.assertIsNone(client.get("goals", row["id"]))

    def test_stub_users_are_isolated(self):
        from fincoach.storage import sb_data
        a = sb_data.StubDataClient("stub-iso-a")
        b = sb_data.StubDataClient("stub-iso-b")
        a.add("goals", {"name": "private", "target": 1,
                        "current": 0, "deadline": ""})
        self.assertEqual(a.count("goals"), 1)
        self.assertEqual(b.count("goals"), 0)

    def test_stub_files_roundtrip(self):
        from fincoach.storage import sb_data
        client = sb_data.StubDataClient("stub-files")
        client.put_file("stub-files/abc_demo.csv", b"a,b\n1,2\n",
                        "text/csv")
        self.assertEqual(client.get_file("stub-files/abc_demo.csv"),
                         b"a,b\n1,2\n")
        client.del_file("stub-files/abc_demo.csv")
        with self.assertRaises(OSError):
            client.get_file("stub-files/abc_demo.csv")

    def test_source_repo_roundtrip_offline(self):
        from fincoach.storage import source_repo
        src = source_repo.add("stub-repo", "csv", "s.csv", b"date,amount\n",
                              mime="text/csv")
        self.assertEqual(src.filename, "s.csv")
        self.assertEqual(len(source_repo.list("stub-repo")), 1)
        self.assertEqual(
            source_repo.read_bytes("stub-repo", src), b"date,amount\n")
        source_repo.set_status("stub-repo", src.id, "done", txn_count=1)
        self.assertEqual(
            source_repo.get("stub-repo", src.id).status, "done")
        archived = source_repo.archive("stub-repo", src.id)
        self.assertEqual(archived.status, "archived")
        source_repo.delete("stub-repo", src.id)
        self.assertEqual(source_repo.list("stub-repo"), [])
        with self.assertRaises(KeyError):
            source_repo.get("stub-repo", src.id)


class TestRestClientOffline(unittest.TestCase):
    """PostgREST/Storage request shapes, with urlopen stubbed (no network)."""

    def _client(self):
        from fincoach.storage import sb_data
        return sb_data.SupabaseRestClient("https://demo.supabase.co",
                                          "anon-key", "user-token", "uid-1")

    def _patch_urlopen(self, bodies):
        import io
        import urllib.request

        calls = []

        class FakeResp:
            def __init__(self, payload, status=200):
                self._payload = payload
                self.status = status

            def read(self):
                return self._payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake(req, timeout=None):
            calls.append(req)
            body, status = bodies[min(len(calls) - 1, len(bodies) - 1)]
            return FakeResp(body, status)

        real = urllib.request.urlopen
        urllib.request.urlopen = fake
        self.addCleanup(setattr, urllib.request, "urlopen", real)
        return calls

    def test_select_scopes_to_user_and_sends_auth(self):
        calls = self._patch_urlopen([(b"[]", 200)])
        self.assertEqual(self._client().rows("transactions"), [])
        req = calls[0]
        self.assertIn("/rest/v1/transactions", req.full_url)
        self.assertIn("user_id=eq.uid-1", req.full_url)
        self.assertEqual(req.get_header("Apikey"), "anon-key")
        self.assertEqual(req.get_header("Authorization"), "Bearer user-token")

    def test_insert_attaches_user_id_and_prefers_representation(self):
        calls = self._patch_urlopen(
            [(b'[{"id": 7, "name": "g"}]', 201)])
        import json
        row = self._client().add("goals", {"name": "g", "target": 5})
        self.assertEqual(row["id"], 7)
        req = calls[0]
        self.assertEqual(req.get_method(), "POST")
        sent = json.loads(req.data.decode("utf-8"))
        self.assertEqual(sent["user_id"], "uid-1")
        self.assertEqual(sent["name"], "g")
        self.assertEqual(req.get_header("Prefer"), "return=representation")

    def test_update_and_delete_scope_to_row_and_user(self):
        calls = self._patch_urlopen(
            [(b'[{"id": 3}]', 200), (b"", 204)])
        client = self._client()
        client.set("goals", 3, {"current": 9})
        client.remove("goals", 3)
        self.assertEqual(calls[0].get_method(), "PATCH")
        self.assertIn("id=eq.3", calls[0].full_url)
        self.assertIn("user_id=eq.uid-1", calls[0].full_url)
        self.assertEqual(calls[1].get_method(), "DELETE")
        self.assertIn("id=eq.3", calls[1].full_url)

    def test_storage_upload_download_paths(self):
        calls = self._patch_urlopen([(b"{}", 200), (b"bytes", 200)])
        client = self._client()
        client.put_file("uid-1/abc_s.csv", b"bytes", "text/csv")
        self.assertEqual(client.get_file("uid-1/abc_s.csv"), b"bytes")
        self.assertIn("/storage/v1/object/sources/uid-1/abc_s.csv",
                      calls[0].full_url)
        self.assertEqual(calls[0].get_header("Content-type"), "text/csv")
        self.assertIn("/storage/v1/object/sources/uid-1/abc_s.csv",
                      calls[1].full_url)

    def test_http_error_maps_to_data_error(self):
        import io
        import urllib.error
        import urllib.request

        def boom(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 401, "x", {}, io.BytesIO(b'{"message": "bad"}'))

        real = urllib.request.urlopen
        urllib.request.urlopen = boom
        self.addCleanup(setattr, urllib.request, "urlopen", real)
        from fincoach.storage import sb_data
        with self.assertRaises(sb_data.SupabaseDataError):
            self._client().rows("goals")

    def test_missing_config_raises_config_error(self):
        from fincoach.storage import sb_data
        with self.assertRaises(sb_data.SupabaseConfigError):
            sb_data.SupabaseRestClient("", "", "tok", "u")
        with self.assertRaises(sb_data.SupabaseConfigError):
            sb_data.SupabaseRestClient("https://x", "k", "", "u")


class TestSeedStub(unittest.TestCase):
    """seed() per user id through the offline stub (no network)."""

    def test_seed_idempotent_sixty_txns(self):
        first = seed.seed("sb-seed")
        self.assertEqual(first["counts"]["transactions"], 60)
        again = seed.seed("sb-seed")
        self.assertEqual(again["counts"]["transactions"], 60)
        conn = db.migrate(user_db_path("sb-seed"))
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM transactions").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 60)

    def test_seed_is_per_user(self):
        seed.seed("sb-seed-a")
        summary = seed.seed("sb-seed-b")
        self.assertEqual(summary["counts"]["transactions"], 60)
        conn = db.migrate(user_db_path("sb-seed-a"))
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM transactions").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 60)


def _has_module(name):
    import importlib.util
    return importlib.util.find_spec(name) is not None


_HAS_OPENPYXL = _has_module("openpyxl")
_HAS_PYPDF = _has_module("pypdf")
_HAS_PIL = _has_module("PIL")
_HAS_PYTESSERACT = _has_module("pytesseract")


def _minimal_pdf_bytes(lines):
    """Smallest valid single-page PDF whose text pypdf can extract."""

    def esc(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    ops = ["BT /F1 12 Tf 72 720 Td 14 TL"]
    for ln in lines:
        ops.append(f"({esc(ln)}) Tj T*")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    pdf = [b"%PDF-1.4"]
    offsets = [0]
    for i, body in enumerate(objs, start=1):
        offsets.append(sum(len(p) + 1 for p in pdf))
        pdf.append(b"%d 0 obj\n" % i + body + b"\nendobj")
    xref_at = sum(len(p) + 1 for p in pdf)
    xref = [b"xref", b"0 %d" % (len(objs) + 1), b"0000000000 65535 f "]
    xref += [b"%010d 00000 n " % off for off in offsets[1:]]
    pdf += xref
    pdf.append(b"trailer\n<< /Size %d /Root 1 0 R >>" % (len(objs) + 1))
    pdf.append(b"startxref\n%d\n%%%%EOF" % xref_at)
    return b"\n".join(pdf) + b"\n"


def _minimal_docx_bytes(paragraphs):
    """Valid .docx (stdlib zip) readable with or without python-docx."""
    import io
    import zipfile

    paras = "".join(
        f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxml'
            'formats.org/package/2006/content-types"><Default Extension="xml"'
            ' ContentType="application/xml"/><Override PartName='
            '"/word/document.xml" ContentType="application/vnd.openxml'
            'formats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>")
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
            'openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/officeDocument" Target='
            '"word/document.xml"/></Relationships>')
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.'
            'openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            + paras + "</w:body></w:document>")
    return buf.getvalue()


def _xlsx_bytes(rows):
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _png_bytes():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (200, 60), "white").save(buf, format="PNG")
    return buf.getvalue()


class TestRequirements(unittest.TestCase):
    """requirements.txt pins every extractor dependency (#3)."""

    def test_extract_deps_pinned_minimums(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "requirements.txt"),
                  encoding="utf-8") as fh:
            live = [ln.strip() for ln in fh
                    if ln.strip() and not ln.strip().startswith("#")]
        for pkg in ("Pillow", "pytesseract", "pypdf", "openpyxl",
                    "python-docx"):
            self.assertTrue(
                any(ln.lower().startswith(pkg.lower() + ">=")
                    for ln in live),
                f"{pkg} must be an uncommented pinned requirement")


class TestTypeColumn(unittest.TestCase):
    """CSV/XLSX 'type' columns and Dr/Cr suffixes set the sign (#12)."""

    def test_csv_type_column_signs(self):
        from fincoach.ingest.extract_csv import extract_csv
        from fincoach.ingest.normalize import normalize_all
        raws = extract_csv(
            b"date,description,amount,type\n"
            b"2026-11-01,Salary,52000,Credit\n"
            b"2026-11-02,Rent,15000,Debit\n")
        valid, rejected = validate(normalize_all(raws))
        self.assertEqual(rejected, [])
        self.assertEqual(valid[0]["amount"], 52000.0)
        self.assertEqual(valid[0]["type"], "income")
        self.assertEqual(valid[1]["amount"], -15000.0)
        self.assertEqual(valid[1]["type"], "debit")

    @unittest.skipUnless(_HAS_OPENPYXL, "openpyxl not installed")
    def test_xlsx_type_column_signs(self):
        from fincoach.ingest.extract_xlsx import extract_xlsx
        from fincoach.ingest.normalize import normalize_all
        raws = extract_xlsx(_xlsx_bytes([
            ["date", "description", "amount", "type"],
            ["2026-11-01", "Salary", 52000, "Credit"],
            ["2026-11-02", "Rent", 15000, "Debit"],
        ]))
        valid, _ = validate(normalize_all(raws))
        self.assertEqual(valid[0]["amount"], 52000.0)
        self.assertEqual(valid[1]["amount"], -15000.0)

    def test_dr_cr_suffixes(self):
        from fincoach.ingest.normalize import parse_amount
        self.assertEqual(parse_amount("250 Dr"), -250.0)
        self.assertEqual(parse_amount("250 dr"), -250.0)
        self.assertEqual(parse_amount("250 Cr"), 250.0)
        self.assertEqual(parse_amount("1,200.50 CR"), 1200.50)
        self.assertEqual(parse_amount("-15000"), -15000.0)
        txn = normalize({"date": "2026-11-01", "description": "x",
                         "amount": "250", "dr_cr": "Dr"})
        self.assertEqual(txn["amount"], -250.0)
        txn = normalize({"date": "2026-11-01", "description": "x",
                         "amount": "250", "dr_cr": "CR"})
        self.assertEqual(txn["amount"], 250.0)


class TestPdfAmounts(unittest.TestCase):
    """PDF regex: small rupee amounts match, years never do (#13)."""

    def _txns(self, lines):
        from fincoach.ingest.extract_pdf import txns_from_lines
        return txns_from_lines(lines)

    def test_small_rupee_amount_matches(self):
        txns = self._txns(["2026-11-01 Salary credit Rs 250"])
        self.assertEqual(len(txns), 1)
        self.assertEqual(txns[0]["amount"], "250")

    def test_same_line_date_and_bare_amount(self):
        txns = self._txns(["01-11-2026 UPI Swiggy 450"])
        self.assertEqual(len(txns), 1)
        self.assertEqual(txns[0]["date"], "2026-11-01")
        self.assertEqual(txns[0]["amount"], "450")

    def test_year_is_not_money(self):
        self.assertEqual(
            self._txns(["Annual report 2023 dated 01-11-2026"]), [])
        txns = self._txns(["2026-11-01 Salary Rs 52000"])
        self.assertEqual(txns[0]["amount"], "52000")

    def test_dr_cr_line_markers_set_sign(self):
        txns = self._txns(["01-11-2026 NEFT salary 52000 Cr",
                           "01-11-2026 ATM withdrawal 5000 Dr"])
        valid, _ = validate(normalize_all(txns))
        self.assertEqual(valid[0]["amount"], 52000.0)
        self.assertEqual(valid[1]["amount"], -5000.0)

    @unittest.skipUnless(_HAS_PYPDF, "pypdf not installed")
    def test_extract_pdf_end_to_end(self):
        from fincoach.ingest.extract_pdf import extract_pdf
        txns = extract_pdf(_minimal_pdf_bytes(
            ["2026-11-01 Salary Rs 52000", "02-11-2026 Rent 15000 Dr"]))
        self.assertEqual(len(txns), 2)
        valid, _ = validate(normalize_all(txns))
        self.assertEqual(valid[0]["amount"], 52000.0)
        self.assertEqual(valid[1]["amount"], -15000.0)


class TestOcrScreenshots(unittest.TestCase):
    """OCR: no year-as-money, UPI direction, Paytm/GPay layouts (#4)."""

    def test_gpay_layout(self):
        from fincoach.ingest.extract_image import txns_from_screenshot
        txns = txns_from_screenshot("Paid to Swiggy\nRs 500\n12 Nov 2026\n")
        self.assertEqual(len(txns), 1)
        valid, _ = validate(normalize_all(txns))
        self.assertEqual(valid[0]["amount"], -500.0)
        self.assertEqual(valid[0]["date"], "2026-11-12")
        self.assertIn("Swiggy", valid[0]["description"])

    def test_paytm_layout(self):
        from fincoach.ingest.extract_image import txns_from_screenshot
        txns = txns_from_screenshot(
            "Payment Successful\nRs 250\nTo Tea Stall\n10 Nov 2026\n")
        self.assertEqual(len(txns), 1)
        valid, _ = validate(normalize_all(txns))
        self.assertEqual(valid[0]["amount"], -250.0)
        self.assertEqual(valid[0]["date"], "2026-11-10")

    def test_transaction_pay_is_debit_and_year_ignored(self):
        from fincoach.ingest.extract_image import txns_from_screenshot
        txns = txns_from_screenshot(
            "Transaction: Pay\nRs 1200\n2023\n01-11-2026\n")
        self.assertEqual(len(txns), 1)
        valid, _ = validate(normalize_all(txns))
        self.assertEqual(valid[0]["amount"], -1200.0)
        self.assertEqual(valid[0]["type"], "debit")

    def test_received_screenshot_is_credit(self):
        from fincoach.ingest.extract_image import txns_from_screenshot
        txns = txns_from_screenshot(
            "Money received\nRs 1000\nFrom Mom\n11 Nov 2026\n")
        valid, _ = validate(normalize_all(txns))
        self.assertEqual(valid[0]["amount"], 1000.0)

    @unittest.skipUnless(_HAS_PIL and _HAS_PYTESSERACT,
                         "Pillow/pytesseract not installed")
    def test_extract_image_with_stubbed_ocr(self):
        import pytesseract

        from fincoach.ingest.extract_image import extract_image
        real = pytesseract.image_to_string
        pytesseract.image_to_string = (
            lambda img: "Paid to Swiggy\nRs 500\n12 Nov 2026\n")
        self.addCleanup(setattr, pytesseract, "image_to_string", real)
        txns = extract_image(_png_bytes(), "shot.png")
        valid, _ = validate(normalize_all(txns))
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0]["amount"], -500.0)


class TestPlainTextVerbs(unittest.TestCase):
    """Bare amounts with everyday verbs extract; dates kept (#14)."""

    def test_sent_bare_amount_is_debit(self):
        from datetime import date as _date

        from fincoach.ingest.extract_text import extract_text
        txns = extract_text(b"Sent 250 to my friend gemini")
        self.assertEqual(len(txns), 1)
        valid, _ = validate(normalize_all(txns))
        self.assertEqual(valid[0]["amount"], -250.0)
        self.assertEqual(valid[0]["date"], _date.today().isoformat())

    def test_received_and_got_are_credit(self):
        from fincoach.ingest.extract_text import extract_text
        for line in (b"Received 500 from mom", b"Got 75 cashback"):
            valid, _ = validate(normalize_all(extract_text(line)))
            self.assertEqual(len(valid), 1)
            self.assertGreater(valid[0]["amount"], 0)

    def test_rs_lines_keep_dates(self):
        from fincoach.ingest.extract_text import extract_text
        txns = extract_text(
            "Rs 450 spent via UPI at Swiggy on 12-11-2026".encode("utf-8"))
        valid, _ = validate(normalize_all(txns))
        self.assertEqual(valid[0]["amount"], -450.0)
        self.assertEqual(valid[0]["date"], "2026-11-12")

    def test_plain_noise_extracts_nothing(self):
        from fincoach.ingest.extract_text import extract_text
        self.assertEqual(extract_text(b"hello there friend"), [])


class TestDocxOffline(unittest.TestCase):
    def test_minimal_docx_extracts(self):
        from fincoach.ingest.extract_docx import extract_docx
        txns = extract_docx(_minimal_docx_bytes(
            ["2026-11-01 Salary Rs 52000"]))
        valid, _ = validate(normalize_all(txns))
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0]["amount"], 52000.0)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestUploadIngest(unittest.TestCase):
    """Sources 'Upload & ingest' for every kind + friendly failures (#2)."""

    def setUp(self):
        import io as _io
        self._io = _io
        flask_app.config["TESTING"] = True
        _use_stub_auth()
        self.client = flask_app.test_client()
        self.client.post("/signup", data={
            "email": "upload@x.co", "password": "password123"})

    def _upload_file(self, kind, payload, filename, **form):
        data = {"kind": kind,
                "file": (self._io.BytesIO(payload), filename)}
        data.update(form)
        return self.client.post("/sources/upload", data=data,
                                follow_redirects=True,
                                content_type="multipart/form-data")

    def test_upload_csv(self):
        r = self._upload_file(
            "csv", b"date,description,amount\n2026-11-01,Salary,52000\n"
                   b"2026-11-02,Rent,-15000\n", "s.csv")
        self.assertIn("Ingested 2 transactions", r.get_data(as_text=True))

    def test_upload_sms(self):
        r = self.client.post("/sources/upload", data={
            "kind": "sms_text",
            "sms_text": "Rs 450 spent via UPI at Swiggy on 12-11-2026."},
            follow_redirects=True)
        self.assertIn("Ingested 1 transactions", r.get_data(as_text=True))

    def test_upload_manual(self):
        r = self.client.post("/sources/upload", data={
            "kind": "manual", "date": "2026-11-05",
            "description": "Upload probe", "amount": "-100"},
            follow_redirects=True)
        self.assertIn("Manual entry saved", r.get_data(as_text=True))

    @unittest.skipUnless(_HAS_OPENPYXL, "openpyxl not installed")
    def test_upload_xlsx(self):
        r = self._upload_file("xlsx", _xlsx_bytes([
            ["date", "description", "amount"],
            ["2026-11-01", "Salary", 52000],
            ["2026-11-02", "Rent", -15000],
        ]), "s.xlsx")
        self.assertIn("Ingested 2 transactions", r.get_data(as_text=True))

    @unittest.skipUnless(_HAS_PYPDF, "pypdf not installed")
    def test_upload_pdf(self):
        r = self._upload_file("pdf", _minimal_pdf_bytes(
            ["2026-11-01 Salary Rs 52000", "02-11-2026 Rent 15000 Dr"]),
            "s.pdf")
        body = r.get_data(as_text=True)
        self.assertIn("Ingested 2 transactions", body)
        txns = self.client.get("/transactions").get_data(as_text=True)
        self.assertIn("Salary", txns)

    @unittest.skipUnless(_HAS_PIL and _HAS_PYTESSERACT,
                         "Pillow/pytesseract not installed")
    def test_upload_image_screenshot(self):
        import pytesseract

        real = pytesseract.image_to_string
        pytesseract.image_to_string = (
            lambda img: "Paid to Swiggy\nRs 500\n12 Nov 2026\n")
        self.addCleanup(setattr, pytesseract, "image_to_string", real)
        r = self._upload_file("image", _png_bytes(), "shot.png")
        self.assertIn("Ingested 1 transactions", r.get_data(as_text=True))

    def test_corrupt_xlsx_fails_friendly(self):
        r = self._upload_file("xlsx", b"not a zip", "s.xlsx")
        body = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Ingest failed", body)

    def test_corrupt_pdf_fails_friendly(self):
        r = self._upload_file("pdf", b"not a pdf", "s.pdf")
        body = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Ingest failed", body)

    def test_corrupt_image_fails_friendly(self):
        r = self._upload_file("image", b"not an image", "s.png")
        body = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Ingest failed", body)

    def test_missing_dependency_fails_friendly(self):
        import sys
        from unittest import mock
        with mock.patch.dict(sys.modules, {"openpyxl": None}):
            r = self._upload_file("xlsx", b"whatever", "s.xlsx")
        body = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Ingest failed", body)
        self.assertIn("openpyxl", body)
        with mock.patch.dict(sys.modules, {"pypdf": None}):
            r = self._upload_file("pdf", b"whatever", "s.pdf")
        body = r.get_data(as_text=True)
        self.assertIn("Ingest failed", body)
        self.assertIn("pypdf", body)

    @unittest.skipUnless(_HAS_PIL and _HAS_PYTESSERACT,
                         "Pillow/pytesseract not installed")
    def test_missing_tesseract_binary_fails_friendly(self):
        import pytesseract

        real = pytesseract.image_to_string

        def boom(img):
            raise Exception("tesseract is not installed or it is not in PATH")

        pytesseract.image_to_string = boom
        self.addCleanup(setattr, pytesseract, "image_to_string", real)
        r = self._upload_file("image", _png_bytes(), "shot.png")
        body = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Ingest failed", body)
        self.assertIn("Tesseract", body)

    def test_deferred_ingest_then_reingest(self):
        from fincoach.storage import source_repo
        stub = flask_app.config["SUPABASE_CLIENT"]
        uid = stub.users["upload@x.co"]["id"]
        r = self._upload_file(
            "csv", b"date,description,amount\n2026-11-01,Salary,52000\n",
            "s.csv", ingest_now="off")
        self.assertIn("ingest pending", r.get_data(as_text=True))
        src = source_repo.list(uid)[-1]
        r = self.client.post(f"/sources/{src.id}/ingest",
                             follow_redirects=True)
        self.assertIn("Ingested 1 transactions", r.get_data(as_text=True))


class TestGoalTimeline(unittest.TestCase):
    """Income quickens the goal timeline; only contributions raise saved."""

    def test_income_raises_profile_savings(self):
        base = [
            {"date": "2026-11-01", "description": "Salary",
             "amount": 50000, "category": "salary", "origin": "verified"},
            {"date": "2026-11-02", "description": "Rent",
             "amount": -15000, "category": "rent", "origin": "verified"},
        ]
        before = profile_mod.build_profile(base)["savings"]
        after = profile_mod.build_profile(base + [
            {"date": "2026-11-03", "description": "Bonus",
             "amount": 5000, "category": "salary", "origin": "verified"},
        ])["savings"]
        self.assertGreater(after, before)

    def test_small_income_visibly_shortens_projection(self):
        goal = {"name": "g", "target": 120000, "current": 20000,
                "deadline": ""}
        slow = goals.goal_progress(goal, monthly_savings=20000,
                                   today="2026-11-22")
        fast = goals.goal_progress(goal, monthly_savings=20500,
                                   today="2026-11-22")
        self.assertLess(fast["days_to_goal"], slow["days_to_goal"])
        self.assertLess(fast["projected_date"], slow["projected_date"])

    def test_only_contributions_raise_saved(self):
        from fincoach.storage import sb_data
        client = sb_data.StubDataClient("goal-timeline")
        gid = goals.create_for(client, "g", 120000)
        saved = goals.record_contribution_for(client, gid, 5000)
        self.assertEqual(saved, 5000.0)
        txns = [{"date": "2026-11-01", "description": "Salary",
                 "amount": 50000, "category": "salary",
                 "origin": "verified"}]
        prof_before = profile_mod.build_profile(txns)
        prog_before = goals.goal_progress(client.get("goals", gid),
                                          prof_before["savings"],
                                          today="2026-11-22")
        txns.append({"date": "2026-11-03", "description": "Bonus",
                     "amount": 5000, "category": "salary",
                     "origin": "verified"})
        prof_after = profile_mod.build_profile(txns)
        prog_after = goals.goal_progress(client.get("goals", gid),
                                         prof_after["savings"],
                                         today="2026-11-22")
        self.assertEqual(client.get("goals", gid)["current"], 5000.0)
        self.assertEqual(prog_after["saved"], 5000.0)
        self.assertLess(prog_after["days_to_goal"], prog_before["days_to_goal"])

    def test_days_to_goal_edges(self):
        goal = {"name": "g", "target": 12000, "current": 0, "deadline": ""}
        stalled = goals.goal_progress(goal, monthly_savings=0,
                                      today="2026-11-22")
        self.assertIsNone(stalled["days_to_goal"])
        self.assertIsNone(stalled["projected_date"])
        done = goals.goal_progress({"name": "g", "target": 12000,
                                    "current": 12000, "deadline": ""},
                                   monthly_savings=1000,
                                   today="2026-11-22")
        self.assertEqual(done["days_to_goal"], 0)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestFooterRemoved(unittest.TestCase):
    """#5: the footer site block is gone; the advice guard stays in code."""

    def test_no_footer_site_block(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "fincoach", "frontend", "templates", "base.html"),
                  encoding="utf-8") as fh:
            base = fh.read()
        self.assertNotIn('<footer class="site">', base)
        self.assertNotIn("Financial provenance legend", base)

    def test_safety_guard_still_refuses_advice(self):
        from fincoach import safety
        allowed, refusal = safety.guard("which stock should i buy?")
        self.assertFalse(allowed)
        self.assertIn("can't recommend", refusal)
        self.assertTrue(safety.guard("how am i doing?")[0])


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestHelpPage(unittest.TestCase):
    """#6: Help page with FAQs, Contact us, and a nav entry."""

    def setUp(self):
        flask_app.config["TESTING"] = True
        _use_stub_auth()
        self.client = flask_app.test_client()
        self.client.post("/signup", data={
            "email": "help@x.co", "password": "password123"})

    def test_help_renders_faqs_and_contact(self):
        from fincoach.config import HELP_EMAIL
        body = self.client.get("/help").get_data(as_text=True)
        self.assertIn("Frequently asked questions", body)
        self.assertIn("Contact us", body)
        self.assertIn(HELP_EMAIL, body)
        self.assertIn("support email", body.lower())
        self.assertIn("investment advice", body)

    def test_nav_links_help_and_reminders(self):
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn('href="/help"', body)
        self.assertIn('href="/reminders"', body)

    def test_help_requires_login(self):
        anon = flask_app.test_client()
        r = anon.get("/help", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestProfilePage(unittest.TestCase):
    """Standalone profile page: details, data overview, edit name."""

    def setUp(self):
        flask_app.config["TESTING"] = True
        _use_stub_auth()
        self.client = flask_app.test_client()
        self.client.post("/signup", data={
            "email": "prof@x.co", "password": "password123",
            "name": "Pro Filer", "phone": "555-0100", "currency": "INR"})

    def test_profile_requires_login(self):
        anon = flask_app.test_client()
        r = anon.get("/profile", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_profile_shows_details(self):
        body = self.client.get("/profile").get_data(as_text=True)
        self.assertIn("Pro Filer", body)
        self.assertIn("prof@x.co", body)
        self.assertIn("555-0100", body)
        self.assertIn("User ID", body)
        self.assertIn("Member since", body)
        self.assertIn("Your data", body)
        self.assertIn("0 source(s)", body)

    def test_profile_menu_links_to_page(self):
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn('href="/profile"', body)
        self.assertIn("View profile", body)

    def test_profile_update_name(self):
        r = self.client.post("/profile", data={
            "name": "New Name", "phone": "555-0200",
            "currency": "USD"}, follow_redirects=True)
        body = r.get_data(as_text=True)
        self.assertIn("Profile updated", body)
        self.assertIn("New Name", body)
        self.assertIn("555-0200", body)
        self.assertIn("USD", body)
        stub = flask_app.config["SUPABASE_CLIENT"]
        self.assertEqual(stub.users["prof@x.co"]["name"], "New Name")


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestSignupProfile(unittest.TestCase):
    """#8/#9: signup name/phone/currency + top-right profile menu."""

    def setUp(self):
        flask_app.config["TESTING"] = True
        _use_stub_auth()
        self.client = flask_app.test_client()

    def test_signup_stores_name_and_menu_shows_it(self):
        r = self.client.post("/signup", data={
            "email": "priya@x.co", "password": "password123",
            "name": "Priya Sharma", "phone": "+91 98765 43210",
            "currency": "INR"}, follow_redirects=True)
        body = r.get_data(as_text=True)
        self.assertIn("Account created", body)
        self.assertIn("Priya Sharma", body)
        self.assertIn("Hi, Priya Sharma", body)
        self.assertIn("profile-menu", body)
        self.assertIn("priya@x.co", body)
        stub = flask_app.config["SUPABASE_CLIENT"]
        user = stub.users["priya@x.co"]
        self.assertEqual(user["name"], "Priya Sharma")
        self.assertEqual(user["phone"], "+91 98765 43210")
        self.assertEqual(user["currency"], "INR")
        meta = stub._user("priya@x.co")["user_metadata"]
        self.assertEqual(meta["name"], "Priya Sharma")
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("sb_name"), "Priya Sharma")

    def test_signup_without_name_still_works(self):
        r = self.client.post("/signup", data={
            "email": "noname@x.co", "password": "password123"},
            follow_redirects=True)
        body = r.get_data(as_text=True)
        self.assertIn("Account created", body)
        self.assertIn("noname@x.co", body)

    def test_signup_form_asks_name_phone_currency(self):
        anon = flask_app.test_client()
        body = anon.get("/signup").get_data(as_text=True)
        self.assertIn('name="name"', body)
        self.assertIn('name="phone"', body)
        self.assertIn('name="currency"', body)

    def test_gotrue_signup_body_carries_metadata(self):
        from fincoach.storage import supabase_auth as sb
        seen = {}

        def fake(method, path, body=None, token=None):
            seen["body"] = body
            return {"user": {"id": "u1", "email": "m@x.co"}, "session": None}

        client = sb.SupabaseAuthClient("https://demo.supabase.co", "anon")
        client._request = fake
        client.sign_up("m@x.co", "password123", name="Mira",
                       phone="123", currency="INR")
        self.assertEqual(seen["body"]["data"]["name"], "Mira")
        self.assertEqual(seen["body"]["data"]["phone"], "123")
        self.assertEqual(seen["body"]["data"]["currency"], "INR")

    def test_display_name_falls_back_to_blank(self):
        from fincoach.storage import supabase_auth as sb
        self.assertEqual(sb.display_name({"email": "a@x.co"}), "")
        self.assertEqual(
            sb.display_name({"user_metadata": {"name": "Bo"}}), "Bo")


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestReviewRangeCsv(unittest.TestCase):
    """#10: timeline viewer (month range) + CSV download + printable view."""

    def setUp(self):
        flask_app.config["TESTING"] = True
        _use_stub_auth()
        self.client = flask_app.test_client()
        self.client.post("/signup", data={
            "email": "review@x.co", "password": "password123"})
        stub = flask_app.config["SUPABASE_CLIENT"]
        seed.seed(stub.users["review@x.co"]["id"])

    def test_timeline_viewer_range(self):
        body = self.client.get(
            "/review?start=2026-08&end=2026-09").get_data(as_text=True)
        self.assertIn("Timeline viewer", body)
        self.assertIn("Aug 2026", body)
        self.assertIn("Sep 2026", body)
        self.assertIn("Range total", body)
        self.assertIn("Download CSV", body)
        self.assertIn("Print view", body)
        self.assertIn('name="start"', body)
        self.assertIn('name="end"', body)

    def test_reversed_range_is_swapped(self):
        body = self.client.get(
            "/review?start=2026-09&end=2026-08").get_data(as_text=True)
        self.assertIn("Range total", body)
        self.assertIn("Aug 2026", body)

    def test_csv_download(self):
        r = self.client.get("/review/report.csv?start=2026-08&end=2026-09")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r.headers["Content-Type"])
        self.assertIn("attachment", r.headers["Content-Disposition"])
        text = r.get_data(as_text=True)
        self.assertIn("month,income,expenses,savings", text)
        self.assertIn("2026-08", text)
        self.assertIn("2026-09", text)
        self.assertNotIn("2026-10", text.split("month,category")[0])
        self.assertIn("month,category,spending", text)

    def test_csv_defaults_to_all_months(self):
        text = self.client.get("/review/report.csv").get_data(as_text=True)
        for month in ("2026-08", "2026-09", "2026-10", "2026-11"):
            self.assertIn(month, text)

    def test_csv_requires_login(self):
        anon = flask_app.test_client()
        r = anon.get("/review/report.csv", follow_redirects=False)
        self.assertEqual(r.status_code, 302)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestArchiveFlow(unittest.TestCase):
    """#15: Delete-vs-Archive popup, archived section, delete semantics."""

    def setUp(self):
        flask_app.config["TESTING"] = True
        _use_stub_auth()
        self.client = flask_app.test_client()
        self.client.post("/signup", data={
            "email": "arch@x.co", "password": "password123"})
        stub = flask_app.config["SUPABASE_CLIENT"]
        self.uid = stub.users["arch@x.co"]["id"]
        seed.seed(self.uid)
        from fincoach.storage import source_repo
        self.src_id = source_repo.list(self.uid)[0].id

    def test_delete_popup_offers_delete_and_archive(self):
        body = self.client.get("/sources").get_data(as_text=True)
        self.assertIn("data-open-dialog", body)
        self.assertIn(f"/sources/{self.src_id}/archive", body)
        self.assertIn(f"/sources/{self.src_id}/delete", body)
        self.assertIn("Archived Sources (0)", body)

    def test_archive_hides_and_restore_brings_back(self):
        r = self.client.post(f"/sources/{self.src_id}/archive",
                             follow_redirects=True)
        body = r.get_data(as_text=True)
        self.assertIn("archived", body)
        self.assertIn("Archived Sources (1)", body)
        self.assertIn("Source repository (0)", body)
        self.assertIn("Add to Source", body)
        self.assertIn("0 transactions",
                      self.client.get("/").get_data(as_text=True))
        r = self.client.post(f"/sources/{self.src_id}/restore",
                             follow_redirects=True)
        self.assertIn("restored", r.get_data(as_text=True))
        body = self.client.get("/sources").get_data(as_text=True)
        self.assertIn("Archived Sources (0)", body)
        self.assertIn("Source repository (1)", body)
        self.assertIn("60 transactions",
                      self.client.get("/").get_data(as_text=True))

    def test_delete_removes_source_but_keeps_unlinked_txns(self):
        from fincoach.storage import sb_data, source_repo
        from fincoach.config import user_sources_dir
        client = sb_data.StubDataClient(self.uid)
        before = len(list(user_sources_dir(self.uid).iterdir()))
        self.assertGreater(before, 0)
        r = self.client.post(f"/sources/{self.src_id}/delete",
                             follow_redirects=True)
        self.assertIn("deleted from the database",
                      r.get_data(as_text=True))
        self.assertEqual(source_repo.list(self.uid), [])
        self.assertEqual(list(user_sources_dir(self.uid).iterdir()), [])
        txns = client.rows("transactions")
        self.assertEqual(len(txns), 60)
        self.assertTrue(all(t.get("source_id") is None for t in txns))
        self.assertIn("60 transactions",
                      self.client.get("/").get_data(as_text=True))

    def test_restore_missing_source_flashes_error(self):
        r = self.client.post("/sources/9999/restore", follow_redirects=True)
        self.assertIn("No source 9999", r.get_data(as_text=True))
        r = self.client.post("/sources/9999/archive", follow_redirects=True)
        self.assertIn("No source 9999", r.get_data(as_text=True))


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestArchiveReflectsEverywhere(unittest.TestCase):
    """#16: every page reflects archive/restore/upload."""

    def setUp(self):
        flask_app.config["TESTING"] = True
        _use_stub_auth()
        self.client = flask_app.test_client()
        self.client.post("/signup", data={
            "email": "every@x.co", "password": "password123"})
        stub = flask_app.config["SUPABASE_CLIENT"]
        self.uid = stub.users["every@x.co"]["id"]
        seed.seed(self.uid)
        from fincoach.storage import source_repo
        self.src_id = source_repo.list(self.uid)[0].id

    def _pages(self):
        return {
            "dashboard": self.client.get("/").get_data(as_text=True),
            "sources": self.client.get("/sources").get_data(as_text=True),
            "transactions": self.client.get("/transactions")
            .get_data(as_text=True),
            "recurring": self.client.get("/recurring").get_data(as_text=True),
            "goals": self.client.get("/goals").get_data(as_text=True),
            "simulate": self.client.get("/simulate").get_data(as_text=True),
            "review": self.client.get("/review").get_data(as_text=True),
            "assistant": self.client.post(
                "/assistant", data={"q": "list my subscriptions"},
                follow_redirects=True).get_data(as_text=True),
        }

    def test_seeded_pages_show_data(self):
        pages = self._pages()
        self.assertIn("60 transactions", pages["dashboard"])
        self.assertIn("demo.csv", pages["sources"])
        self.assertIn("Flat rent", pages["transactions"])
        self.assertNotIn("No recurring payments detected", pages["recurring"])
        self.assertIn("Emergency fund", pages["goals"])
        self.assertNotIn("Rs 0/month", pages["simulate"])
        self.assertIn("Nov 2026", pages["review"])
        self.assertNotIn("No recurring payments detected yet",
                         pages["assistant"])

    def test_archived_pages_show_empty(self):
        self.client.post(f"/sources/{self.src_id}/archive")
        pages = self._pages()
        self.assertIn("0 transactions", pages["dashboard"])
        self.assertIn("Archived Sources (1)", pages["sources"])
        self.assertIn("No transactions yet", pages["transactions"])
        self.assertIn("No recurring payments detected", pages["recurring"])
        self.assertIn("Rs 0", pages["goals"])
        self.assertIn("Rs 0/month", pages["simulate"])
        self.assertIn("No months of data yet", pages["review"])
        self.assertIn("No recurring payments detected yet", pages["assistant"])

    def test_restore_brings_everything_back(self):
        self.client.post(f"/sources/{self.src_id}/archive")
        self.client.post(f"/sources/{self.src_id}/restore")
        pages = self._pages()
        self.assertIn("60 transactions", pages["dashboard"])
        self.assertIn("Flat rent", pages["transactions"])
        self.assertIn("Nov 2026", pages["review"])

    def test_upload_after_archive_shows_new_txn(self):
        self.client.post(f"/sources/{self.src_id}/archive")
        self.client.post("/sources/upload", data={
            "kind": "manual", "date": "2026-11-05",
            "description": "Fresh upload", "amount": "-100"})
        self.assertIn("1 transactions",
                      self.client.get("/").get_data(as_text=True))
        self.assertIn("Fresh upload", self.client.get("/transactions")
                      .get_data(as_text=True))


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestSimulateCharts(unittest.TestCase):
    """#17: bigger Baseline-vs-Scenario chart, enlarge, chart-type dropdown."""

    def setUp(self):
        flask_app.config["TESTING"] = True
        _use_stub_auth()
        self.client = flask_app.test_client()
        self.client.post("/signup", data={
            "email": "sim@x.co", "password": "password123"})

    def test_chart_controls_render(self):
        body = self.client.get("/simulate").get_data(as_text=True)
        self.assertIn("data-sim-chart-type", body)
        for value in ("bar", "line", "pie", "donut", "histogram"):
            self.assertIn(f'value="{value}"', body)
        self.assertNotIn('value="gantt"', body)
        self.assertIn("chart-lg", body)
        self.assertIn("data-sim-enlarge", body)
        self.assertIn('id="sim-enlarged"', body)
        self.assertIn("data-sim-chart-big", body)

    def test_renderers_exist_and_gantt_note_in_source(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "fincoach", "frontend", "static", "app.js"),
                  encoding="utf-8") as fh:
            js = fh.read()
        for fn in ("renderLine", "renderSimPies", "renderHistogram",
                   "renderSimChart"):
            self.assertIn(f"function {fn}", js)
        with open(os.path.join(root, "fincoach", "frontend", "templates",
                               "simulate.html"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("gantt", src.lower())
        self.assertNotIn('value="gantt"', src)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestSearchBoxes(unittest.TestCase):
    """#18: search boxes on Transactions and Sources lists."""

    def setUp(self):
        flask_app.config["TESTING"] = True
        _use_stub_auth()
        self.client = flask_app.test_client()
        self.client.post("/signup", data={
            "email": "search@x.co", "password": "password123"})
        stub = flask_app.config["SUPABASE_CLIENT"]
        seed.seed(stub.users["search@x.co"]["id"])

    def test_search_inputs_present(self):
        txns = self.client.get("/transactions").get_data(as_text=True)
        self.assertIn('type="search"', txns)
        self.assertIn('name="q"', txns)
        sources = self.client.get("/sources").get_data(as_text=True)
        self.assertIn('type="search"', sources)
        self.assertIn('name="q"', sources)
        self.assertIn("data-source-search", sources)

    def test_server_side_filtering(self):
        body = self.client.get("/transactions?q=swiggy") \
            .get_data(as_text=True)
        self.assertIn("Swiggy", body)
        self.assertNotIn("Flat rent", body)
        body = self.client.get("/sources?q=demo.csv").get_data(as_text=True)
        self.assertIn("demo.csv", body)
        body = self.client.get("/sources?q=no-such-file") \
            .get_data(as_text=True)
        self.assertNotIn("demo.csv", body)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestBackToTop(unittest.TestCase):
    """#19: back-to-top button styled as a growing graph."""

    def setUp(self):
        flask_app.config["TESTING"] = True
        _use_stub_auth()
        self.client = flask_app.test_client()
        self.client.post("/signup", data={
            "email": "top@x.co", "password": "password123"})

    def test_button_on_every_page(self):
        for path in ("/", "/sources", "/transactions", "/goals"):
            body = self.client.get(path).get_data(as_text=True)
            self.assertIn('id="to-top"', body)
            self.assertIn("data-to-top", body)
            self.assertIn("Back to top", body)

    def test_growing_graph_styles_and_script(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "fincoach", "frontend", "static", "style.css"),
                  encoding="utf-8") as fh:
            css = fh.read()
        self.assertIn(".to-top", css)
        self.assertIn("@keyframes grow", css)
        with open(os.path.join(root, "fincoach", "frontend", "static", "app.js"),
                  encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("initToTop", js)
        self.assertIn("scrollTo", js)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestChatScroll(unittest.TestCase):
    """#24: chat area scrollable with a cap, auto-scroll to latest."""

    def test_chat_cap_and_autoscroll(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "fincoach", "frontend", "static", "style.css"),
                  encoding="utf-8") as fh:
            css = fh.read()
        self.assertIn("max-height: 420px", css)
        self.assertIn("overflow-y: auto", css)
        with open(os.path.join(root, "fincoach", "frontend", "static", "app.js"),
                  encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("chat.scrollTop = chat.scrollHeight", js)
        flask_app.config["TESTING"] = True
        _use_stub_auth()
        client = flask_app.test_client()
        client.post("/signup", data={
            "email": "chat@x.co", "password": "password123"})
        body = client.get("/assistant").get_data(as_text=True)
        self.assertIn("data-chat", body)


class TestReminderLogic(unittest.TestCase):
    """#22/#23: bill auto-detection, lead windows, validation (offline)."""

    def test_auto_detect_finds_regular_bills(self):
        from fincoach.intelligence import reminders as rem
        seed.seed("rem-bills")
        conn = db.migrate(user_db_path("rem-bills"))
        try:
            txns = [dict(r) for r in conn.execute("SELECT * FROM transactions")]
        finally:
            conn.close()
        bills = rem.auto_detect(txns, today="2026-11-22")
        self.assertGreaterEqual(len(bills), 1)
        merchants = " ".join(b["merchant"] for b in bills)
        self.assertIn("netflix", merchants)
        bill = bills[0]
        self.assertIn("next_date", bill)
        self.assertIn(bill["status"], ("active", "changed", "missing"))

    def test_lead_windows(self):
        from fincoach.intelligence import reminders as rem
        base = {"text": "Rent", "amount": 15000, "due_date": "2026-11-25",
                "lead": "1day", "done": False}
        self.assertTrue(rem.is_due(base, today="2026-11-24"))
        self.assertFalse(rem.is_due(base, today="2026-11-20"))
        self.assertTrue(rem.is_due(dict(base, due_date="2026-11-10"),
                                   today="2026-11-24"))  # overdue
        self.assertFalse(rem.is_due(dict(base, done=True),
                                    today="2026-11-24"))
        self.assertFalse(rem.is_due(dict(base, due_date=""),
                                    today="2026-11-24"))
        week = dict(base, lead="1week")
        self.assertTrue(rem.is_due(week, today="2026-11-20"))
        self.assertFalse(rem.is_due(week, today="2026-11-10"))
        due = rem.due_reminders([dict(base, id=2, due_date="2026-11-25"),
                                 dict(base, id=1, due_date="2026-11-10")],
                                today="2026-11-24")
        self.assertEqual([r["id"] for r in due], [1, 2])

    def test_days_until_edges(self):
        from fincoach.intelligence import reminders as rem
        self.assertIsNone(rem.days_until("", today="2026-11-24"))
        self.assertIsNone(rem.days_until("not-a-date", today="2026-11-24"))
        self.assertEqual(rem.days_until("2026-11-24", today="2026-11-24"), 0)

    def test_validation_rejects_bad_input(self):
        from fincoach.intelligence import reminders as rem
        with self.assertRaises(ValueError):
            rem.validate("", 10, "2026-11-25", "1day")
        with self.assertRaises(ValueError):
            rem.validate("Rent", -5, "2026-11-25", "1day")
        with self.assertRaises(ValueError):
            rem.validate("Rent", 10, "", "1day")
        with self.assertRaises(ValueError):
            rem.validate("Rent", 10, "2026-11-25", "1month")
        row = rem.validate("Rent", "15000", "2026-11-25", "2day")
        self.assertEqual(row["amount"], 15000.0)

    def test_stub_crud_roundtrip(self):
        from fincoach.intelligence import reminders as rem
        from fincoach.storage import sb_data
        client = sb_data.StubDataClient("rem-crud")
        row = rem.create_for(client, "Rent", 15000, "2026-11-25",
                             lead="2day")
        self.assertEqual(row["text"], "Rent")
        self.assertFalse(row["done"])
        self.assertEqual(len(rem.list_for(client)), 1)
        updated = rem.mark_done_for(client, row["id"])
        self.assertTrue(updated["done"])
        rem.delete_for(client, row["id"])
        self.assertEqual(rem.list_for(client), [])


class TestMailerOffline(unittest.TestCase):
    """#22/#23: stdlib SMTP mailer with test-mode capture (zero network)."""

    def test_capture_records_instead_of_sending(self):
        from fincoach.services import mailer
        capture: list = []
        record = mailer.send_email("u@x.co", "Hi", "Body", capture=capture)
        self.assertEqual(len(capture), 1)
        self.assertEqual(capture[0]["to"], "u@x.co")
        self.assertEqual(record["subject"], "Hi")

    def test_digest_helpers(self):
        from fincoach.services import mailer
        self.assertIn("1 bill reminder", mailer.digest_subject(1))
        self.assertIn("3 bill reminder", mailer.digest_subject(3))
        body = mailer.digest_body([{"text": "Rent", "amount": 15000,
                                    "due_date": "2026-11-25"}])
        self.assertIn("Rent", body)
        self.assertIn("15,000", body)
        self.assertIsNone(mailer.send_due_digest("u@x.co", [], capture=[]))

    def test_unconfigured_send_raises_setup_hint(self):
        from fincoach import config
        from fincoach.services import mailer
        old = config.SMTP_HOST
        config.SMTP_HOST = ""
        try:
            self.assertFalse(mailer.is_configured())
            with self.assertRaises(ValueError) as ctx:
                mailer.send_email("u@x.co", "s", "b")
            self.assertIn("SMTP_HOST", str(ctx.exception))
        finally:
            config.SMTP_HOST = old

    def test_smtp_failure_maps_to_value_error(self):
        import smtplib
        from unittest import mock

        from fincoach import config
        from fincoach.services import mailer
        old = (config.SMTP_HOST, config.SMTP_USER)
        config.SMTP_HOST, config.SMTP_USER = "mail.test", "me@test"
        try:
            with mock.patch.object(smtplib, "SMTP",
                                   side_effect=OSError("down")):
                with self.assertRaises(ValueError) as ctx:
                    mailer.send_email("u@x.co", "s", "b")
            self.assertIn("Could not send", str(ctx.exception))
        finally:
            config.SMTP_HOST, config.SMTP_USER = old


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestRemindersPages(unittest.TestCase):
    """#22/#23: reminders page flows + digest send (captured, no network)."""

    def setUp(self):
        flask_app.config["TESTING"] = True
        _use_stub_auth()
        self.client = flask_app.test_client()
        self.client.post("/signup", data={
            "email": "remind@x.co", "password": "password123"})
        stub = flask_app.config["SUPABASE_CLIENT"]
        self.uid = stub.users["remind@x.co"]["id"]
        seed.seed(self.uid)

    def tearDown(self):
        flask_app.config.pop("MAIL_CAPTURE", None)

    def test_page_shows_detected_bills_and_form(self):
        body = self.client.get("/reminders").get_data(as_text=True)
        self.assertIn("Detected bills", body)
        self.assertIn("Remind me", body)
        self.assertIn("Add a manual reminder", body)
        self.assertIn('name="lead"', body)
        self.assertIn("1 week before", body)

    def test_manual_create_done_delete(self):
        r = self.client.post("/reminders", data={
            "action": "create", "text": "Test rent", "amount": "15000",
            "due_date": "2026-12-01", "lead": "2day"},
            follow_redirects=True)
        self.assertIn("Reminder saved", r.get_data(as_text=True))
        body = self.client.get("/reminders").get_data(as_text=True)
        self.assertIn("Test rent", body)
        from fincoach.storage import sb_data
        rid = sb_data.StubDataClient(self.uid).rows("reminders")[0]["id"]
        r = self.client.post("/reminders", data={
            "action": "done", "reminder_id": rid, "done": "1"},
            follow_redirects=True)
        self.assertIn("Reminder updated", r.get_data(as_text=True))
        r = self.client.post("/reminders", data={
            "action": "delete", "reminder_id": rid}, follow_redirects=True)
        self.assertIn("Reminder deleted", r.get_data(as_text=True))
        self.assertNotIn("Test rent", self.client.get("/reminders")
                         .get_data(as_text=True))

    def test_create_validation_flashes_error(self):
        r = self.client.post("/reminders", data={
            "action": "create", "text": "", "amount": "10",
            "due_date": "2026-12-01", "lead": "1day"},
            follow_redirects=True)
        self.assertIn("Describe the reminder", r.get_data(as_text=True))

    def test_from_bill_adopts_detected_bill(self):
        r = self.client.post("/reminders", data={
            "action": "from_bill", "merchant": "netflix subscription",
            "amount": "499", "due_date": "2026-12-19", "lead": "1week"},
            follow_redirects=True)
        self.assertIn("Bill reminder saved", r.get_data(as_text=True))

    def test_send_captures_digest_in_test_mode(self):
        from datetime import date
        flask_app.config["MAIL_CAPTURE"] = []
        self.client.post("/reminders", data={
            "action": "create", "text": "Due rent", "amount": "15000",
            "due_date": date.today().isoformat(), "lead": "1day"})
        r = self.client.post("/reminders/send", follow_redirects=True)
        self.assertIn("captured", r.get_data(as_text=True))
        capture = flask_app.config["MAIL_CAPTURE"]
        self.assertEqual(len(capture), 1)
        self.assertEqual(capture[0]["to"], "remind@x.co")
        self.assertIn("Due rent", capture[0]["body"])

    def test_send_with_nothing_due(self):
        flask_app.config["MAIL_CAPTURE"] = []
        r = self.client.post("/reminders/send", follow_redirects=True)
        self.assertIn("Nothing due right now", r.get_data(as_text=True))
        self.assertEqual(flask_app.config["MAIL_CAPTURE"], [])

    def test_archived_sources_have_no_bills(self):
        from fincoach.storage import source_repo
        src_id = source_repo.list(self.uid)[0].id
        self.client.post(f"/sources/{src_id}/archive")
        body = self.client.get("/reminders").get_data(as_text=True)
        self.assertIn("Detected bills (0)", body)


class TestRemindersSchema(unittest.TestCase):
    """Phase 3 schema: reminders amount/lead/kind columns (both backends)."""

    def test_supabase_reminders_columns(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "supabase", "schema.sql"),
                  encoding="utf-8") as fh:
            sql = fh.read()
        for col in ("amount numeric", "lead text", "kind text",
                    "email text"):
            self.assertIn(col, sql)
        self.assertIn("add column if not exists amount", sql)

    def test_stub_reminders_table_exists(self):
        from fincoach.storage import sb_data
        client = sb_data.StubDataClient("rem-schema")
        row = client.add("reminders", {"text": "t", "due_date": "2026-11-25",
                                       "amount": 5, "lead": "1day",
                                       "kind": "manual", "done": False})
        self.assertEqual(row["lead"], "1day")
        self.assertEqual(client.count("reminders"), 1)


class TestGeminiRest(unittest.TestCase):
    """#7: Gemini REST shapes over stdlib urllib, urlopen stubbed."""

    def _patch_urlopen(self, bodies):
        import urllib.request

        calls = []

        class FakeResp:
            def __init__(self, payload):
                self._payload = payload

            def read(self):
                return self._payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake(req, timeout=None):
            calls.append(req)
            body = bodies[min(len(calls) - 1, len(bodies) - 1)]
            return FakeResp(body)

        real = urllib.request.urlopen
        urllib.request.urlopen = fake
        self.addCleanup(setattr, urllib.request, "urlopen", real)
        return calls

    def test_generate_content_request_shape_and_reply(self):
        import json
        from fincoach.assistant import llm as llm_mod
        calls = self._patch_urlopen(
            [b'{"candidates": [{"content": {"parts": '
             b'[{"text": "Hello back"}]}}]}'])
        out = llm_mod.call_gemini("hi", api_key="secret-key",
                                   model="gemini-1.5-flash",
                                   urlopen=None)
        self.assertEqual(out, "Hello back")
        req = calls[0]
        self.assertEqual(req.get_method(), "POST")
        self.assertIn("models/gemini-1.5-flash:generateContent",
                      req.full_url)
        self.assertIn("key=secret-key", req.full_url)
        self.assertEqual(req.get_header("Content-type"), "application/json")
        sent = json.loads(req.data.decode("utf-8"))
        self.assertEqual(sent["contents"][0]["parts"][0]["text"], "hi")

    def test_http_error_maps_to_llm_error(self):
        import io
        import urllib.error
        import urllib.request
        from fincoach.assistant import llm as llm_mod

        def boom(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 400, "x", {}, io.BytesIO(b"{}"))

        real = urllib.request.urlopen
        urllib.request.urlopen = boom
        self.addCleanup(setattr, urllib.request, "urlopen", real)
        with self.assertRaises(llm_mod.LlmError):
            llm_mod.call_gemini("hi", api_key="k", model="m")

    def test_empty_candidates_and_missing_key_raise(self):
        from fincoach.assistant import llm as llm_mod
        self._patch_urlopen([b"{}"])
        with self.assertRaises(llm_mod.LlmError):
            llm_mod.call_gemini("hi", api_key="k", model="m")
        with self.assertRaises(llm_mod.LlmError):
            llm_mod.call_gemini("hi", api_key="", model="m")


class TestLlmContext(unittest.TestCase):
    """#7: per-question context slices are relevant and capped."""

    def _ctx(self):
        seed.seed("test-llm-ctx")
        conn = db.migrate(user_db_path("test-llm-ctx"))
        try:
            txns = [dict(r) for r in conn.execute("SELECT * FROM transactions")]
            rec = recurring_mod.detect(txns)
            prof = profile_mod.build_profile(txns, rec)
            grows = []
            for row in conn.execute("SELECT * FROM goals ORDER BY id"):
                g = dict(row)
                g["progress"] = goals.goal_progress(g, prof["savings"])
                grows.append(g)
        finally:
            conn.close()
        return {"profile": prof, "recurring": rec, "goals": grows,
                "txns": txns,
                "months": sorted({str(t["date"])[:7] for t in txns
                                  if t.get("date")})}

    def test_base_slice_capped(self):
        import json
        from fincoach.assistant import llm as llm_mod
        context = llm_mod.build_context("how am i doing?", self._ctx())
        self.assertLessEqual(len(context["transactions"]), 30)
        self.assertGreater(len(context["transactions"]), 0)
        self.assertLessEqual(len(json.dumps(context, default=str)), 6000)
        small = llm_mod.build_context("how am i doing?", self._ctx(),
                                       max_txns=5)
        self.assertEqual(len(small["transactions"]), 5)
        for key in ("profile", "goals", "recurring", "months",
                    "transactions"):
            self.assertIn(key, context)
        self.assertNotIn("review", context)
        self.assertNotIn("simulation", context)

    def test_review_and_simulation_slices(self):
        from fincoach.assistant import llm as llm_mod
        ctx = self._ctx()
        review_ctx = llm_mod.build_context("review 2026-11", ctx)
        self.assertEqual(review_ctx["review"]["month"], "2026-11")
        self.assertIn("savings_rate", review_ctx["review"])
        sim_ctx = llm_mod.build_context("what if i cut dining by 30%?",
                                        ctx)
        self.assertIn("new_savings", sim_ctx["simulation"])
        self.assertIn("goal_impacts", sim_ctx["simulation"])

    def test_goals_and_recurring_capped(self):
        from fincoach.assistant import llm as llm_mod
        ctx = self._ctx()
        ctx["goals"] = [dict(ctx["goals"][0], name=f"g{i}")
                        for i in range(15)]
        context = llm_mod.build_context("are my goals on track?", ctx)
        self.assertEqual(len(context["goals"]), 10)


class TestLlmEngine(unittest.TestCase):
    """#7: gemini label live, rules fallback, advice never calls the model."""

    def _ctx(self):
        seed.seed("test-llm-eng")
        conn = db.migrate(user_db_path("test-llm-eng"))
        try:
            txns = [dict(r) for r in conn.execute("SELECT * FROM transactions")]
            rec = recurring_mod.detect(txns)
            prof = profile_mod.build_profile(txns, rec)
            grows = []
            for row in conn.execute("SELECT * FROM goals ORDER BY id"):
                g = dict(row)
                g["progress"] = goals.goal_progress(g, prof["savings"])
                grows.append(g)
        finally:
            conn.close()
        return {"profile": prof, "recurring": rec, "goals": grows,
                "txns": txns,
                "months": sorted({str(t["date"])[:7] for t in txns
                                  if t.get("date")})}

    def setUp(self):
        self._old_key = os.environ.pop("GEMINI_API_KEY", None)
        self.addCleanup(self._restore_key)

    def _restore_key(self):
        if self._old_key is not None:
            os.environ["GEMINI_API_KEY"] = self._old_key
        else:
            os.environ.pop("GEMINI_API_KEY", None)

    def test_no_key_falls_back_to_rules(self):
        from fincoach.assistant import llm as llm_mod
        self.assertFalse(llm_mod.is_configured())
        text, engine = llm_mod.answer_with_engine("how am i doing?",
                                                  self._ctx())
        self.assertEqual(engine, "rules")
        self.assertIn("Rs 15,002", text)

    def test_stubbed_call_returns_gemini_label(self):
        from fincoach.assistant import llm as llm_mod
        seen = {}

        def fake(prompt, context):
            seen["prompt"] = prompt
            seen["context"] = context
            return "Gemini says hi"

        text, engine = llm_mod.answer_with_engine("how am i doing?",
                                                  self._ctx(),
                                                  call_fn=fake)
        self.assertEqual((text, engine), ("Gemini says hi", "gemini"))
        self.assertIn("how am i doing?", seen["prompt"])
        self.assertIn("profile", seen["context"])

    def test_call_failure_and_blank_fall_back(self):
        from fincoach.assistant import llm as llm_mod
        ctx = self._ctx()

        def boom(prompt, context):
            raise RuntimeError("down")

        text, engine = llm_mod.answer_with_engine("how am i doing?", ctx,
                                                  call_fn=boom)
        self.assertEqual(engine, "rules")
        self.assertIn("Rs 15,002", text)
        text, engine = llm_mod.answer_with_engine(
            "how am i doing?", ctx, call_fn=lambda p, c: "  ")
        self.assertEqual(engine, "rules")

    def test_advice_refusal_never_calls_model(self):
        from fincoach.assistant import llm as llm_mod
        calls = []

        def fake(prompt, context):
            calls.append(prompt)
            return "buy it!"

        text, engine = llm_mod.answer_with_engine(
            "which stock should i buy?", self._ctx(), call_fn=fake)
        self.assertEqual(engine, "rules")
        self.assertIn("can't recommend", text)
        self.assertEqual(calls, [])


class TestChatActionsParse(unittest.TestCase):
    """#20: the four tool actions parse; ordinary questions do not."""

    def test_add_transaction(self):
        from fincoach.assistant import chat_actions as actions
        found = actions.parse_action(
            "add expense 250 for coffee on 2026-11-20")
        self.assertEqual(found["kind"], "add_transaction")
        self.assertEqual(found["params"]["amount"], -250.0)
        self.assertEqual(found["params"]["date"], "2026-11-20")
        self.assertEqual(found["params"]["description"], "coffee")
        income = actions.parse_action("add income 5000 freelance work")
        self.assertEqual(income["params"]["amount"], 5000.0)
        self.assertIn("Confirm", actions.describe(found))
        self.assertIn("Cancel", actions.describe(found))

    def test_create_goal(self):
        from fincoach.assistant import chat_actions as actions
        found = actions.parse_action(
            "create goal Goa trip 50000 by 2027-06-30")
        self.assertEqual(found["kind"], "create_goal")
        self.assertEqual(found["params"]["name"], "Goa trip")
        self.assertEqual(found["params"]["target"], 50000.0)
        self.assertEqual(found["params"]["deadline"], "2027-06-30")

    def test_record_contribution(self):
        from fincoach.assistant import chat_actions as actions
        found = actions.parse_action("contribute 5000 to Emergency fund")
        self.assertEqual(found["kind"], "record_contribution")
        self.assertEqual(found["params"]["goal"], "Emergency fund")
        self.assertEqual(found["params"]["amount"], 5000.0)
        short = actions.parse_action("add 2000 to Goa trip")
        self.assertEqual(short["kind"], "record_contribution")

    def test_recategorize(self):
        from fincoach.assistant import chat_actions as actions
        by_id = actions.parse_action("recategorize transaction 12 to dining")
        self.assertEqual(by_id["kind"], "recategorize")
        self.assertEqual(by_id["params"]["txn_id"], 12)
        self.assertEqual(by_id["params"]["category"], "dining")
        by_text = actions.parse_action("change Swiggy to groceries")
        self.assertEqual(by_text["params"]["match"], "Swiggy")
        self.assertEqual(by_text["params"]["category"], "groceries")

    def test_ordinary_questions_are_not_actions(self):
        from fincoach.assistant import chat_actions as actions
        for question in ("how am i doing?", "are my goals on track?",
                         "what if i cut dining by 30%?", "review 2026-11",
                         "blargh wibble?", "create goal Goa trip",
                         "add expense", "recategorize Swiggy to moon"):
            self.assertIsNone(actions.parse_action(question), question)


class TestChatActionsExecute(unittest.TestCase):
    """#20: confirmed actions write via the stub transport (offline)."""

    def test_add_transaction_and_duplicate(self):
        from fincoach.assistant import chat_actions as actions
        from fincoach.storage import sb_data
        client = sb_data.StubDataClient("act-add")
        action = actions.parse_action("add expense 250 for coffee")
        out = actions.execute_action(client, action)
        self.assertIn("Added transaction", out)
        rows = client.rows("transactions")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount"], -250.0)
        self.assertTrue(rows[0]["category"])
        again = actions.execute_action(client, action)
        self.assertIn("already in your ledger", again)
        self.assertEqual(client.count("transactions"), 1)

    def test_create_goal(self):
        from fincoach.assistant import chat_actions as actions
        from fincoach.storage import sb_data
        client = sb_data.StubDataClient("act-goal")
        out = actions.execute_action(
            client, actions.parse_action("create goal Goa trip 50000"))
        self.assertIn("Created goal", out)
        self.assertEqual(client.rows("goals")[0]["name"], "Goa trip")

    def test_record_contribution_and_unknown_goal(self):
        from fincoach.assistant import chat_actions as actions
        from fincoach.storage import sb_data
        seed.seed("act-contrib")
        client = sb_data.StubDataClient("act-contrib")
        out = actions.execute_action(
            client, actions.parse_action(
                "contribute 5000 to Emergency fund"))
        self.assertIn("Recorded Rs 5,000", out)
        goal = client.find("goals", name="Emergency fund")[0]
        self.assertEqual(float(goal["current"]), 25000.0)
        with self.assertRaises(ValueError):
            actions.execute_action(
                client, actions.parse_action("contribute 5 to Nope"))

    def test_recategorize_learns_rule(self):
        from fincoach.assistant import chat_actions as actions
        from fincoach.storage import sb_data
        seed.seed("act-recat")
        client = sb_data.StubDataClient("act-recat")
        txn = [t for t in client.rows("transactions")
               if "Netflix" in t["description"]][0]
        out = actions.execute_action(
            client,
            actions.parse_action(
                f"recategorize transaction {txn['id']} to dining"))
        self.assertIn("set to dining", out)
        self.assertEqual(
            client.get("transactions", txn["id"])["category"], "dining")
        pats = " ".join(r["pattern"] for r in client.rows("category_rules"))
        self.assertIn("netflix", pats)
        with self.assertRaises(ValueError):
            actions.execute_action(
                client, actions.parse_action(
                    "recategorize transaction 999999 to dining"))


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestAssistantConfirmFlow(unittest.TestCase):
    """#20: proposals show first; Confirm applies, Cancel drops."""

    def setUp(self):
        flask_app.config["TESTING"] = True
        _use_stub_auth()
        self.client = flask_app.test_client()
        self.client.post("/signup", data={
            "email": "confirm@x.co", "password": "password123"})
        stub = flask_app.config["SUPABASE_CLIENT"]
        self.uid = stub.users["confirm@x.co"]["id"]
        seed.seed(self.uid)

    def test_goal_proposal_then_confirm(self):
        r = self.client.post("/assistant", data={
            "q": "create goal Goa trip 50000"}, follow_redirects=True)
        body = r.get_data(as_text=True)
        self.assertIn("Pending action", body)
        self.assertIn("Goa trip", body)
        self.assertNotIn("Created goal", body)
        goals_before = self.client.get("/goals").get_data(as_text=True)
        self.assertNotIn("Goa trip", goals_before)
        r = self.client.post("/assistant", data={"action": "confirm"},
                             follow_redirects=True)
        self.assertIn("Created goal", r.get_data(as_text=True))
        self.assertIn("Goa trip",
                      self.client.get("/goals").get_data(as_text=True))

    def test_cancel_makes_no_change(self):
        self.client.post("/assistant", data={
            "q": "add expense 250 for chat-txn-xyz"})
        r = self.client.post("/assistant", data={"action": "cancel"},
                             follow_redirects=True)
        self.assertIn("Cancelled", r.get_data(as_text=True))
        self.assertNotIn("chat-txn-xyz", self.client.get("/transactions")
                         .get_data(as_text=True))

    def test_contribution_and_recategorize_by_chat(self):
        from fincoach.storage import sb_data
        self.client.post("/assistant", data={
            "q": "contribute 1000 to Emergency fund"})
        r = self.client.post("/assistant", data={"action": "confirm"},
                             follow_redirects=True)
        self.assertIn("Recorded Rs 1,000", r.get_data(as_text=True))
        txn = [t for t in sb_data.StubDataClient(self.uid)
               .rows("transactions") if "Swiggy" in t["description"]][0]
        self.client.post("/assistant", data={
            "q": f"recategorize transaction {txn['id']} to groceries"})
        r = self.client.post("/assistant", data={"action": "confirm"},
                             follow_redirects=True)
        self.assertIn("set to groceries", r.get_data(as_text=True))

    def test_advice_still_refused_without_pending(self):
        r = self.client.post("/assistant", data={
            "q": "which stock should i buy?"}, follow_redirects=True)
        body = r.get_data(as_text=True)
        self.assertIn("recommend specific stocks", body)
        self.assertNotIn("Pending action", body)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestChatSessions(unittest.TestCase):
    """#21: stored sessions, navigator, revisit, per-user isolation."""

    def setUp(self):
        flask_app.config["TESTING"] = True
        _use_stub_auth()
        self.client = flask_app.test_client()
        self.client.post("/signup", data={
            "email": "chats@x.co", "password": "password123"})
        stub = flask_app.config["SUPABASE_CLIENT"]
        self.uid = stub.users["chats@x.co"]["id"]
        seed.seed(self.uid)

    def _session_ids(self):
        from fincoach.storage import sb_data
        return sorted(r["id"] for r in
                      sb_data.StubDataClient(self.uid)
                      .rows("chat_sessions"))

    def test_ask_revisit_and_new_chat(self):
        self.client.post("/assistant", data={"q": "how am i doing?"})
        first = self._session_ids()[-1]
        self.client.post("/assistant", data={
            "q": "are my goals on track?", "session_id": first})
        self.client.post("/assistant", data={"q": "list my subscriptions"})
        ids = self._session_ids()
        self.assertEqual(len(ids), 2)
        first_page = self.client.get(f"/assistant?session={first}") \
            .get_data(as_text=True)
        self.assertIn('<div class="msg-q">how am i doing?</div>',
                      first_page)
        self.assertIn('<div class="msg-q">are my goals on track?</div>',
                      first_page)
        self.assertNotIn('<div class="msg-q">list my subscriptions</div>',
                         first_page)
        other = [i for i in ids if i != first][0]
        other_page = self.client.get(f"/assistant?session={other}") \
            .get_data(as_text=True)
        self.assertIn('<div class="msg-q">list my subscriptions</div>',
                      other_page)
        self.assertNotIn('<div class="msg-q">how am i doing?</div>',
                         other_page)
        self.assertIn("Chats", other_page)
        self.client.post("/assistant", data={"action": "new"})
        self.assertEqual(len(self._session_ids()), 3)

    def test_sessions_stay_private(self):
        self.client.post("/assistant", data={"q": "how am i doing?"})
        other = flask_app.test_client()
        other.post("/signup", data={
            "email": "chats2@x.co", "password": "password123"})
        body = other.get("/assistant").get_data(as_text=True)
        self.assertNotIn("msg-q", body)
        self.assertIn("No chats yet", body)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestAssistantLayout(unittest.TestCase):
    """#7/#20/#21: knows-panel on top, engine badges, chat columns."""

    def setUp(self):
        flask_app.config["TESTING"] = True
        _use_stub_auth()
        self.client = flask_app.test_client()
        self.client.post("/signup", data={
            "email": "layout@x.co", "password": "password123"})
        stub = flask_app.config["SUPABASE_CLIENT"]
        seed.seed(stub.users["layout@x.co"]["id"])
        self.addCleanup(flask_app.config.pop, "LLM_CALL", None)

    def test_knows_panel_above_conversation(self):
        body = self.client.get("/assistant").get_data(as_text=True)
        self.assertLess(body.index("What it knows"),
                        body.index("<h2>Conversation</h2>"))
        self.assertIn('aria-label="Chats"', body)
        self.assertIn("data-chat", body)
        self.assertIn("Change data by chat", body)

    def test_engine_badges(self):
        flask_app.config["LLM_CALL"] = lambda p, c: "Gemini hi"
        gemini = self.client.post(
            "/assistant", data={"q": "how am i doing?"},
            follow_redirects=True).get_data(as_text=True)
        self.assertIn(">Gemini<", gemini)
        flask_app.config.pop("LLM_CALL", None)
        rules = self.client.post(
            "/assistant", data={"q": "are my goals on track?"},
            follow_redirects=True).get_data(as_text=True)
        self.assertIn("from your data", rules)

    def test_chat_engine_column_both_backends(self):
        from fincoach.storage import sb_data
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "supabase", "schema.sql"),
                  encoding="utf-8") as fh:
            sql = fh.read()
        self.assertIn("engine text", sql)
        self.assertIn("add column if not exists engine", sql)
        client = sb_data.StubDataClient("chat-engine")
        session = client.add("chat_sessions", {"title": "t"})
        row = client.add("chat_messages", {
            "session_id": session["id"], "role": "assistant",
            "content": "hi", "engine": "gemini"})
        self.assertEqual(row["engine"], "gemini")


if __name__ == "__main__":
    unittest.main()
