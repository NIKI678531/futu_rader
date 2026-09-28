"""`jobs/extract.py` —— 按 ETF × 时间段抽取、预过滤、排队，不调模型。"""

import json
import os
import sys
from datetime import datetime

import pytest
from sqlalchemy import insert, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from ai import config  # noqa: E402
from jobs import extract  # noqa: E402
from radar_db import create_all, make_engine  # noqa: E402
from radar_db.schema import (  # noqa: E402
    analysis_scopes, annotation_jobs, annotation_runs, annotations, comments, feeds, meta_kv,
)

OWN, PEER = "3033", "3032"
OWNERSHIP = {OWN: "own", PEER: "peer", "7226": "own"}


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "t.db").as_posix())
    create_all(eng)
    with eng.begin() as conn:
        conn.execute(insert(meta_kv).values(k="anchor", v="2026-08-25"))
        conn.execute(insert(feeds), [
            # 当前期（8-19 ～ 8-25）
            {"feed_id": 1, "code": OWN, "posted_at": datetime(2026, 8, 20, 9), "feed_type": 1,
             "title": "恒科", "content": "正文", "author_name": "路人",
             "like_count": 0, "comment_count": 0, "image_count": 0, "raw_json_broken": False},
            {"feed_id": 2, "code": PEER, "posted_at": datetime(2026, 8, 21, 9), "feed_type": 1,
             "title": "同业", "content": "正文", "author_name": "孫子的末代傳人",
             "like_count": 0, "comment_count": 0, "image_count": 0, "raw_json_broken": False},
            # 基准期（8-12 ～ 8-18）
            {"feed_id": 3, "code": OWN, "posted_at": datetime(2026, 8, 15, 9), "feed_type": 1,
             "title": "旧帖", "content": "正文", "author_name": "恒生投資管理有限公司",
             "like_count": 0, "comment_count": 0, "image_count": 0, "raw_json_broken": False},
            # 范围外
            {"feed_id": 4, "code": OWN, "posted_at": datetime(2026, 6, 1, 9), "feed_type": 1,
             "title": "更旧", "content": "正文", "author_name": "路人",
             "like_count": 0, "comment_count": 0, "image_count": 0, "raw_json_broken": False},
        ])
        conn.execute(insert(comments), [
            {"comment_id": 11, "feed_id": 1, "content": "點差太大", "author_uid": "a"},
            {"comment_id": 12, "feed_id": 1, "content": "騰訊業績好", "author_uid": "b"},   # 仅个股 ⇒ 剔
            {"comment_id": 13, "feed_id": 1, "content": "[捂脸]", "author_uid": "c"},       # 纯表情 ⇒ 剔
            {"comment_id": 14, "feed_id": 2, "content": "呢隻費率貴", "author_uid": "d"},
            {"comment_id": 15, "feed_id": 3, "content": "舊帖評論", "author_uid": "e"},
            {"comment_id": 16, "feed_id": 4, "content": "範圍外", "author_uid": "f"},
        ])
    return eng


@pytest.fixture()
def cfg():
    return config.load(model="m", prompt_version="comment-product-v2", schema_version="v2",
                       taxonomy_version="v2")


def rows(engine, table, *where):
    with engine.connect() as conn:
        return conn.execute(select(table).where(*where)).mappings().all()


def test_dry_run_writes_nothing_but_counts_everything(engine, cfg, tmp_path):
    stats = extract.run(engine, cfg, codes=[OWN, PEER], date_from=datetime(2026, 8, 19),
                        date_to=datetime(2026, 8, 25), dry_run=True, ownership=OWNERSHIP,
                        report_dir=tmp_path / "rep")
    c = stats["comments"]
    assert c["candidates"] == 4                      # 11 12 13 14
    assert c["dropped"]["offpool_stock_only"] == 1
    assert c["dropped"]["sticker_only"] == 1
    assert c["kept"] == 2
    assert rows(engine, annotation_jobs) == []
    assert rows(engine, analysis_scopes) == []
    assert rows(engine, annotations) == []
    assert (tmp_path / "rep" / f"{stats['scope_id']}.md").exists()


def test_run_creates_scope_jobs_and_rule_rows(engine, cfg, tmp_path):
    stats = extract.run(engine, cfg, codes=[OWN, PEER], date_from=datetime(2026, 8, 19),
                        date_to=datetime(2026, 8, 25), ownership=OWNERSHIP, report_dir=tmp_path)
    sid = stats["scope_id"]
    scope = rows(engine, analysis_scopes)[0]
    assert scope["scope_id"] == sid and json.loads(scope["codes_json"]) == [OWN, PEER]
    assert json.loads(scope["stats_json"])["comments"]["kept"] == 2

    jobs = rows(engine, annotation_jobs)
    assert {j["target_id"] for j in jobs} == {11, 14}
    assert all(j["scope_id"] == sid for j in jobs)
    # 自家优先：3033 的评论 priority 高于 3032 的。
    prio = {j["target_id"]: j["priority"] for j in jobs}
    assert prio[11] > prio[14]

    # 剔掉的两条以 provider=rule 落库，各两行（relevance + text_quality）。
    anns = rows(engine, annotations)
    assert {a["target_id"] for a in anns} == {12, 13}
    assert len(anns) == 4
    run = rows(engine, annotation_runs)[0]
    assert run["provider"] == "rule" and run["status"] == "done"
    assert stats["estimate"]["comment_product"]["pending_items"] == 2


