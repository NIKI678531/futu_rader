"""采集调度入口。

当前只注册一个 heartbeat 任务：证明调度器起得来、间隔取自环境变量、Ctrl-C 能干净退出。
真正的采集任务见 jobs/collect.py（占位）。

worker 的职责边界（plan.md Q2，CLAUDE.md 铁律 1）：**只落原始数据**。
热度、去重、基准区间、环比这些口径一律归 backend/core/，这里不算、也不缓存算好的值。
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
    log.info("heartbeat —— 采集任务尚未实现，见 jobs/collect.py")


def build_scheduler(interval_seconds=None):
    """装配调度器但不启动，好让测试能在不跑真任务的前提下断言注册结果。"""
    interval = interval_seconds or int(os.getenv("COLLECT_INTERVAL_SECONDS", "60"))
    scheduler = BlockingScheduler(timezone=os.getenv("TZ", "Asia/Hong_Kong"))
    scheduler.add_job(heartbeat, "interval", seconds=interval, id="heartbeat")
    return scheduler


def main():
    scheduler = build_scheduler()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: sys.exit(0))
    log.info(
        "调度器启动：%s", [(j.id, str(j.trigger)) for j in scheduler.get_jobs()]
    )
    heartbeat()  # 先跑一次，免得启动后要等满一个间隔才看得到反馈
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("调度器停止")


if __name__ == "__main__":
    main()
