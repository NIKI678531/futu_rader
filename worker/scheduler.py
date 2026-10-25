"""Worker entry point: heartbeat, the 08:00 daily refresh and the AI request queue.

On start the worker upgrades an Alembic-versioned database to the current
schema, so the backend and worker never run against an older queue table.
"""

import logging
import os
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from radar_db import make_engine
from radar_db.analysis_requests import record_worker_heartbeat

_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_ENV):
    load_dotenv(_ENV, override=False)

# Windows 上 stdout 默认走控制台代码页（本机 cp936），中文日志会变成乱码；
# 容器里重定向到管道时同理。钉死 UTF-8，日志才可读、可 grep。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=os.getenv("RADAR_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
# Every 2-second queue poll would otherwise print two INFO lines.
logging.getLogger("apscheduler").setLevel(logging.WARNING)
log = logging.getLogger("worker")


def runtime_readiness():
    from jobs.admin_analysis import runtime_readiness as inspect_runtime

    return inspect_runtime()


def heartbeat(*, engine=None, now=None):
    health_file = os.getenv("WORKER_HEALTH_FILE")
    if health_file:
        Path(health_file).touch()
    engine = engine or make_engine()
    readiness = runtime_readiness()
    record_worker_heartbeat(engine, now=now, readiness=readiness)
    if readiness["ready"]:
        log.info("heartbeat —— AI worker 已就绪，等待数据库请求")
    else:
        log.warning("heartbeat —— AI worker 未就绪：%s", readiness["error"])


def process_analysis_request(*, engine=None):
    from jobs.admin_analysis import process_next

    result = process_next(engine=engine)
    if result:
        log.info(
            "admin AI request %s (%s): %s",
            result["id"], result["range"], result["status"],
        )


def daily_refresh(*, engine=None):
    from jobs.daily import run_daily

    run_daily(engine=engine)


def intraday_refresh(*, engine=None):
    from jobs.daily import run_intraday_analysis

    run_intraday_analysis(engine=engine)


def intraday_sync(*, engine=None):
    from jobs.daily import run_intraday_sync

    run_intraday_sync(engine=engine)


def recover_analysis_requests(*, engine=None):
    """Catch up page analysis missed before bootstrap produced a usable anchor."""

    from jobs.daily import recover_missing_analysis

    engine = engine or make_engine()
    result = recover_missing_analysis(engine)
    queued = result.get("queued", ())
    if queued:
        log.info(
            "startup recovery —— 已补交 %s",
            ",".join(item["range"] for item in queued),
        )
    else:
        log.info("startup recovery —— %s", result.get("status"))
    return result


def recover_prices(*, engine=None):
    """Backfill missed FMP sync receipts without blocking the analysis queue."""

    from jobs.daily import recover_missing_prices

    result = recover_missing_prices(engine or make_engine())
    log.info(
        "startup price recovery —— %s（缺 %s 只）",
        result.get("status"),
        len(result.get("missing", ())),
    )
    return result


def upgrade_schema(engine):
    """Apply pending Alembic migrations; databases without a version table are left alone."""
    from sqlalchemy import inspect
    from radar_db.source_facts import fact_source

    if fact_source() == "market_insight":
        log.info("Direct source state uses explicit preparation; automatic schema migration is disabled")
        return False

    if "alembic_version" not in inspect(engine).get_table_names():
        log.warning("数据库没有 alembic_version，跳过自动迁移：先按 radar_db/migrations/README.md 执行 stamp")
        return False
    from alembic import command
    from alembic.config import Config

    # No ini file: env.py would otherwise reconfigure logging and silence the worker.
    config = Config()
    config.set_main_option("script_location", str(ROOT / "radar_db" / "migrations"))
    config.set_main_option("prepend_sys_path", str(ROOT))
    command.upgrade(config, "head")
    return True


def build_scheduler(
    interval_seconds=None,
    poll_seconds=None,
    *,
    engine=None,
    cron=None,
    intraday_expression=None,
    intraday_sync_expression=None,
):
    """Build the persistent worker scheduler without starting it."""
    engine = engine or make_engine()
    interval = interval_seconds or int(os.getenv("WORKER_HEARTBEAT_INTERVAL_SECONDS", "60"))
    poll = poll_seconds or int(os.getenv("AI_REQUEST_POLL_SECONDS", "2"))
    timezone = os.getenv("TZ", "Asia/Hong_Kong")
    scheduler = BlockingScheduler(timezone=timezone)
    scheduler.add_job(
        heartbeat,
        "interval",
        seconds=interval,
        id="heartbeat",
        kwargs={"engine": engine},
    )
    scheduler.add_job(
        process_analysis_request,
        "interval",
        seconds=poll,
        id="admin-ai-requests",
        max_instances=1,
        coalesce=True,
        kwargs={"engine": engine},
    )
    scheduler.add_job(
        recover_prices,
        "date",
        id="startup-price-recovery",
        max_instances=1,
        misfire_grace_time=300,
        kwargs={"engine": engine},
    )
    from jobs.daily import daily_cron, intraday_cron, intraday_sync_cron

    expression = daily_cron(cron)
    if expression:
        scheduler.add_job(
            daily_refresh,
            CronTrigger.from_crontab(expression, timezone=timezone),
            id="daily-refresh",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=6 * 3600,
            kwargs={"engine": engine},
        )
    sync_expression = intraday_sync_cron(intraday_sync_expression)
    if sync_expression:
        scheduler.add_job(
            intraday_sync,
            CronTrigger.from_crontab(sync_expression, timezone=timezone),
            id="intraday-sync",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=30 * 60,
            kwargs={"engine": engine},
        )
    intraday = intraday_cron(intraday_expression)
    if intraday:
        scheduler.add_job(
            intraday_refresh,
            CronTrigger.from_crontab(intraday, timezone=timezone),
            id="intraday-refresh",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=2 * 3600,
            kwargs={"engine": engine},
        )
    return scheduler


def main():
    engine = make_engine()
    upgrade_schema(engine)
    scheduler = build_scheduler(engine=engine)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: sys.exit(0))
    log.info(
        "Worker 存活进程启动：%s", [(j.id, str(j.trigger)) for j in scheduler.get_jobs()]
    )
    heartbeat(engine=engine)  # 先跑一次，免得启动后要等满一个间隔才看得到反馈
    try:
        recover_analysis_requests(engine=engine)
    except Exception:  # noqa: BLE001 - recovery must not stop the queue consumer
        log.exception("startup recovery 失败；worker 继续运行，等待定时或手动补齐")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("调度器停止")


if __name__ == "__main__":
    main()
