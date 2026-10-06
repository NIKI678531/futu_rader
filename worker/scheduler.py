"""Worker entry point for heartbeat and database-backed admin AI requests."""

import logging
import os
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apscheduler.schedulers.blocking import BlockingScheduler
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
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("worker")


def runtime_readiness():
    from jobs.admin_analysis import runtime_readiness as inspect_runtime

    return inspect_runtime()


def heartbeat(*, engine=None, now=None):
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


def build_scheduler(interval_seconds=None, poll_seconds=None, *, engine=None):
    """Build the persistent worker scheduler without starting it."""
    engine = engine or make_engine()
    interval = interval_seconds or int(os.getenv("WORKER_HEARTBEAT_INTERVAL_SECONDS", "60"))
    poll = poll_seconds or int(os.getenv("AI_REQUEST_POLL_SECONDS", "2"))
    scheduler = BlockingScheduler(timezone=os.getenv("TZ", "Asia/Hong_Kong"))
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
    return scheduler


def main():
    engine = make_engine()
    scheduler = build_scheduler(engine=engine)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: sys.exit(0))
    log.info(
        "Worker 存活进程启动：%s", [(j.id, str(j.trigger)) for j in scheduler.get_jobs()]
    )
    heartbeat(engine=engine)  # 先跑一次，免得启动后要等满一个间隔才看得到反馈
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("调度器停止")


if __name__ == "__main__":
    main()
