# FinCoach

Local-first money coach that turns messy statements into calm guidance.
Ingests PDFs, spreadsheets, screenshots, SMS alerts, or manual entries.
Every rupee is labeled verified, estimated, inferred, or projected.
Educational use only: no securities or buy/sell advice, ever.
Your data lives in your own Supabase project, isolated per user.

## Architecture (3 layers)

1. Ingest (`fincoach/ingest/`, `fincoach/storage/source_repo.py`,
   `fincoach/storage/sb_data.py`): each user's original files go to the
   Supabase Storage bucket `sources/`; extractors turn each kind into
   raw rows, then normalize, validate, and dedupe produce canonical
   transactions in Supabase Postgres — every row and file scoped to its
   owner by Row Level Security.
2. Intelligence (`fincoach/intelligence/`: `categorize.py`, `learn.py`,
   `recurring.py`, `profile.py`, `goals.py`, `simulate.py`, `nudges.py`,
   `review.py`): deterministic rules only — no LLM math. Corrections
   become learned rules; profiles, recurring detection, goal
   projections, scenarios, nudges, and reviews are pure functions over
   your transactions.
3. App (`fincoach/app.py`, `fincoach/frontend/`,
   `fincoach/storage/seed.py`): a plain Flask dashboard with sources,
   transactions, recurring, goals, simulator, and monthly review pages,
   plus an idempotent demo seed and a unittest suite.

Project layout: `fincoach/` is the backend package — `ingest/` (file
pipeline), `intelligence/` (money logic), `assistant/` (chat + Gemini),
`storage/` (Supabase data/auth, demo seed), `services/` (email), plus
`app.py`, `config.py`, `models.py`, `safety.py` at the root.
`fincoach/frontend/` holds the UI (`templates/`, `static/`); `tests/`
the suite plus `fixtures/` sample inputs; `supabase/` the SQL schema;
`tmp/` disposable scratch (probe scripts and runs, never committed).

## Setup (Windows)

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # required: add your Supabase URL + anon key
```

macOS/Linux: use `python3 -m venv .venv` and `source .venv/bin/activate`.

## Run

```powershell
.venv\Scripts\activate
python -m fincoach --check   # smoke test: migrate a temp DB, print table counts
python -m flask --app fincoach.app run   # start the dashboard
```

Run these from the project root (`FinCoach/`), then open
http://127.0.0.1:5000 in a browser. (`python fincoach/app.py` also works
from anywhere.) Set a real `SECRET_KEY` in `.env` on any shared machine.

## Login + data (Supabase)

Accounts and financial data live in your Supabase project.

1. Create a project at [Supabase](https://supabase.com), then open
   Settings -> API and copy the Project URL and `Publishable` key
   (`sb_publishable_...`, the replacement for the legacy `anon public`
   JWT — either format works).
2. Run `supabase/schema.sql` once in the dashboard SQL editor (see
   `supabase/README.md`): it creates the tables, the owner-only Row
   Level Security policies, and the private `sources` Storage bucket.
3. Put the credentials in `.env` (see `.env.example`):
   `SUPABASE_URL=https://xyzcompany.supabase.co` and
   `SUPABASE_ANON_KEY=your-anon-public-key`.
4. Sign up from the FinCoach signup page. If your project requires email
   confirmation (Supabase default), confirm via the email first; otherwise
   turn it off under Authentication -> Providers -> Email for instant access.
5. The demo login (`demo@fincoach.local` / `demo1234`) works after creating
   that account once via Sign up.

Every request carries the logged-in user's access token, so each account
starts from an empty dashboard and stays isolated from other accounts.
New users can optionally press **Load demo data** (on the dashboard or
Sources page) to explore with the 60-transaction sample dataset first;
from a terminal, `python -m fincoach.storage.seed --user <user-id>` (with
`SUPABASE_SERVICE_KEY` set) seeds one account the same way.

`FINCOACH_DATA` is test-only: when set, FinCoach uses an offline SQLite
stub instead of Supabase. The test suite sets it to a temp folder; never
set it in a real `.env`.

## Features

- Dashboard: monthly income/expenses/savings, top categories, nudges, goals.
- Sources: upload pdf, docx, csv, xlsx, image, sms_text, or manual entries;
  trigger ingest and see status plus txn/rejected counts. CSV/XLSX `type`
  columns (Debit/Credit, Dr/Cr) set the sign; PDFs read small whole-rupee
  amounts; screenshots (Paytm/GPay layouts) aggregate amount + payee + date
  across OCR lines; SMS accepts bare amounts with everyday verbs
  ("Sent 250 to ..."); corrupt files and missing OCR/extractor packages
  fail as friendly messages, never a crash page. Search the list by name,
  kind, or status.
