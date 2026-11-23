# FinCoach Supabase setup

FinCoach stores all user data (tables + uploaded files) in your own
Supabase project. You only need to do this once.

## 1. Create the tables

1. Open your Supabase project dashboard.
2. Go to **SQL Editor** -> **New query**.
3. Paste the entire contents of `schema.sql` (in this folder) and press
   **Run**.
4. Re-running the same file later is safe (it only adds missing pieces).

This creates the tables `sources`, `transactions`, `category_rules`,
`goals`, `goal_contribs`, `profiles`, `chat_sessions`, `chat_messages`
and `reminders` — each scoped to its owner via `user_id` plus Row Level
Security — and a private Storage bucket named `sources` for uploaded
statement files.

## 2. Point FinCoach at the project

Put these in the project `.env` (see `.env.example`):

```text
SUPABASE_URL=https://xyzcompany.supabase.co
SUPABASE_ANON_KEY=your-anon-public-key
```

`SUPABASE_ANON_KEY` takes the `Publishable` key (`sb_publishable_...`);
the legacy `anon` JWT also works. The app sends this key plus the
logged-in user's access token with every data request, so Supabase
enforces per-user isolation on every row and file.

Optional, for terminal admin tasks only (e.g. `python -m fincoach.storage.seed
--user <uuid>` against the live database): `SUPABASE_SERVICE_KEY` with
the project's `service_role` key. Never expose it to browsers; the web
app never uses it.

## 3. Local mode is tests-only

`FINCOACH_DATA` is **not** a second production backend. When that
variable is set, FinCoach uses an offline SQLite stub transport under
that directory instead of Supabase. The test suite sets it to a temp
folder; do not set it in a real `.env`.
