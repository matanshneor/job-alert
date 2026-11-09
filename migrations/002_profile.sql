-- 002_profile.sql: the candidate profile used by the AI filter and scorer.
-- Kept in the DB (not in git) because it is personal data. Upload with:
--   uv run python -m jobalert.tools.profile --upload
create table if not exists profile (
  key          text primary key,                -- 'default'
  profile_yaml text not null,                   -- structured background (cv/profile.yaml)
  cv_text      text not null,                   -- CV text without contact details (cv/cv.txt)
  updated_at   timestamptz not null default now()
);
alter table profile enable row level security;

create index if not exists evaluations_decision_idx on evaluations (decision);

-- Hebrew summary of experience gaps for borderline alerts, e.g. "פיתוח תוכנה: נדרש 2, יש 0.5".
alter table evaluations add column if not exists gaps_he text;
