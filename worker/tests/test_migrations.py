"""`radar_db/migrations/` —— 迁移脚本与 `schema.py` 不许漂移。

迁移和 `MetaData` 是同一个 schema 的两份表述。它们分叉的那一刻，表现是：新环境
（跑迁移建库）和老环境（`create_all` 建库）**结构不同**，而两边的代码完全一样。
查起来极难 —— 症状会是「某个环境上这个查询报 no such column」，而本机永远复现不了。

所以这里逐表、逐列比对两条路径建出来的库。另外验一遍 §10.3 那个 SQLite 主键问题：
它是**静默**失效的，只有真的插两行才看得出来。
"""

import os
import sys

import pytest
from sqlalchemy import inspect, insert, select, text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from radar_db import create_all, make_engine  # noqa: E402
from radar_db.schema import annotation_jobs, annotations, metadata  # noqa: E402

alembic = pytest.importorskip("alembic", reason="迁移测试需要 alembic")
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

INI = os.path.join(REPO, "radar_db", "alembic.ini")


def alembic_cfg(url):
    cfg = Config(INI)
    # env.py 调 radar_db.db_url()，它读 RADAR_DB_URL。测试靠这个变量把库指到临时目录。
    os.environ["RADAR_DB_URL"] = url
    return cfg


@pytest.fixture()
def migrated(tmp_path, monkeypatch):
    url = "sqlite:///" + (tmp_path / "migrated.db").as_posix()
    monkeypatch.setenv("RADAR_DB_URL", url)
    command.upgrade(alembic_cfg(url), "head")
    return make_engine(url)


@pytest.fixture()
def created(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "created.db").as_posix())
    create_all(eng)
    return eng


# ── 两条建库路径必须产出同一个库 ───────────────────────────────────────


def test_migrations_produce_the_same_tables_as_create_all(migrated, created):
    a = set(inspect(migrated).get_table_names()) - {"alembic_version"}
    assert a == set(inspect(created).get_table_names())


def test_every_column_matches(migrated, created):
    mi, ci = inspect(migrated), inspect(created)
    for table in sorted(set(ci.get_table_names())):
        got = {c["name"]: (str(c["type"]), c["nullable"])
               for c in mi.get_columns(table)}
        want = {c["name"]: (str(c["type"]), c["nullable"])
                for c in ci.get_columns(table)}
        assert got == want, f"{table} 的列在两条路径下不一致"


def test_indexes_and_constraints_match(migrated, created):
    mi, ci = inspect(migrated), inspect(created)
    for table in sorted(set(ci.get_table_names())):
        assert ({i["name"] for i in mi.get_indexes(table)}
                == {i["name"] for i in ci.get_indexes(table)}), f"{table} 索引不一致"
        assert ({u["name"] for u in mi.get_unique_constraints(table)}
                == {u["name"] for u in ci.get_unique_constraints(table)}), (
            f"{table} 唯一约束不一致 —— 幂等依赖它们")
        assert ({c["name"] for c in mi.get_check_constraints(table)}
                == {c["name"] for c in ci.get_check_constraints(table)}), (
            f"{table} CHECK 约束不一致 —— 状态与预算上限依赖它们")


def test_old_single_table_annotations_is_gone(migrated):
    """ADR-0010 的单表形态撑不住生产标注（判定单元、版本、证据全缺）。"""
    cols = {c["name"] for c in inspect(migrated).get_columns("annotations")}
    assert "annotation_id" in cols and "subject_code" in cols and "run_id" in cols
    assert "confidence" not in cols, "旧的自报 confidence 列不该留下"


