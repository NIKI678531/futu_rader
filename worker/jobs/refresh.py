"""Airflow-facing entry point for MarketInsight sync and bounded AI analysis.

Examples::

    python -m jobs.refresh sync --run-id "$AIRFLOW_CTX_DAG_RUN_ID"
    python -m jobs.refresh analyze --mode daily --budget-date 2026-09-25 \
        --calibration-report /config/calibration.json

Database credentials are environment-only so they cannot leak through Airflow's
rendered Bash command or process listing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, select

ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT, ROOT / "worker", ROOT / "backend"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from collection import AiRequest, FutuRefresh, MarketInsightMySqlAdapter, SyncRequest
from jobs.import_dump import pool_codes
from radar_db import make_engine
from radar_db.schema import ai_daily_budget, ingestion_runs


def _source_engine():
    url = os.getenv("MARKET_INSIGHT_DATABASE_URL", "").strip()
    if not url:
        raise ValueError("Missing MARKET_INSIGHT_DATABASE_URL; inject it from an Airflow/Kubernetes Secret")
    return create_engine(url, future=True, pool_pre_ping=True)


def _refresh(with_source=True):
    target = make_engine()
    source = MarketInsightMySqlAdapter(_source_engine(), set(pool_codes())) if with_source else None
    return FutuRefresh(target, source)


def _calibration_report(explicit: Path | None):
    """Load the calibration gate from a CLI path or the runtime Secret.

    Kubernetes Secrets are normally injected as environment values, so the
    production form is inline JSON.  A path remains supported for local runs
    and deployments that mount a read-only Secret/ConfigMap volume.
    """
    if explicit is not None:
        return explicit
    configured = os.getenv("AI_CALIBRATION_REPORT", "").strip()
    if not configured:
        return None
    if configured.startswith("{"):
        report = json.loads(configured)
        if not isinstance(report, dict):
            raise ValueError("AI_CALIBRATION_REPORT inline JSON must be an object")
        return report
    return Path(configured)


def _data_governance_approved() -> bool:
    """Strictly parse the automatic-analysis governance approval Secret."""
    configured = os.getenv("AI_DATA_GOVERNANCE_APPROVED", "").strip().lower()
    if not configured:
        return False
    if configured == "true":
        return True
    if configured == "false":
        return False
    raise ValueError(
        "AI_DATA_GOVERNANCE_APPROVED must be exactly true or false; "
        "automatic AI remains disabled"
    )


def _parser():
    parser = argparse.ArgumentParser(description="Futu Radar automatic source refresh")
    sub = parser.add_subparsers(dest="command", required=True)

    sync = sub.add_parser("sync", help="incrementally synchronize MarketInsight into Radar")
    sync.add_argument("--run-id", help="stable orchestration id; Airflow retries must reuse it")
    sync.add_argument("--through-source-run-id", help="only accept completion declared by this source run")
    sync.add_argument(
        "--mode", choices=("incremental", "backfill", "repair"), default="incremental",
        help="repair recomputes settled 24-hour counters from observation history",
    )
    sync.add_argument("--page-size", type=int, default=1000)
    sync.add_argument("--dry-run", action="store_true")

    analyze = sub.add_parser("analyze", help="run calibrated AI with a persistent HKT-day budget")
    analyze.add_argument("--mode", choices=("auto", "daily", "weekly"), default="auto")
    analyze.add_argument("--budget-date", type=date.fromisoformat)
    analyze.add_argument(
        "--calibration-report",
        type=Path,
        help="validated report path; defaults to AI_CALIBRATION_REPORT (inline JSON or path)",
    )
    analyze.add_argument("--max-http-attempts", type=int, default=500)
    analyze.add_argument("--batch-size", type=int, default=5)
    analyze.add_argument("--concurrency", type=int, default=2)
    analyze.add_argument("--wait-for-ready", action="store_true")
    analyze.add_argument("--wait-timeout-seconds", type=int, default=28800)

    sub.add_parser("status", help="print the latest ingestion run and daily budget")
    return parser


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "sync":
            result = _refresh().sync(SyncRequest(
                mode=args.mode,
                run_id=args.run_id,
                through_source_run_id=args.through_source_run_id,
                page_size=args.page_size,
                dry_run=args.dry_run,
            ))
            print(json.dumps(result.as_dict(), ensure_ascii=False, default=str))
            return 0

        if args.command == "analyze":
            data_governance_approved = _data_governance_approved()
            if not data_governance_approved:
                raise ValueError(
                    "Automatic AI analysis is disabled: set "
                    "AI_DATA_GOVERNANCE_APPROVED=true only after the ADR-0017/ADR-0019 "
                    "data-governance prerequisites are approved"
                )
            budget_date = args.budget_date or datetime.now(ZoneInfo("Asia/Hong_Kong")).date()
            calibration_report = _calibration_report(args.calibration_report)
            mode = args.mode
            if mode == "auto":
                mode = "weekly" if budget_date.weekday() == 0 else "daily"
            result = _refresh(with_source=args.wait_for_ready).analyze(AiRequest(
                budget_date=budget_date,
                mode=mode,
                data_governance_approved=data_governance_approved,
                calibration_report=calibration_report,
                max_http_attempts=args.max_http_attempts,
                batch_size=args.batch_size,
                concurrency=args.concurrency,
                wait_for_ready=args.wait_for_ready,
                wait_timeout_seconds=args.wait_timeout_seconds,
            ))
            print(json.dumps(result.as_dict(), ensure_ascii=False, default=str))
            return 0 if result.status == "complete" else 2

        target = make_engine()
        with target.connect() as conn:
            latest = conn.execute(select(ingestion_runs).order_by(
                ingestion_runs.c.started_at.desc(), ingestion_runs.c.run_id.desc()
            ).limit(1)).mappings().first()
            budgets = conn.execute(select(ai_daily_budget).order_by(
                ai_daily_budget.c.budget_date.desc()
            ).limit(7)).mappings().all()
        print(json.dumps({
            "latestSync": dict(latest) if latest else None,
            "dailyBudgets": [dict(row) for row in budgets],
        }, ensure_ascii=False, default=str, indent=2))
        return 0
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
