-- FinLit Before You Pause: Supabase (Postgres) schema
create extension if not exists pgcrypto with schema extensions;

-- ---------- reference data ----------
create table public.funds (
  scheme_code   text primary key,
  name          text not null,
  category      text not null,
  asset_class   text not null check (asset_class in ('equity','debt','hybrid','gold','other')),
  benchmark_code text
);
create table public.nav_history (
   scheme_code text not null references public.funds(scheme_code),
   nav_date    date not null,
   nav         numeric(14,4) not null check (nav > 0),
   primary key (scheme_code, nav_date)
);

-- ---------- user data ----------
create table public.profiles (
   id uuid primary key references auth.users(id) on delete cascade,
   display_name text,
   language text not null default 'en' check (language in ('en','hi','bn','ta','te','mr')),
   risk_profile text check (risk_profile in ('conservative','moderate','moderately_aggressive','aggressive')),
   risk_fall_tolerance_pct smallint check (risk_fall_tolerance_pct between 1 and 90),
   created_at timestamptz not null default now()
);
create table public.consents (             -- append-only ledger; latest row per (user, purpose) is current
   id uuid primary key default gen_random_uuid(),
   user_id uuid not null references auth.users(id) on delete cascade,
   purpose text not null check (purpose in ('personalised_insights','ai_processing','analytics')),
   notice_version text not null,
   granted boolean not null,
   recorded_at timestamptz not null default now()
);
create index consents_latest_idx on public.consents (user_id, purpose, recorded_at desc);

create table public.holdings (
   id uuid primary key default gen_random_uuid(),
   user_id uuid not null references auth.users(id) on delete cascade,
   scheme_code text not null references public.funds(scheme_code),
   units numeric(18,4) not null check (units >= 0),
   invested_amount numeric(14,2) not null check (invested_amount >= 0),
   as_of date not null,
   unique (user_id, scheme_code, as_of)
);
create table public.goals (
   id uuid primary key default gen_random_uuid(),
   user_id uuid not null references auth.users(id) on delete cascade,
   name text not null,
   target_amount numeric(14,2) not null check (target_amount > 0),
   target_date date not null,
   created_at timestamptz not null default now()
);
create table public.sips (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  scheme_code text not null references public.funds(scheme_code),
  goal_id uuid references public.goals(id) on delete set null,
  amount numeric(12,2) not null check (amount > 0),
  debit_day smallint not null check (debit_day between 1 and 28),
  status text not null default 'active' check (status in ('active','paused','stopped')),
  started_on date not null,
  completed_instalments integer not null default 0 check (completed_instalments >= 0),
  paused_until date
);
create index sips_user_idx on public.sips (user_id, status);

-- ---------- co-pilot ----------
create table public.copilot_payloads (
   id uuid primary key default gen_random_uuid(),
   sip_id uuid not null references public.sips(id) on delete cascade,
   user_id uuid not null references auth.users(id) on delete cascade,
   as_of date not null,
   engine_version text not null,
   payload jsonb not null,
   payload_hash text not null,
   created_at timestamptz not null default now(),
   unique (sip_id, as_of, engine_version)
);
create table public.copilot_sessions (
   id uuid primary key default gen_random_uuid(),
   user_id uuid not null references auth.users(id) on delete cascade,
   sip_id uuid not null references public.sips(id) on delete cascade,
   payload_id uuid references public.copilot_payloads(id),
   action text not null check (action in ('pause','reduce','stop')),
   reason text check (reason in ('need_cash','market_worry','better_option','not_sure')),
   eligibility jsonb not null,
   variant text not null check (variant in ('treatment','holdout','ineligible')),
   started_at timestamptz not null default now(),
   ended_at timestamptz,
   skipped_at_panel smallint
);
create index sessions_cooldown_idx on public.copilot_sessions (sip_id, started_at desc);
create table public.copilot_panel_views (
   id bigint generated always as identity primary key,
   session_id uuid not null references public.copilot_sessions(id) on delete cascade,
   panel text not null check (panel in ('reason','reframe','cost','score','explain','decision')),
   dwell_ms integer not null check (dwell_ms >= 0),
   recorded_at timestamptz not null default now()
);
create table public.copilot_captions (
   id uuid primary key default gen_random_uuid(),
   session_id uuid not null references public.copilot_sessions(id) on delete cascade,
   prompt_version text not null,
   model text not null,
   caption jsonb,
   validator_result jsonb not null,
   served text not null check (served in ('llm','template')),
   latency_ms integer,
   created_at timestamptz not null default now()
);
create table public.decisions (
   id uuid primary key default gen_random_uuid(),
   session_id uuid not null unique references public.copilot_sessions(id),
   user_id uuid not null references auth.users(id) on delete cascade,
   sip_id uuid not null references public.sips(id),
   choice text not null check (choice in ('keep','reduce','pause','stop')),
   new_amount numeric(12,2),
   pause_months smallint check (pause_months between 1 and 12),
   decided_at timestamptz not null default now(),
   recall_answered boolean not null default false,
   recall_correct boolean
);
create table public.checkins (
   id uuid primary key default gen_random_uuid(),
   decision_id uuid not null unique references public.decisions(id) on delete cascade,
   due_on date not null,
   responded_at timestamptz,
   regret_reported boolean,
   restarted_within_90d boolean,
   sip_active_at_90d boolean
);
create table public.experiment_assignments (
   user_id uuid not null references auth.users(id) on delete cascade,
   experiment text not null,
   arm text not null check (arm in ('treatment','holdout')),
   assigned_at timestamptz not null default now(),
   primary key (user_id, experiment)
);
create table public.feature_flags (
   key text primary key,
   enabled boolean not null,
   updated_by text not null,
   updated_at timestamptz not null default now()
);
insert into public.feature_flags (key, enabled, updated_by) values ('copilot_enabled', true, 'system');

