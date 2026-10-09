"""Airflow-facing entry point for MarketInsight sync and bounded AI analysis.

Examples::

    python -m jobs.refresh sync --run-id "$AIRFLOW_CTX_DAG_RUN_ID"
    python -m jobs.refresh analyze --mode daily --budget-date 2026-09-25

Database credentials are environment-only so they cannot leak through Airflow's
rendered Bash command or process listing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT, ROOT / "worker", ROOT / "backend"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import clock
from collection import (
    AiRequest,
    FutuRefresh,
    MarketInsightMySqlAdapter,
    SyncRequest,
)
from collection.models import HARD_DAILY_MAX
from collection.source import inspect_source
from jobs.import_dump import pool_codes
from jobs.hkex_catalog import activate_pending_if_receipt_matches
from radar_db import make_engine
from radar_db.schema import ai_daily_budget, ingestion_runs
from radar_db.source_db import make_source_engine


def _source_engine():
    return make_source_engine()


def _refresh(with_source=True, through_source_run_id=None):
    target = make_engine()
    activated = []
    source = None
    if with_source:
        source_engine = _source_engine()
        if through_source_run_id:
            activated = activate_pending_if_receipt_matches(
                target, source_engine, through_source_run_id
            )
        try:
            active_codes = pool_codes(engine=target)
        except TypeError:  # compatibility for injected adapters in operational tests
            active_codes = pool_codes()
        source = MarketInsightMySqlAdapter(source_engine, set(active_codes))
    service = FutuRefresh(target, source)
    service.activated_catalog_codes = tuple(activated)
    return service


def source_configured() -> bool:
    return bool(os.getenv("MARKET_INSIGHT_DATABASE_URL", "").strip())


def check_source(*, codes, sample_size=25):
    source = None
    try:
        source = _source_engine()
        return inspect_source(source, codes, sample_size=sample_size)
    except (SQLAlchemyError, RuntimeError) as exc:
        raise ValueError(
            f"Source inspection failed ({type(exc).__name__}); "
            "check connectivity, SELECT permissions and the source schema"
        ) from None
    finally:
        if source is not None:
            source.dispose()


def latest_closing_run(source, hkt_day: date):
    """The newest successful ``comments_all`` receipt for ``hkt_day`` or the day before."""

    closing = getattr(source, "closing_run", None)
    if closing is None:
        return None
    for day in (hkt_day, hkt_day - timedelta(days=1)):
        receipt = closing(day)
        if receipt:
            return receipt
    return None


def sync_once(*, mode="incremental", run_id=None, through_source_run_id=None, page_size=1000,
              dry_run=False, feed_ids=(), service=None):
    """One MarketInsight → Radar sync, including catalog-driven route rebuilds."""
    from radar_db.source_facts import fact_source

    if fact_source() == "market_insight":
        raise ValueError("Direct-read mode must not materialize source facts; use source_state preparation")

    service = service or _refresh(through_source_run_id=through_source_run_id)
    result = service.sync(SyncRequest(
        mode=mode,
        run_id=run_id,
        through_source_run_id=through_source_run_id,
        page_size=page_size,
        dry_run=dry_run,
        feed_ids=tuple(feed_ids),
    ))
    payload = result.as_dict()
    activated = tuple(getattr(service, "activated_catalog_codes", ()))
    if activated and not dry_run:
        from jobs.backfill_comment_routes import run as rebuild_comment_routes
        route_result = rebuild_comment_routes(
            service.target,
            activate_routes=True,
            preserve_quality_gate=True,
        )
        payload["catalogActivatedCodes"] = list(activated)
        payload["commentRouteDigest"] = route_result["productPoolDigest"]
    return payload


def _data_governance_approved() -> bool:
    """AI runs unless ``AI_DATA_GOVERNANCE_APPROVED`` is explicitly ``false``."""
    configured = os.getenv("AI_DATA_GOVERNANCE_APPROVED", "").strip().lower()
    if not configured:
        return True
    if configured == "true":
        return True
    if configured == "false":
        return False
    raise ValueError(
        "AI_DATA_GOVERNANCE_APPROVED must be exactly true or false; "
        "automatic AI remains disabled"
    )


def _optional_date(value: str) -> date | None:
    return date.fromisoformat(value) if value.strip() else None


def _parser():
    parser = argparse.ArgumentParser(description="Futu Radar automatic source refresh")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check-source", help="inspect source schema and bounded samples without writing")
    check.add_argument("--codes", required=True, help="comma-separated HK product codes to inspect")
    check.add_argument("--sample-size", type=int, default=25, help="feeds per product, from 1 to 100")

    sync = sub.add_parser("sync", help="incrementally synchronize MarketInsight into Radar")
    sync.add_argument("--run-id", help="stable orchestration id; Airflow retries must reuse it")
    sync.add_argument("--through-source-run-id", help="only accept completion declared by this source run")
    sync.add_argument(
        "--mode", choices=("incremental", "backfill", "repair"), default="incremental",
        help="repair recomputes settled 24-hour counters from observation history",
    )
    sync.add_argument("--page-size", type=int, default=1000)
    sync.add_argument(
        "--feed-id",
        type=int,
        action="append",
        default=[],
        help="re-read one current MarketInsight feed row; repeat for a bounded database repair",
    )
    sync.add_argument("--dry-run", action="store_true")

    analyze = sub.add_parser("analyze", help="run AI with a persistent HKT-day request counter")
    analyze.add_argument("--mode", choices=("auto", "daily", "weekly"), default="auto")
    analyze.add_argument("--budget-date", type=date.fromisoformat)
    analyze.add_argument(
        "--anchor",
        type=_optional_date,
        help="analyze this already-complete anchor; empty means latest complete",
    )
    analyze.add_argument("--max-http-attempts", type=int, default=HARD_DAILY_MAX)
    analyze.add_argument("--batch-size", type=int, default=5)
    analyze.add_argument("--concurrency", type=int, default=2)
    analyze.add_argument(
        "--codes",
        help="optional comma-separated product subset for staged backfill",
    )
    analyze.add_argument(
        "--ranges",
        help="optional comma-separated ranges (d0,d1,d2,d7,d14,d30,mtd)",
    )
    analyze.add_argument("--wait-for-ready", action="store_true")
    analyze.add_argument("--wait-timeout-seconds", type=int, default=28800)

    sub.add_parser("status", help="print the latest ingestion run and daily budget")
    return parser


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "check-source":
            payload = check_source(
                codes=tuple(value.strip() for value in args.codes.split(",") if value.strip()),
                sample_size=args.sample_size,
            )
            print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
            return 0 if payload["status"] == "sample_checked" else 2

        if args.command == "sync":
            payload = sync_once(
                mode=args.mode,
                run_id=args.run_id,
                through_source_run_id=args.through_source_run_id,
                page_size=args.page_size,
                dry_run=args.dry_run,
                feed_ids=tuple(args.feed_id),
            )
            print(json.dumps(payload, ensure_ascii=False, default=str))
            return 0

        if args.command == "analyze":
            data_governance_approved = _data_governance_approved()
            if not data_governance_approved:
                raise ValueError(
                    "Automatic AI analysis is disabled: set "
                    "AI_DATA_GOVERNANCE_APPROVED=true only after the ADR-0017/ADR-0019 "
                    "data-governance prerequisites are approved"
                )
            budget_date = args.budget_date or clock.now(ZoneInfo("Asia/Hong_Kong")).date()
            mode = args.mode
            if mode == "auto":
                mode = "weekly" if budget_date.weekday() == 0 else "daily"
            result = _refresh(with_source=args.wait_for_ready).analyze(AiRequest(
                budget_date=budget_date,
                anchor=args.anchor,
                mode=mode,
                data_governance_approved=data_governance_approved,
                max_http_attempts=args.max_http_attempts,
                batch_size=args.batch_size,
                concurrency=args.concurrency,
                wait_for_ready=args.wait_for_ready,
                wait_timeout_seconds=args.wait_timeout_seconds,
                codes=tuple(value.strip() for value in (args.codes or "").split(",") if value.strip()),
                range_keys=tuple(
                    value.strip() for value in (args.ranges or "").split(",") if value.strip()
                ),
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
    from dotenv import load_dotenv

    load_dotenv(ROOT / "worker" / ".env", override=False)
    raise SystemExit(main())
