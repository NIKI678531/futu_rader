"""Alembic 环境 —— 连接串与 `MetaData` 都从 `radar_db` 取，不在这里重定义一份。

## 为什么不在 alembic.ini 里写 URL

`db_url()` 是 backend 与 worker 运行时真正用的那个函数（读 `RADAR_DB_URL`，
留空则落到 `%LOCALAPPDATA%\\futu-radar\\radar.db`）。迁移必须跑在**同一个库**上。
在 ini 里写死一份的后果是：本机迁移改的是仓库里的某个 SQLite，而服务读的是
LOCALAPPDATA 下那个 5.27 GB 的库 —— 迁移「成功」了，但服务看到的仍是旧表。

## batch 模式

SQLite 不支持大多数 `ALTER TABLE`（加约束、改类型、删列都不行）。`render_as_batch`
让 Alembic 在 SQLite 下自动走「建新表 → 拷数据 → 换名」的路径。MySQL 下这个开关
不产生额外动作，所以两个方言可以共用同一份迁移脚本（ADR-0016）。
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from radar_db import db_url  # noqa: E402
from radar_db.schema import metadata  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# autogenerate 的对照基准。注意它只是**辅助**：生成的脚本必须人工读一遍再提交，
# 尤其是 `AUTO_PK` 这类带 `with_variant` 的类型，autogenerate 认不出方言差异。
target_metadata = metadata

URL = db_url()


def run_migrations_offline():
    context.configure(
        url=URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = URL
    connectable = engine_from_config(section, prefix="sqlalchemy.",
                                     poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
