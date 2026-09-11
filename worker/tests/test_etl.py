"""`jobs/etl.py` 的 `stamp()` —— 事实表的「代」。

ETL 只重建 feeds/comments/mentions，不碰 `meta_kv` 的其余键。而后端把全池扫描的结果
按区间缓存在进程里，只在 `meta_kv` 变了的时候丢。没有这个标记，重跑一次 ETL 之后
还在跑的后端会拿旧数据一路服务到有人重启它 —— 界面上看不出任何异样。

（ETL 主体要 `src_*` 镜像与真实 dump 才跑得动，不在这里测；见 `test_dumpio.py`。）
"""

import os
import sys

import pytest
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from jobs import etl  # noqa: E402
from radar_db import create_all, make_engine  # noqa: E402
from radar_db.schema import meta_kv  # noqa: E402

STATS = {"feeds": 504400, "broken": 456, "comments": 1200000, "mentions": 600000}


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "t.db").as_posix())
    create_all(eng)
    return eng


def _meta(engine):
    with engine.connect() as conn:
        return {k: v for k, v in conn.execute(select(meta_kv.c.k, meta_kv.c.v))}


def test_stamp_records_what_the_fact_tables_contain(engine):
    etl.stamp(engine, STATS)
    v = _meta(engine)["etl_generation"]
    for part in ("feeds=504400", "comments=1200000", "mentions=600000", "broken=456"):
        assert part in v


def test_rerunning_on_the_same_source_does_not_change_the_marker(engine):
    """值取计数而不是时间戳，是有意的。

    ETL 是可重跑的：同样的 `src_*` 重跑出来就是同一批行，数据没变，后端的缓存本来
    就该留着。写时间戳会把「又跑了一遍」误报成「数据变了」，每次重跑白白让后端
    重算一轮全池扫描。
    """
    etl.stamp(engine, STATS)
    first = _meta(engine)["etl_generation"]
    etl.stamp(engine, STATS)
    assert _meta(engine)["etl_generation"] == first


def test_a_different_row_count_changes_the_marker(engine):
    etl.stamp(engine, STATS)
    first = _meta(engine)["etl_generation"]
    etl.stamp(engine, {**STATS, "comments": 1200001})
    assert _meta(engine)["etl_generation"] != first


def test_stamp_leaves_the_import_keys_alone(engine):
    """锚点是 `import_dump` 写的。ETL 顺手清掉它会让后端的区间整个变成未知。"""
    from sqlalchemy import insert

    with engine.begin() as conn:
        conn.execute(insert(meta_kv), [
            {"k": "anchor", "v": "2026-08-25"},
            {"k": "anchor_ts", "v": "2026-08-25 23:59:59"},
        ])
    etl.stamp(engine, STATS)
    etl.stamp(engine, {**STATS, "feeds": 1})  # 改写自己那一行，不牵连别人
    m = _meta(engine)
    assert m["anchor"] == "2026-08-25"
    assert m["anchor_ts"] == "2026-08-25 23:59:59"
    assert "etl_generation" in m
