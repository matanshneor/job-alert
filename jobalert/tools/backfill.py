"""One-time AI pass over the jobs that existed before tracking started ("baseline").

    uv run python -m jobalert.tools.backfill --sample 5            # try on 5 random jobs, print, save nothing
    uv run python -m jobalert.tools.backfill --evaluate            # evaluate all baseline jobs, save results
    uv run python -m jobalert.tools.backfill --summary             # how many passed, by score
    uv run python -m jobalert.tools.backfill --release --min-score 50
        # relevant jobs with score >= 50 become pending: the hourly run sends them, best first
"""
from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..ai.evaluate import evaluate_job
from ..ai.llm import LLM
from ..ai.profile import load_db_profile
from ..config import load_settings, require_env
from ..db import Database

log = logging.getLogger("jobalert.backfill")


def print_evaluation(row, ev) -> None:
    print(f"\n=== {row['company']} | {row['title']} | {row['location']}")
    print(f"decision: {ev.decision}  score: {ev.score}  rubric: {ev.rubric}  reject: {ev.reject_reason}")
    if ev.gaps_he:
        print(f"gaps: {ev.gaps_he}")
    if ev.reason_he:
        print(f"reason: {ev.reason_he}")
    for req in ev.requirements:
        if req.get("category") == "experience":
            print(f"   - [{'must' if req.get('must_have') else 'nice'}] {req.get('text')} "
                  f"| years {req.get('years_required')} | areas {req.get('matched_areas')} "
                  f"| cv {req.get('cv_years', '-')} gap {req.get('gap', '-')}")
    usage = {k: v for k, v in ev.models.items()}
    print(f"usage: {usage}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser()
    p.add_argument("--sample", type=int, default=0)
    p.add_argument("--evaluate", action="store_true")
    p.add_argument("--limit", type=int, default=1000)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--summary", action="store_true")
    p.add_argument("--release", action="store_true")
    p.add_argument("--min-score", type=int, default=0)
    args = p.parse_args()

    settings = load_settings()
    db = Database(require_env("DATABASE_URL"))

    if args.sample or args.evaluate:
        profile = load_db_profile(db)
        llm = LLM(require_env("ANTHROPIC_API_KEY"))
        if args.sample:
            rows = db.conn.execute(
                """
                select j.id, j.source, j.company, j.title, j.location, j.description, j.fingerprint
                from jobs j where j.notify_status = 'baseline' and j.status = 'open'
                order by random() limit %s
                """,
                (args.sample,),
            ).fetchall()
        else:
            rows = db.jobs_to_evaluate("baseline", args.limit)
        print(f"Evaluating {len(rows)} jobs ({'sample, not saved' if args.sample else 'saving results'})")
        done = 0
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(evaluate_job, llm, profile, row, settings.ai): row for row in rows}
            for future in as_completed(futures):
                row = futures[future]
                try:
                    ev = future.result()
                except Exception as exc:  # noqa: BLE001 - keep going; unevaluated jobs can be retried
                    log.warning("Failed on job %s: %s", row["id"], exc)
                    continue
                done += 1
                if args.sample:
                    print_evaluation(row, ev)
                else:
                    db.save_evaluation(row["id"], ev, None)   # stays 'baseline' until --release
                    if done % 20 == 0:
                        print(f"  {done}/{len(rows)}")
        print(f"Done: {done}/{len(rows)}")

    if args.summary or args.evaluate:
        print("\nBaseline evaluation summary (decision: total | score >=70 | 50-69 | <50):")
        for r in db.baseline_summary():
            print(f"  {r['decision']:10s} {r['n']:4d} | {r['s70']:4d} | {r['s50']:4d} | {r['below50']:4d}")
        print(f"  not evaluated yet: {db.count_to_evaluate('baseline')}")

    if args.release:
        released, skipped = db.release_baseline(args.min_score)
        print(f"Released {released} jobs for alerts (score >= {args.min_score}); {skipped} skipped.")
    db.close()


if __name__ == "__main__":
    main()
