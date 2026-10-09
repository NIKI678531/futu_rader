"""Daily full refresh: latest facts in, every page range out (ADR-0031).

One entry point serves the worker's 08:00 cron, a manual ``--now`` run and the
Airflow pod::

    python -X utf8 -m jobs.daily --now            # 08:00 completion ranges
    python -X utf8 -m jobs.daily --intraday       # 13:00 / 15:00 d0 refresh
    python -X utf8 -m jobs.daily --now --drain    # also execute here until the queue is empty

Steps, each recorded in ``meta_kv['ai_daily_last_run']``:

1. MarketInsight sync when ``MARKET_INSIGHT_DATABASE_URL`` is set.  Without it
   the database is the imported dump and the step is reported as skipped.
2. Price sync for every active pool product when ``FMP_API_KEY`` is set. FMP
   remains primary; an exact identity miss or a safely empty full intraday
   series falls back to Yahoo Finance for both daily and intraday prices.
3. Enqueue the short-range completion set d7, d1 and d2 as
   ``requested_by='schedule'``.  Longer ranges are explicitly selected in the
   management page so they cannot bury the high-priority d7 work.

A failed sync or price step never blocks step 3: AI then runs on the facts
already in the database so the pages are complete for the current anchor.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT, ROOT / "worker", ROOT / "backend"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from sqlalchemy import insert, select, update

import clock
from core.data_clock import analysis_range_anchor
from radar_db import make_engine
from radar_db.analysis_requests import (
    RANGE_PRIORITY,
    enqueue,
    list_active_requests,
    request_followup,
)
from radar_db.product_catalog import load_products
from radar_db.schema import meta_kv

log = logging.getLogger("worker.daily")

HKT = ZoneInfo("Asia/Hong_Kong")
DAILY_RANGES = ("d7", "d1", "d2")
LAST_RUN_KEY = "ai_daily_last_run"
INTRADAY_LAST_RUN_KEY = "ai_intraday_last_run"
INTRADAY_LAST_SLOT_KEY = "ai_intraday_last_enqueued_slot"
PRICE_LOOKBACK_DAYS = 59


def daily_cron(value=None) -> str | None:
    """``AI_DAILY_CRON``（默认 08:00）；``off`` 关闭定时，只保留手动补齐。"""
    raw = os.getenv("AI_DAILY_CRON", "0 8 * * *") if value is None else value
    raw = raw.strip()
    return None if raw.lower() in ("", "off", "false", "none") else raw


def intraday_cron(value=None) -> str | None:
    """Twice-daily partial-day refresh in Hong Kong time."""
    raw = os.getenv("AI_INTRADAY_CRON", "0 13,15 * * *") if value is None else value
    raw = raw.strip()
    return None if raw.lower() in ("", "off", "false", "none") else raw


def _error(exc) -> str:
    return " ".join(str(exc).split())[:500]


def _put_meta(engine, key, value):
    with engine.begin() as conn:
        if not conn.execute(update(meta_kv).where(meta_kv.c.k == key).values(v=value)).rowcount:
            conn.execute(insert(meta_kv).values(k=key, v=value))


def _anchor(engine) -> date | None:
    with engine.connect() as conn:
        value = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "anchor")).scalar()
    return date.fromisoformat(value[:10]) if value else None


def sync_step(today: date) -> dict:
    from jobs import refresh
    from radar_db.source_facts import fact_source, source_state_status

    if fact_source() == "market_insight":
        engine = make_engine()
        try:
            return source_state_status(engine)
        finally:
            engine.dispose()

    if not refresh.source_configured():
        return {"status": "skipped", "mode": "dump",
                "reason": "未配置 MARKET_INSIGHT_DATABASE_URL：本地 dump 模式，只分析库里已有数据"}
    service = refresh._refresh()
    receipt = refresh.latest_closing_run(service.source, today)
    through = receipt["run_id"] if receipt else None
    if through:
        service = refresh._refresh(through_source_run_id=through)
    payload = refresh.sync_once(
        run_id=f"daily-{today:%Y%m%d}-{uuid.uuid4().hex[:8]}",
        through_source_run_id=through,
        service=service,
    )
    return {"status": payload.get("status"), "mode": "production", "throughSourceRunId": through,
            "completeThrough": payload.get("complete_through"),
            "inserted": payload.get("inserted"), "updated": payload.get("updated")}


def intraday_sync_step(at: datetime) -> dict:
    """Run the normal live keyset sync without claiming a complete source day."""
    from jobs import refresh
    from radar_db.source_facts import fact_source

    if fact_source() == "market_insight":
        return sync_step(at.date())

    if not refresh.source_configured():
        return {"status": "skipped", "mode": "dump",
                "reason": "未配置 MARKET_INSIGHT_DATABASE_URL：日内增量同步已跳过"}
    slot = at.astimezone(HKT) if at.tzinfo is not None else at.replace(tzinfo=HKT)
    service = refresh._refresh()
    require_indexes = getattr(service.source, "require_incremental_indexes", None)
    if callable(require_indexes):
        require_indexes()
    payload = refresh.sync_once(
        mode="incremental",
        run_id=f"intraday-{slot:%Y%m%d-%H}",
        service=service,
    )
    return {
        "status": payload.get("status"),
        "mode": "incremental",
        "completeThrough": payload.get("complete_through"),
        "inserted": payload.get("inserted"),
        "updated": payload.get("updated"),
        "rowsRead": payload.get("rows_read"),
    }


def _price_step_through(
    engine,
    through: date,
    *,
    intraday_start: date | None = None,
    force_intraday: bool = False,
) -> dict:
    if not os.getenv("FMP_API_KEY", "").strip():
        return {"status": "skipped", "reason": "未配置 FMP_API_KEY"}
    from jobs import sync_prices
    from market_data.fmp import FmpClient
    from market_data.yahoo import YahooFinanceClient

    codes = [product["code"] for product in load_products(engine=engine)]
    rows = sync_prices.sync(
        engine,
        FmpClient(),
        codes,
        through - timedelta(days=PRICE_LOOKBACK_DAYS),
        through,
        intraday_start=intraday_start,
        force_intraday=force_intraday,
        fallback_client=YahooFinanceClient(),
    )
    return {"status": "ok", "products": len(codes),
            "ok": sum(row["status"] in ("ok", "reused") for row in rows),
            "unavailable": sum(row["status"] == "unavailable" for row in rows)}


def price_step(engine) -> dict:
    anchor = _anchor(engine)
    if anchor is None:
        return {"status": "skipped", "reason": "meta_kv 里没有 anchor"}
    return _price_step_through(engine, anchor)


def intraday_price_step(engine, today: date) -> dict:
    # 15:00 不能复用 13:00 的当天分时成功回执；只重拉当天分时，历史日线仍复用缓存。
    return _price_step_through(
        engine,
        today,
        intraday_start=today,
        force_intraday=True,
    )


def run_daily(engine=None, *, ranges=DAILY_RANGES, sync=sync_step, prices=price_step) -> dict:
    """Run the three steps once; returns and persists the step report."""

    engine = engine or make_engine()
    started = clock.now(HKT)
    report = {"startedAt": started.isoformat(timespec="seconds"), "ranges": list(ranges)}
    try:
        report["sync"] = sync(started.date())
    except Exception as exc:  # noqa: BLE001
        log.exception("每日同步失败，继续在现有数据上分析")
        report["sync"] = {"status": "failed", "error": _error(exc)}
    try:
        report["prices"] = prices(engine)
    except Exception as exc:  # noqa: BLE001
        log.exception("行情同步失败，继续分析")
        report["prices"] = {"status": "failed", "error": _error(exc)}
    queued = []
    for range_key in ranges:
        request, created = enqueue(engine, range_key, requested_by="schedule")
        queued.append({"range": range_key, "id": request["id"], "created": created})
    report["queued"] = queued
    anchor = _anchor(engine)
    report["anchor"] = anchor.isoformat() if anchor else None
    report["finishedAt"] = clock.now(HKT).isoformat(timespec="seconds")
    _put_meta(engine, LAST_RUN_KEY, json.dumps(report, ensure_ascii=False, default=str))
    log.info("每日任务：同步 %s，行情 %s，入队 %s",
             report["sync"]["status"], report["prices"]["status"],
             ",".join(item["range"] for item in queued))
    return report


def run_intraday(
    engine=None,
    *,
    scheduled_at: datetime | None = None,
    sync=intraday_sync_step,
    prices=intraday_price_step,
) -> dict:
    """Refresh current-day facts/prices and enqueue exactly one d0 analysis."""
    engine = engine or make_engine()
    started = scheduled_at or clock.now(HKT)
    if started.tzinfo is None:
        started = started.replace(tzinfo=HKT)
    else:
        started = started.astimezone(HKT)
    slot = started.strftime("%Y-%m-%dT%H")
    report = {"startedAt": started.isoformat(timespec="seconds"), "slot": slot, "ranges": ["d0"]}
    try:
        report["sync"] = sync(started)
    except Exception as exc:  # noqa: BLE001
        log.exception("日内增量同步失败，尝试使用已有当天数据")
        report["sync"] = {"status": "failed", "error": _error(exc)}
    complete_anchor = _anchor(engine)
    data_date = analysis_range_anchor(engine, "d0", complete_anchor)
    report["dataDate"] = data_date.isoformat() if data_date else None
    if data_date is None:
        report["prices"] = {
            "status": "skipped",
            "reason": "当天社区数据尚未同步，无法确定 d0 数据日",
        }
    else:
        try:
            report["prices"] = prices(engine, data_date)
        except Exception as exc:  # noqa: BLE001
            log.exception("当天行情同步失败，继续处理社区数据")
            report["prices"] = {"status": "failed", "error": _error(exc)}
    with engine.connect() as conn:
        last_slot = conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == INTRADAY_LAST_SLOT_KEY
        )).scalar_one_or_none()
    if data_date is None:
        report["analysis"] = {
            "status": "unavailable",
            "reason": "当天数据尚未同步，未创建 d0 分析任务",
        }
        report["queued"] = []
    elif last_slot == slot:
        report["analysis"] = {"status": "deduplicated", "reason": "该日内时段已提交"}
        report["queued"] = []
    else:
        request, created = enqueue(engine, "d0", requested_by="schedule")
        followup = False
        if not created:
            request_followup(engine, "d0", requested_by="schedule")
            followup = True
        _put_meta(engine, INTRADAY_LAST_SLOT_KEY, slot)
        report["analysis"] = {
            "status": "queued" if created else "followup_queued",
            "followupRequested": followup,
        }
        report["queued"] = [{"range": "d0", "id": request["id"], "created": created}]

    report["anchor"] = complete_anchor.isoformat() if complete_anchor else None
    report["finishedAt"] = clock.now(HKT).isoformat(timespec="seconds")
    _put_meta(engine, INTRADAY_LAST_RUN_KEY, json.dumps(report, ensure_ascii=False, default=str))
    log.info("日内任务 %s：同步 %s，行情 %s，分析 %s",
             slot, report["sync"]["status"], report["prices"]["status"],
             report["analysis"]["status"])
    return report


def drain(engine, *, process=None, sleep=time.sleep) -> list[dict]:
    """Execute queued requests in this process until none is active."""

    if process is None:
        from jobs.admin_analysis import process_next as process
    results = []
    while True:
        result = process(engine=engine)
        if result is None:
            if not list_active_requests(engine):
                return results
            # Another consumer holds the remaining request; wait for it.
            sleep(30)
            continue
        results.append(result)


def main(argv=None):
    parser = argparse.ArgumentParser(description="每日全量更新：同步、行情、D7/D1/D2 AI")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--now", action="store_true", help="立即执行一次 08:00 完整日链路")
    mode.add_argument("--intraday", action="store_true", help="立即执行一次当天增量链路")
    parser.add_argument("--drain", action="store_true", help="在本进程内把队列跑完（Airflow pod 用）")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if not args.now and not args.intraday:
        parser.print_help()
        return 0
    engine = make_engine()
    report = run_intraday(engine) if args.intraday else run_daily(engine)
    if args.drain:
        report["results"] = [
            {"id": row["id"], "range": row["range"], "status": row["status"]}
            for row in drain(engine)
        ]
    print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    failed = [row for row in report.get("results", ()) if row["status"] == "failed"]
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
