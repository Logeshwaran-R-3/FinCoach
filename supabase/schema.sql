-- FinCoach Supabase schema: Postgres tables + Row Level Security + Storage.
--
-- Run this whole file once in your Supabase dashboard SQL editor
-- (Dashboard -> SQL Editor -> New query -> paste -> Run). It is
-- idempotent: safe to re-run.
--
-- Every table carries user_id so each account stays isolated; RLS
-- policies restrict every row to its owner (auth.uid() = user_id).
-- The API layer always sends the logged-in user's access token plus
-- the anon key, and always filters / writes with that user's id.

-- ---------------------------------------------------------------- sources
create table if not exists public.sources (
    id bigint generated always as identity primary key,
    user_id uuid not null,
    kind text not null,
    filename text not null,
    mime text not null default '',
    size integer not null default 0,
    sha256 text not null default '',
    created_at timestamptz not null default now(),
    status text not null default 'pending'
        check (status in ('pending', 'processing', 'done', 'failed', 'archived')),
    error text not null default '',
    txn_count integer not null default 0,
    rejected_count integer not null default 0,
    storage_path text not null default ''
);
create index if not exists sources_user_id_idx on public.sources (user_id);

-- ----------------------------------------------------------- transactions
create table if not exists public.transactions (
    id bigint generated always as identity primary key,
    user_id uuid not null,
    source_id bigint references public.sources (id) on delete set null,
    date text not null,
    description text not null,
    merchant text not null default '',
    amount numeric not null,
    currency text not null default 'INR',
    type text not null default 'other',
    category text not null default '',
    origin text not null default 'verified'
        check (origin in ('verified', 'estimated', 'inferred', 'projected')),
    note text not null default '',
    hash text not null default '',
    created_at timestamptz not null default now()
);
create index if not exists transactions_user_id_idx on public.transactions (user_id);
create index if not exists transactions_user_date_idx on public.transactions (user_id, date);

-- -------------------------------------------------------- category_rules
create table if not exists public.category_rules (
    id bigint generated always as identity primary key,
    user_id uuid not null,
    pattern text not null,
    category text not null,
    priority integer not null default 0,
    hits integer not null default 0
);
create index if not exists category_rules_user_id_idx on public.category_rules (user_id);

-- ---------------------------------------------------------------- goals
create table if not exists public.goals (
    id bigint generated always as identity primary key,
    user_id uuid not null,
    name text not null,
    target numeric not null,
    "current" numeric not null default 0,
    deadline text not null default '',
    created_at timestamptz not null default now()
);
create index if not exists goals_user_id_idx on public.goals (user_id);

-- ---------------------------------------------------------- goal_contribs
create table if not exists public.goal_contribs (
    id bigint generated always as identity primary key,
    user_id uuid not null,
    goal_id bigint not null references public.goals (id) on delete cascade,
    amount numeric not null,
    date text not null,
    note text not null default ''
);
create index if not exists goal_contribs_user_id_idx on public.goal_contribs (user_id);
create index if not exists goal_contribs_goal_id_idx on public.goal_contribs (goal_id);

-- --------------------------------------------------------------- profiles
create table if not exists public.profiles (
    id bigint generated always as identity primary key,
    user_id uuid not null,
    name text not null,
    data jsonb not null default '{}',
    created_at timestamptz not null default now()
);
create index if not exists profiles_user_id_idx on public.profiles (user_id);

-- --------------------------------------------------------- chat_sessions
create table if not exists public.chat_sessions (
    id bigint generated always as identity primary key,
    user_id uuid not null,
    title text not null default '',
    created_at timestamptz not null default now()
);
create index if not exists chat_sessions_user_id_idx on public.chat_sessions (user_id);

-- --------------------------------------------------------- chat_messages
create table if not exists public.chat_messages (
    id bigint generated always as identity primary key,
    user_id uuid not null,
    session_id bigint not null references public.chat_sessions (id) on delete cascade,
    role text not null default 'user',
    content text not null default '',
    engine text not null default '',
    created_at timestamptz not null default now()
);
create index if not exists chat_messages_user_id_idx on public.chat_messages (user_id);
create index if not exists chat_messages_session_id_idx on public.chat_messages (session_id);
-- Engine label for projects created before the Gemini upgrade (Phase 4).
alter table public.chat_messages add column if not exists engine text not null default '';

-- -------------------------------------------------------------- reminders
create table if not exists public.reminders (
    id bigint generated always as identity primary key,
    user_id uuid not null,
    text text not null default '',
    due_date text not null default '',
    amount numeric not null default 0,
    lead text not null default '1day'
        check (lead in ('1day', '2day', '1week')),
    kind text not null default 'manual'
        check (kind in ('manual', 'bill')),
    email text not null default '',
    done boolean not null default false,
    created_at timestamptz not null default now()
);
create index if not exists reminders_user_id_idx on public.reminders (user_id);
-- Columns for projects created before the reminders upgrade (Phase 3).
alter table public.reminders add column if not exists amount numeric not null default 0;
alter table public.reminders add column if not exists lead text not null default '1day';
alter table public.reminders add column if not exists kind text not null default 'manual';
alter table public.reminders add column if not exists email text not null default '';

-- ------------------------------------------------- Row Level Security
alter table public.sources enable row level security;
alter table public.transactions enable row level security;
alter table public.category_rules enable row level security;
alter table public.goals enable row level security;
alter table public.goal_contribs enable row level security;
alter table public.profiles enable row level security;
alter table public.chat_sessions enable row level security;
alter table public.chat_messages enable row level security;
alter table public.reminders enable row level security;

-- Owner-only policies: a user can only see / change their own rows.
do $$
declare
    t text;
begin
    foreach t in array array[
        'sources', 'transactions', 'category_rules', 'goals',
        'goal_contribs', 'profiles', 'chat_sessions', 'chat_messages',
        'reminders'
    ]
    loop
        execute format(
            'drop policy if exists owner_all on public.%I', t);
        execute format(
            'create policy owner_all on public.%I '
            'for all using (auth.uid() = user_id) '
            'with check (auth.uid() = user_id)', t);
    end loop;
end
$$;

-- ------------------------------------------------- Storage bucket: sources
insert into storage.buckets (id, name, public)
values ('sources', 'sources', false)
on conflict (id) do nothing;

-- Object paths look like "<user-uuid>/<sha>_<filename>"; each user may
-- only touch objects under their own folder.
drop policy if exists "sources owner read" on storage.objects;
create policy "sources owner read" on storage.objects
    for select using (
        bucket_id = 'sources'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

drop policy if exists "sources owner write" on storage.objects;
create policy "sources owner write" on storage.objects
    for insert with check (
        bucket_id = 'sources'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

drop policy if exists "sources owner update" on storage.objects;
create policy "sources owner update" on storage.objects
    for update using (
        bucket_id = 'sources'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

drop policy if exists "sources owner delete" on storage.objects;
create policy "sources owner delete" on storage.objects
    for delete using (
        bucket_id = 'sources'
        and (storage.foldername(name))[1] = auth.uid()::text
    );