-- ---------- append-only, hash-chained audit log ----------
create table public.audit_log (
   id bigint generated always as identity primary key,
   session_id uuid,
   user_id uuid,
   event text not null,
   detail jsonb not null,
   prev_hash text,
   row_hash text not null,
   created_at timestamptz not null default now()
);
create function public.audit_chain() returns trigger language plpgsql as $$
declare last_hash text;
begin
   perform pg_advisory_xact_lock(778899);
   select row_hash into last_hash from public.audit_log order by id desc limit 1;
   new.prev_hash := last_hash;
  new.created_at := now();
  new.row_hash := encode(extensions.digest(coalesce(last_hash,'') || new.event || new.detail::text ||
      new.created_at::text, 'sha256'), 'hex');
  return new;
end $$;
create trigger audit_chain_bi before insert on public.audit_log for each row execute function public.audit_chain();
create function public.audit_immutable() returns trigger language plpgsql as $$
begin raise exception 'audit_log is append-only'; end $$;
create trigger audit_no_change before update or delete on public.audit_log for each row execute function
    public.audit_immutable();
create trigger audit_no_truncate before truncate on public.audit_log for each statement execute function
    public.audit_immutable();
revoke all on public.audit_log from anon, authenticated;

-- ---------- row-level security ----------
alter table public.funds enable row level security;
alter table public.nav_history enable row level security;
create policy funds_read on public.funds for select to authenticated using (true);
create policy nav_read on public.nav_history for select to authenticated using (true);

do $$
declare t text;
begin
  foreach t in array
    array['holdings','goals','sips','copilot_payloads','copilot_sessions','decisions','experiment_assignments','consents']
  loop
    execute format('alter table public.%I enable row level security', t);
    execute format('create policy %I on public.%I for select to authenticated using (user_id = (select auth.uid()))', t ||
         '_own_read', t);
  end loop;
end $$;

alter table public.profiles enable row level security;
create policy profiles_own_read on public.profiles for select to authenticated using (id = (select auth.uid()));
create policy profiles_own_update on public.profiles for update to authenticated using (id = (select auth.uid())) with
    check (id = (select auth.uid()));
create policy consents_own_insert on public.consents for insert to authenticated with check (user_id = (select
    auth.uid()));

alter table public.checkins enable row level security;
create policy checkins_own_read on public.checkins for select to authenticated
  using (exists (select 1 from public.decisions d where d.id = decision_id and d.user_id = (select auth.uid())));
alter table public.copilot_panel_views enable row level security;
alter table public.copilot_captions enable row level security;
alter table public.feature_flags enable row level security;
create policy flags_read on public.feature_flags for select to authenticated using (true);
-- Writes to sessions, payloads, captions, panel_views, decisions, checkins, audit_log: service role only (no client policies).
