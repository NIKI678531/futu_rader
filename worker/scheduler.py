"""Worker 容器存活入口。

生产同步和分析均由 Airflow 显式调用 ``jobs.refresh``；本进程只发出 heartbeat，
不采集社区数据、不轮询 MarketInsight，也不自动启动 AI。
"""

import logging
import os
import signal
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

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


def heartbeat():
    log.info("heartbeat —— 等待 Airflow 显式触发数据库同步或分析")


def build_scheduler(interval_seconds=None):
    """装配存活心跳但不启动；它不承载采集或分析任务。"""
    interval = interval_seconds or int(os.getenv("WORKER_HEARTBEAT_INTERVAL_SECONDS", "60"))
    scheduler = BlockingScheduler(timezone=os.getenv("TZ", "Asia/Hong_Kong"))
    scheduler.add_job(heartbeat, "interval", seconds=interval, id="heartbeat")
    return scheduler


def main():
    scheduler = build_scheduler()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: sys.exit(0))
    log.info(
        "Worker 存活进程启动：%s", [(j.id, str(j.trigger)) for j in scheduler.get_jobs()]
    )
    heartbeat()  # 先跑一次，免得启动后要等满一个间隔才看得到反馈
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("调度器停止")


if __name__ == "__main__":
    main()
