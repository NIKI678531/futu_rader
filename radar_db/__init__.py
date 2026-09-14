"""瘦库的连接与建表 —— `backend/` 与 `worker/` 共用的唯一一份 schema。

## 为什么它在仓库根，而不是 backend/ 或 worker/ 里

写这些表的是 worker 的 ETL（[ADR-0009](../docs/adr/0009-worker-scope.md)），读它们的是
`backend/core/` 的口径 SQL。schema 抄两份的第一天两边是一样的，第三天就不是了——而
「列名对不上」这类错误在只读工作台里表现为**某个字段静默变成缺失**，正是铁律 2 最怕的形态。
两个 Dockerfile 的构建上下文本来就是仓库根，把它 COPY 进去只是一行。

## 本地 SQLite / 生产 MySQL

`RADAR_DB_URL` 决定方言，两边跑同一份 `MetaData`：

    sqlite:///P:/NIKI/futu-radar/backend/data/radar.db      # 本地默认
    mysql+pymysql://user:pw@host:3307/futu_radar?charset=utf8mb4   # 生产

这不违反 [ADR-0014](../docs/adr/0014-data-access-layer.md)（SQLAlchemy Core，不用 ORM）：
这里只有 `Table` 定义和 `create_all`，没有映射类、没有 session、没有身份映射。口径 SQL 仍然
是手写的、看得见的。

两个方言能共用的前提是 **`backend/core/` 只查扁平事实表，不查 `raw_json`** ——
JSON 函数在 MySQL 8 和 SQLite 之间不通用，而拆 JSON 是 worker 的活，不是口径的活。

## 默认路径为什么在仓库外

两个理由，各自都足够：

1. **仓库在 P: 盘（SMB 共享）**。实测顺序写 9.4 MB/s，C: 盘 3.1 GB/s —— 差 330 倍。
   SQLite 的写入是大量小事务，在网络盘上还有锁语义不可靠的问题，`src_feeds` 又有 GB 级的
   `raw_json`。放 P: 上导入要跑几小时且随时可能锁死。
2. **瘦库含真实用户昵称、IP 归属地、个人简介**（ADR-0008）。放在仓库树外，就不用靠
   `.gitignore` 拦住它——少一层「只要有人 `git add -f` 就漏」的可能。

默认落在用户数据目录（Windows 是 `%LOCALAPPDATA%\\futu-radar\\`）。要换位置就设
`RADAR_DB_URL`。
"""

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, event

from .schema import metadata  # noqa: F401  （re-export，调用方 `from radar_db import metadata`）

REPO_ROOT = Path(__file__).resolve().parents[1]


def default_data_dir():
    if sys.platform == "win32":
        base = os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    else:
        base = os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "futu-radar"


def db_url():
    """当前进程该连哪个库。默认本机 SQLite（见模块 docstring 为什么不在仓库里）。"""
    url = os.getenv("RADAR_DB_URL", "").strip()
    if url:
        return url
    path = default_data_dir() / "radar.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return "sqlite:///" + path.as_posix()


def make_engine(url=None, bulk=False):
    """建引擎。

    ``bulk=True`` 是**一次性导入专用**：SQLite 下关掉 fsync、开 WAL、加大 page cache。
    崩溃时可能丢最后一批写入——对一个可以重跑的一次性导入这是划算的，对线上服务不是，
    所以它是个显式开关而不是默认值。
    """
    url = url or db_url()
    is_sqlite = url.startswith("sqlite")
    engine = create_engine(url, future=True, echo=False)

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _rec):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            # 多线程标注（AI_CONCURRENCY）下写事务会互相等锁。不设这个，第二个线程
            # 立刻拿到 "database is locked" 而不是等几毫秒。
            cur.execute("PRAGMA busy_timeout=5000")
            if bulk:
                cur.execute("PRAGMA synchronous=OFF")
                cur.execute("PRAGMA cache_size=-262144")  # 256 MiB
                cur.execute("PRAGMA temp_store=MEMORY")
            cur.close()

    return engine


def create_all(engine):
    metadata.create_all(engine)
