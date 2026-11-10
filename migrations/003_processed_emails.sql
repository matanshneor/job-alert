-- 003_processed_emails.sql: job-alert emails already read (phase 3).
-- An email is recorded only after its jobs were saved, so a crash mid-run just re-reads it.
-- Emails the parser could not read are NOT recorded: they are retried every run until the
-- parser is fixed or they fall out of the look-back window.
create table if not exists processed_emails (
  gmail_id     text primary key,                -- Gmail X-GM-MSGID
  source       text not null,                   -- gmail_linkedin | gmail_jobify
  subject      text,
  received_at  timestamptz,
  jobs_found   int not null,
  processed_at timestamptz not null default now()
);
alter table processed_emails enable row level security;