- Archive vs Delete: the Delete popup offers both. Archive excludes a
  source's transactions from every page's calculations without deleting
  anything; restore it from Archived Sources ("Add to Source"). Delete
  removes the source row and its file from the database; already-extracted
  transactions stay in the ledger with their source link cleared, so
  figures never silently change.
- Transactions: full table with source links and origin badges; correcting a
  category learns a rule for future transactions. Search box filters live
  (typing) and server-side (`?q=`).
- Recurring: detected subscriptions and bills with cadence and change flags.
- Reminders: regular bills auto-detected from history, manual reminders
  with an amount and a 1-day / 2-day / 1-week lead, and one-click email
  digests of whatever is due (see "Bill reminders by email" below).
- Goals: create goals, record contributions, track honest on-track math.
  Projections are day-granular, so new income visibly shortens the
  timeline; only contributions raise the saved total.
- Simulate: what-if dining/rent/income/savings deltas with goal impacts.
  The Baseline-vs-Scenario chart is large, click-to-enlarge, and switchable
  between bar, line, pie, donut, and histogram views (a Gantt chart is
  deliberately omitted — it fits task schedules, not a 3-measure
  comparison).
- Review: per-month income, spending, savings rate, and highlights, plus a
  timeline viewer (custom month range with per-month rows and totals), a
  downloadable CSV report per range, and a printer-friendly view.
- Assistant: ask about your money in plain words ("how am i doing?",
  "what if i cut dining by 30%?") — Gemini answers when a key is set,
  otherwise the deterministic rule engine does; every reply is labelled
  with its engine. Change data by chat ("add expense 250 for coffee",
  "create goal Goa trip 50000", "contribute 5000 to Emergency fund",
  "recategorize Swiggy to dining") — each change shows a proposal and
  runs only after you press Confirm. Conversations are stored per user
  with a Chats navigator for revisiting. The chat area scrolls with a
  capped height and auto-scrolls to the latest reply.
- Help: FAQ page with a Contact us email (set `HELP_EMAIL` in `.env`).
- Login: Supabase Auth (email + password, plus name and optional
  phone/currency stored in user metadata) guards every page; tokens are
  validated per request and refreshed silently on expiry. No Supabase
  dependency is needed — the client is stdlib-only. The top-right
  profile menu shows your name and email with logout.

## Bill reminders by email

Reminders are emailed with the standard library (`smtplib`), no extra
packages. Add these to `.env` (see `.env.example`):

```text
SMTP_HOST = smtp.gmail.com
SMTP_PORT = 587
SMTP_USER = you@gmail.com
SMTP_PASS = your-16-char-app-password
SMTP_FROM = you@gmail.com
```

Gmail no longer accepts your normal password for scripts — create an
app password instead:

1. Turn on 2-Step Verification for your Google account (Google Account
   -> Security -> 2-Step Verification).
2. Open Security -> App passwords (search "app passwords" if hidden),
   create one named e.g. "FinCoach", and copy the 16-character code.
3. Paste the code (no spaces) as `SMTP_PASS`. Any provider works the
   same way — use its SMTP host/port and credentials.

Press **Email due** on the Reminders page to send one digest of every
reminder inside its lead window (overdue counts as due). Without SMTP
settings the page shows a setup hint instead of sending. Tests never
send mail: they pass a capture list (`app.config["MAIL_CAPTURE"]`).

## Gemini assistant (optional)

Without any setup the assistant answers from its built-in rule engine
(`fincoach/assistant/assistant.py`, deterministic figures from your data). To get
natural-language answers from Google Gemini instead:

1. Create a free API key at
   [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Add it to `.env` (see `.env.example`):
   `GEMINI_API_KEY = your-gemini-api-key`.
3. Optionally pin the model: `GEMINI_MODEL = gemini-1.5-flash`
   (the default when unset).

How it works (`fincoach/assistant/llm.py`, stdlib-only HTTPS, no new packages):
each question plus a small capped slice of your own data (profile,
goals, recurring, recent transactions, and — when relevant — a monthly
review and a what-if simulation) is sent to the `generateContent`
endpoint. Replies are labelled **Gemini**; when no key is set or any
API error occurs, the rule engine answers instead (labelled
**from your data**). Stock or buy/sell questions are always refused
before the model is called, and data-change requests always need your
Confirm press. Tests never touch the network: they stub the call.

## Safety note

FinCoach is for educational purposes only. It provides general financial
literacy information and does not provide securities advice or buy/sell
recommendations. Consult a qualified professional before making decisions.
Numbers marked projected are scenarios, not predictions.
