# job-alert

Personal job-alert tool. Every hour it checks company career boards (through their ATS public APIs) and job-alert
emails in Gmail (LinkedIn, Jobify), stores jobs in Postgres, and sends new relevant jobs to Telegram.

## Run locally

```bash
uv sync
cp .env.example .env                              # then fill in the values yourself
uv run python -m jobalert.tools.migrate           # create tables (once)
uv run python -m jobalert.run --dry-run           # fetch + filter only, no DB / Telegram
uv run python -m jobalert.run                     # real run
uv run pytest                                     # tests
```

Flags: `--only monday,upwind` (`gmail` = the email sources), `--sample-alerts 3` (send test alerts from stored jobs).

Check Gmail access: `uv run python -m jobalert.tools.gmail_check`.

## How it works

- `config/companies.yaml`: the companies to watch. One collector per ATS, so a new company on a
  supported ATS is one config entry.
- `config/settings.yaml`: location filter (Israel + remote open to Israel), politeness delays, closing rules.
- A source seen for the first time is **baselined**: its existing jobs are stored without alerts.
- Each job has a stable `job_key` (`ats:company:external_id`) and a cross-source `fingerprint`.
  A job is never alerted twice (`alerts.job_id` is unique), and the same job from a second source is skipped.
- A job missing from 2 successful runs in a row is marked closed, and its Telegram alert is edited.
  A source that suddenly returns nothing never closes jobs, and a source failing 3 runs in a row sends a warning.

| ATS | Collector | Companies |
|---|---|---|
| Ashby | `jobalert/collectors/ashby.py` | monday.com, Moon Active |
| Comeet | `jobalert/collectors/comeet.py` | Upwind, ZIM, Fiverr |
| Lever (EU) | `jobalert/collectors/lever.py` | Mobileye |
| Greenhouse | `jobalert/collectors/greenhouse.py` | AppsFlyer, Taboola, Payoneer, NICE, Playtika |
| Eightfold | `jobalert/collectors/eightfold.py` | Amdocs |
| Workday | `jobalert/collectors/workday.py` | Palo Alto Networks (incl. CyberArk), NVIDIA, Intel |
| Oracle Recruiting Cloud | `jobalert/collectors/oracle_hcm.py` | Bank Discount |
| Careers page (HTML) | `jobalert/collectors/mizrahi.py` | Mizrahi-Tefahot |
| SmartRecruiters | `jobalert/collectors/smartrecruiters.py` | Wix |

### Job-alert emails (phase 3)

- Gmail is read over IMAP with an app password, **read-only** (mailbox opened with EXAMINE, bodies
  fetched with BODY.PEEK: nothing is marked read, moved or deleted). Sources are in `settings.yaml` → `email`.
- Parsers: `jobalert/gmail/parsers.py` (LinkedIn text part; Jobify HTML, weekly and legacy layouts).
- Emails have no job description, so these jobs skip the AI and the location filter and are sent as-is,
  marked "📬 ממייל של LinkedIn". They still go through cross-source dedup, so a job already sent from a
  company board isn't sent again (and vice versa).
- Each email is recorded in `processed_emails` once its jobs are saved. An email the parser can't read
  is not recorded, so it is retried every run, and a Telegram warning is sent after 3 failing runs.
- The first run baselines what is already in the inbox (no alerts).

## Deploy (Railway)

Cron service, schedule `0 * * * *`, start command `python main.py`.
Variables: `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ANTHROPIC_API_KEY`,
`GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`.