def test_downgrade_then_upgrade_is_clean(tmp_path, monkeypatch):
    """迁移要能倒回去 —— 否则一次改错就只能重建 5.27 GB 的库。"""
    url = "sqlite:///" + (tmp_path / "roundtrip.db").as_posix()
    monkeypatch.setenv("RADAR_DB_URL", url)
    cfg = alembic_cfg(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0001")
    eng = make_engine(url)
    assert "annotation_runs" not in inspect(eng).get_table_names()
    # 回到 0001 后旧的单表形态重新出现（结构回滚，数据不回来 —— 见 0002 的 docstring）。
    assert "confidence" in {c["name"] for c in inspect(eng).get_columns("annotations")}
    command.upgrade(cfg, "head")
    assert "annotation_runs" in inspect(eng).get_table_names()


# ── runbook §10.3：SQLite 的自动行号 ───────────────────────────────────


@pytest.mark.parametrize("table,pk", [
    (annotations, "annotation_id"),
    (annotation_jobs, "job_id"),
])
def test_autoincrement_actually_works_on_sqlite(migrated, table, pk):
    """`BigInteger` 在 SQLite 下渲染成 `BIGINT`，那不是 rowid 别名 —— autoincrement
    **静默**失效，两行都拿到 NULL 主键，插第二行才炸。只有真插两行才测得出来。"""
    from datetime import datetime

    now = datetime(2026, 8, 1, 10, 0)
    base = (
        {"target_type": "comment", "target_id": 1, "subject_code": "3033",
         "kind": "attitude", "value_json": '"positive"', "run_id": "r1",
         "input_hash": "h", "review_state": "pending", "created_at": now}
        if pk == "annotation_id" else
        {"target_type": "comment", "target_id": 1, "subject_code": "3033",
         "task": "comment_product", "input_hash": "h", "status": "pending",
         "priority": 0, "attempts": 0, "created_at": now, "updated_at": now}
    )
    with migrated.begin() as conn:
        for i in (1, 2):
            row = dict(base)
            # 唯一约束不许两行完全相同，所以换一处非主键字段。
            row["input_hash"] = f"h{i}"
            conn.execute(insert(table).values(**row))
        ids = [r[0] for r in conn.execute(select(table.c[pk]))]
    assert len(set(ids)) == 2 and all(isinstance(i, int) for i in ids)


def test_sqlite_renders_the_pk_as_plain_integer():
    """直接看 DDL：SQLite 要的是精确的 `INTEGER`，MySQL 要的是 `BIGINT AUTO_INCREMENT`。"""
    from sqlalchemy.schema import CreateTable

    sqlite_ddl = str(CreateTable(annotations).compile(
        dialect=make_engine("sqlite://").dialect))
    assert "annotation_id INTEGER NOT NULL" in sqlite_ddl
    assert "annotation_id BIGINT" not in sqlite_ddl


def test_alembic_ini_is_ascii_only():
    """configparser 用**平台默认编码**读 ini —— 在 zh-CN Windows 上是 gbk。
    往 ini 里写一个中文注释，`alembic` 命令本身就会 UnicodeDecodeError（实测）。
    说明文字放 migrations/README.md，那个文件按 UTF-8 读。"""
    with open(INI, "rb") as fh:
        raw = fh.read()
    bad = [(i, b) for i, b in enumerate(raw) if b > 0x7F]
    assert not bad, f"alembic.ini 出现非 ASCII 字节，偏移 {bad[0][0]}"


def test_metadata_is_the_only_schema_definition():
    """`radar_db.metadata` 是 backend 与 worker 共用的那一份（ADR-0009）。
    这条断言在有人另起一个 MetaData 时会响。"""
    assert {t for t in metadata.tables} >= {
        "feeds", "comments", "mentions", "users", "meta_kv",
        "annotation_runs", "annotation_jobs", "annotations",
        "annotation_evidence", "review_decisions",
        "ingestion_runs", "collector_checkpoints", "ai_daily_budget",
        "feed_counter_observations",
    }


def test_collection_migration_preserves_feeds_and_backfills_unknown(tmp_path, monkeypatch):
    """历史 dump 没有覆盖语义；迁移不得把它猜成 complete 或丢掉大表中的行。"""
    url = "sqlite:///" + (tmp_path / "collection-upgrade.db").as_posix()
    monkeypatch.setenv("RADAR_DB_URL", url)
    cfg = alembic_cfg(url)
    command.upgrade(cfg, "0008")
    eng = make_engine(url)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO feeds "
            "(feed_id, code, posted_at, feed_type, like_count, comment_count, "
            "image_count, raw_json_broken) "
            "VALUES (1, '3033', '2026-08-25 10:00:00', 1, 2, 3, 0, 0)"
        ))

    command.upgrade(cfg, "head")
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT feed_id, source_observed_at, comment_coverage_status "
            "FROM feeds WHERE feed_id = 1"
        )).one()
    assert tuple(row) == (1, None, "unknown")

    command.downgrade(cfg, "0008")
    assert {column["name"] for column in inspect(eng).get_columns("feeds")} == {
        "feed_id", "code", "posted_at", "feed_type", "author_uid", "author_name",
        "title", "content", "like_count", "comment_count", "image_count",
        "share_count", "browse_count", "comments_parsed", "comments_truncated",
        "original_lang", "raw_json_broken",
    }
    with eng.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM feeds")).scalar_one() == 1
