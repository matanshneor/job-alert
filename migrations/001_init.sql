-- 001_init.sql: initial schema for the job-alert tool.
-- Access model: the app connects directly to Postgres (DATABASE_URL).
-- RLS is enabled on every table with no policies, so Supabase's public REST API cannot read or write anything.

create table if not exists jobs (
  id             bigint generated always as identity primary key,
  job_key        text not null unique,          -- "{ats}:{company_slug}:{external_id}", stable per source
  fingerprint    text not null,                 -- norm(company)|norm(title)|norm(city), cross-source dedup
  source         text not null,                 -- ashby | comeet | lever | workday | ... | gmail_linkedin | gmail_jobify
  company_slug   text not null,
  company        text not null,
  title          text not null,
  location       text,
  country        text,
  is_remote      boolean,
  url            text not null,
  description    text,                          -- plain text
  posted_at      timestamptz,
  first_seen_at  timestamptz not null default now(),
  last_seen_at   timestamptz not null default now(),
  missed_runs    int not null default 0,        -- consecutive successful runs where the job was absent
  status         text not null default 'open' check (status in ('open', 'closed')),
  closed_at      timestamptz,
  -- pending:  waiting to be alerted (phase 2: waiting for AI evaluation)
  -- baseline: existed before the source was first tracked; evaluated once in phase 2
  -- sent:     alert delivered
  -- skipped:  not alerted (duplicate from another source, or rejected by the AI filter)
  notify_status  text not null default 'pending'
                 check (notify_status in ('pending', 'baseline', 'sent', 'skipped')),
  raw            jsonb
);
create index if not exists jobs_fingerprint_idx on jobs (fingerprint);
create index if not exists jobs_source_company_status_idx on jobs (source, company_slug, status);
create index if not exists jobs_notify_pending_idx on jobs (notify_status) where notify_status = 'pending';

-- One row per alert ever sent. unique(job_id) guarantees a job is never alerted twice.
-- Cross-source duplicates (same fingerprint, different source) are blocked in code before inserting.
create table if not exists alerts (
  id                  bigint generated always as identity primary key,
  job_id              bigint not null unique references jobs (id) on delete cascade,
  fingerprint         text not null,            -- used to block the same job arriving from another source
  telegram_message_id bigint,
  message_text        text,                     -- kept so the message can be edited when the job closes
  borderline          boolean not null default false,
  sent_at             timestamptz not null default now()
);
create index if not exists alerts_fingerprint_idx on alerts (fingerprint);

-- Phase 2: AI filter + score results. Created now so the schema stays stable.
create table if not exists evaluations (
  id            bigint generated always as identity primary key,
  job_id        bigint not null unique references jobs (id) on delete cascade,
  decision      text not null check (decision in ('alert', 'borderline', 'reject')),
  reject_reason text,                           -- e.g. 'gap>=5y', 'enrollment_required'
  requirements  jsonb,                          -- [{type, years_required, cv_years, gap}]
  score         int check (score between 0 and 100),
  rubric        jsonb,                          -- {must_have, experience, tools, advantages}
  reason_he     text,
  models        jsonb,                          -- model ids + token usage
  created_at    timestamptz not null default now()
);

-- Health of each company source. Closing logic and failure warnings depend on it.
create table if not exists source_state (
  source_key           text primary key,        -- e.g. "ashby:monday"
  last_success_at      timestamptz,
  last_count           int,
  consecutive_failures int not null default 0,
  last_error           text,
  meta                 jsonb not null default '{}'::jsonb   -- e.g. cached Comeet token
);

create table if not exists runs (
  id          bigint generated always as identity primary key,
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  stats       jsonb,                            -- per source: fetched / kept / new / closed / error
  error       text
);

alter table jobs         enable row level security;
alter table alerts       enable row level security;
alter table evaluations  enable row level security;
alter table source_state enable row level security;
alter table runs         enable row level security;
