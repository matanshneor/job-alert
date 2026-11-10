# job-alert

Personal job-alert tool. Every hour it checks company career boards (through their ATS public APIs),
stores jobs in Supabase, and sends new relevant jobs to Telegram.

## Run locally

```bash
uv sync
cp .env.example .env                              # then fill in the values yourself
uv run python -m jobalert.tools.migrate           # create tables (once)
uv run python -m jobalert.run --dry-run           # fetch + filter only, no DB / Telegram
uv run python -m jobalert.run                     # real run
uv run pytest                                     # tests
```

Flags: `--only monday,upwind`, `--sample-alerts 3` (send test alerts from stored jobs).

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
| SmartRecruiters | phase 4 | Wix |

## Deploy (Railway)

Cron service, schedule `0 * * * *`, start command `python main.py`.
Variables: `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