def test_rerun_is_idempotent(engine, cfg, tmp_path):
    kw = dict(codes=[OWN], date_from=datetime(2026, 8, 19), date_to=datetime(2026, 8, 25),
              ownership=OWNERSHIP, report_dir=tmp_path)
    a = extract.run(engine, cfg, **kw)
    b = extract.run(engine, cfg, **kw)
    assert a["comments"]["queued_new"] == 1
    assert b["comments"]["queued_new"] == 0 and b["comments"]["already_queued_or_done"] == 1
    assert b["comments"]["rule_rows_written"] == 0
    assert len(rows(engine, annotation_jobs)) == 1
    assert len(rows(engine, analysis_scopes)) == 2  # 两个 scope 头，同一批任务


def test_baseline_window_is_included_with_lower_priority(engine, cfg, tmp_path):
    stats = extract.run(engine, cfg, codes=[OWN], date_from=datetime(2026, 8, 19),
                        date_to=datetime(2026, 8, 25), with_baseline=True,
                        ownership=OWNERSHIP, report_dir=tmp_path)
    w = stats["comments"]["by_window"]
    assert w["baseline"]["from"] == "2026-08-12" and w["baseline"]["to"] == "2026-08-18"
    jobs = {j["target_id"]: j for j in rows(engine, annotation_jobs)}
    assert set(jobs) == {11, 15}          # 16 在范围外
    assert jobs[11]["priority"] > jobs[15]["priority"]


def test_offpool_switch(engine, cfg, tmp_path):
    stats = extract.run(engine, cfg, codes=[OWN], date_from=datetime(2026, 8, 19),
                        date_to=datetime(2026, 8, 25), drop_offpool=False,
                        ownership=OWNERSHIP, report_dir=tmp_path)
    assert stats["comments"]["dropped"]["offpool_stock_only"] == 0
    assert {j["target_id"] for j in rows(engine, annotation_jobs)} == {11, 12}


def test_posts_only_for_kol_and_official_authors(engine, cfg, tmp_path):
    stats = extract.run(engine, cfg, codes=[OWN, PEER], date_from=datetime(2026, 8, 12),
                        date_to=datetime(2026, 8, 25), task="post_annotation",
                        ownership=OWNERSHIP, report_dir=tmp_path)
    jobs = rows(engine, annotation_jobs, annotation_jobs.c.task == "post_annotation")
    # feed 2（KOL）与 feed 3（官号全称）排进来；feed 1 的「路人」不排。
    assert {j["target_id"] for j in jobs} == {2, 3}
    assert stats["posts"]["queued_new"] == 2


def test_range_resolves_against_anchor(engine):
    class A:
        range = "d7"
        from_ = None
        to = None
    frm, to = extract.resolve_window(A(), engine)
    assert (frm, to) == (datetime(2026, 8, 19), datetime(2026, 8, 25))


def test_hkt_day_extracts_utc_naive_boundary_rows(engine, cfg, tmp_path):
    timestamps = (
        datetime(2026, 8, 24, 15, 59, 59),
        datetime(2026, 8, 24, 16, 0, 0),
        datetime(2026, 8, 25, 15, 59, 59),
        datetime(2026, 8, 25, 16, 0, 0),
    )
    with engine.begin() as conn:
        conn.execute(insert(feeds), [
            {
                "feed_id": 100 + i,
                "code": OWN,
                "posted_at": posted_at,
                "feed_type": 1,
                "title": "邊界帖",
                "content": "正文",
                "author_name": "路人",
                "like_count": 0,
                "comment_count": 1,
                "image_count": 0,
                "raw_json_broken": False,
            }
            for i, posted_at in enumerate(timestamps, 1)
        ])
        conn.execute(insert(comments), [
            {
                "comment_id": 100 + i,
                "feed_id": 100 + i,
                "content": f"ETF 費率邊界評論 {i}",
                "author_uid": f"boundary-{i}",
            }
            for i in range(1, 5)
        ])

    stats = extract.run(
        engine,
        cfg,
        codes=[OWN],
        date_from=datetime(2026, 8, 25),
        date_to=datetime(2026, 8, 25),
        dry_run=True,
        drop_offpool=False,
        ownership=OWNERSHIP,
        report_dir=tmp_path,
    )

    assert stats["comments"]["candidates"] == 2


def test_resolve_codes_rejects_unknown():
    class A:
        codes = "3033,9999"
        all = False
    with pytest.raises(SystemExit):
        extract.resolve_codes(A(), OWNERSHIP)
