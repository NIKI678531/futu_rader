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
from radar_db.annotations_read import current_annotations  # noqa: E402
from radar_db.comment_filter import filter_readiness_values, load_comment_filter_config  # noqa: E402
from radar_db.schema import (  # noqa: E402
    analysis_scopes, annotation_jobs, annotation_runs, annotations, comments, feed_mentions,
    feeds, mentions,
    meta_kv,
)

OWN, PEER = "3033", "3032"
OWNERSHIP = {OWN: "own", PEER: "peer", "3037": "own", "7226": "own"}


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "t.db").as_posix())
    create_all(eng)
    with eng.begin() as conn:
        conn.execute(insert(meta_kv).values(k="anchor", v="2026-08-25"))
        conn.execute(insert(meta_kv), [
            {"k": key, "v": value}
            for key, value in filter_readiness_values(load_comment_filter_config()).items()
        ])
        conn.execute(insert(feeds), [
            # 当前期（8-19 ～ 8-25）
            {"feed_id": 1, "code": OWN, "source_ticker": "03033.HK", "posted_at": datetime(2026, 8, 20, 9), "feed_type": 1,
             "title": "恒科", "content": "$03033.HK$ 正文", "author_name": "路人",
             "like_count": 0, "comment_count": 0, "image_count": 0, "raw_json_broken": False},
            {"feed_id": 2, "code": PEER, "source_ticker": "03032.HK", "posted_at": datetime(2026, 8, 21, 9), "feed_type": 1,
             "title": "同业", "content": "$03032.HK$ 正文", "author_name": "孫子的末代傳人",
             "like_count": 0, "comment_count": 0, "image_count": 0, "raw_json_broken": False},
            # 基准期（8-12 ～ 8-18）
            {"feed_id": 3, "code": OWN, "source_ticker": "03033.HK", "posted_at": datetime(2026, 8, 15, 9), "feed_type": 1,
             "title": "旧帖", "content": "$03033.HK$ 正文", "author_name": "恒生投資管理有限公司",
             "like_count": 0, "comment_count": 0, "image_count": 0, "raw_json_broken": False},
            # 范围外
            {"feed_id": 4, "code": OWN, "source_ticker": "03033.HK", "posted_at": datetime(2026, 6, 1, 9), "feed_type": 1,
             "title": "更旧", "content": "$03033.HK$ 正文", "author_name": "路人",
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
        conn.execute(insert(mentions), [
            {"feed_id": feed_id, "code": code, "source": source, "in_pool": True}
            for feed_id, code in ((1, OWN), (2, PEER), (3, OWN), (4, OWN))
            for source in ("anchor", "body")
        ])
        conn.execute(insert(feed_mentions), [
            {"feed_id": feed_id, "raw_ticker": ticker, "market": "HK", "occurrences": 1}
            for feed_id, ticker in ((1, "03033.HK"), (2, "03032.HK"),
                                    (3, "03033.HK"), (4, "03033.HK"))
        ])
    return eng


@pytest.fixture()
def cfg():
    return config.load(model="m", prompt_version="comment-product-v2", schema_version="v2",
                       taxonomy_version="v2")


def rows(engine, table, *where):
    with engine.connect() as conn:
        return conn.execute(select(table).where(*where)).mappings().all()


def test_mutating_extract_fails_closed_until_parent_filter_is_ready(tmp_path, cfg):
    unready = make_engine("sqlite:///" + (tmp_path / "unready.db").as_posix())
    create_all(unready)

    with pytest.raises(RuntimeError, match="parent-feed comment filter is not ready"):
        extract.run(
            unready,
            cfg,
            codes=[OWN],
            date_from=datetime(2026, 8, 25),
            date_to=datetime(2026, 8, 25),
            ownership=OWNERSHIP,
            report_dir=None,
        )

    preview = extract.run(
        unready,
        cfg,
        codes=[OWN],
        date_from=datetime(2026, 8, 25),
        date_to=datetime(2026, 8, 25),
        ownership=OWNERSHIP,
        report_dir=None,
        dry_run=True,
    )
    assert preview["comments"]["candidates"] == 0


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
    scope_stats = json.loads(scope["stats_json"])
    assert scope_stats["exactRuleVersion"] == "parent-feed-v1"
    assert scope_stats["comments"]["kept"] == 2

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
    audits = [json.loads(a["value_json"]) for a in anns if a["kind"] == "text_quality"]
    assert all(
        set(("rule", "reason", "matched_tickers", "rule_version", "decided_at")) <= set(audit)
        for audit in audits
    )
    assert all(datetime.fromisoformat(audit["decided_at"]) for audit in audits)
    run = rows(engine, annotation_runs)[0]
    assert run["provider"] == "rule" and run["status"] == "done"
    assert stats["estimate"]["comment_product"]["pending_items"] == 2


def test_empty_comment_is_audited_instead_of_silently_pending(engine, cfg, tmp_path):
    with engine.begin() as conn:
        conn.execute(insert(comments).values(
            comment_id=23,
            feed_id=1,
            content="",
            author_uid="empty-author",
        ))

    stats = extract.run(
        engine,
        cfg,
        codes=[OWN],
        date_from=datetime(2026, 8, 19),
        date_to=datetime(2026, 8, 25),
        ownership=OWNERSHIP,
        report_dir=tmp_path,
    )

    assert stats["comments"]["dropped"]["empty"] == 1
    empty_rows = rows(engine, annotations, annotations.c.target_id == 23)
    assert [(row["kind"], json.loads(row["value_json"])) for row in empty_rows[:1]] == [
        ("relevance", "irrelevant")
    ]
    audit = json.loads(empty_rows[1]["value_json"])
    assert {
        "rule": audit["rule"],
        "reason": audit["reason"],
        "matched_tickers": audit["matched_tickers"],
        "rule_version": audit["rule_version"],
    } == {
        "rule": "empty",
        "reason": "empty",
        "matched_tickers": [],
        "rule_version": "prefilter-v1",
    }
    assert datetime.fromisoformat(audit["decided_at"])
    assert rows(engine, annotation_jobs, annotation_jobs.c.target_id == 23) == []


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


def test_reply_ticker_does_not_override_qualified_parent_anchor(engine, cfg, tmp_path):
    with engine.begin() as conn:
        conn.execute(insert(comments).values(
            comment_id=17,
            feed_id=1,
            content="$03032.HK$ 費率更低",
            author_uid="explicit-peer",
        ))

    stats = extract.run(
        engine,
        cfg,
        codes=[OWN],
        date_from=datetime(2026, 8, 19),
        date_to=datetime(2026, 8, 25),
        ownership=OWNERSHIP,
        report_dir=tmp_path,
    )

    jobs = rows(engine, annotation_jobs, annotation_jobs.c.target_id == 17)
    assert {(row["target_id"], row["subject_code"]) for row in jobs} == {(17, OWN)}
    exact_rows = rows(engine, annotations, annotations.c.target_id == 17)
    assert exact_rows == []
    assert stats["comments"]["dropped"]["parent_feed_filter"] == 0


def test_unqualified_parent_drops_all_replies_and_supersedes_old_job(engine, cfg, tmp_path):
    old_job_created_at = datetime(2026, 8, 26, 0, 0)
    with engine.begin() as conn:
        conn.execute(insert(feeds).values(
            feed_id=5,
            code=OWN,
            source_ticker="03033.HK",
            posted_at=datetime(2026, 8, 22, 9),
            feed_type=1,
            title="只談同業",
            content="$03032.HK$",
            author_name="路人",
            like_count=0,
            comment_count=3,
            image_count=0,
            raw_json_broken=False,
        ))
        conn.execute(insert(mentions), [
            {"feed_id": 5, "code": OWN, "source": "anchor", "in_pool": True},
            {"feed_id": 5, "code": PEER, "source": "body", "in_pool": True},
        ])
        conn.execute(insert(feed_mentions).values(
            feed_id=5, raw_ticker="03032.HK", market="HK", occurrences=1
        ))
        conn.execute(insert(comments), [
            {"comment_id": 18, "feed_id": 5, "content": "手续费太高", "author_uid": "generic"},
            {"comment_id": 19, "feed_id": 5, "content": "$03033.HK$ 手续费太高", "author_uid": "self"},
            {"comment_id": 20, "feed_id": 5, "content": "恒生指數ETF 手续费太高", "author_uid": "named"},
        ])
        conn.execute(insert(annotation_jobs).values(
            target_type="comment",
            target_id=18,
            subject_code=OWN,
            task="comment_product",
            input_hash="legacy-v2-job",
            status="pending",
            priority=0,
            attempts=0,
            scope_id=None,
            stage="llm",
            created_at=old_job_created_at,
            updated_at=old_job_created_at,
        ))

    extract.run(
        engine,
        cfg,
        codes=[OWN],
        date_from=datetime(2026, 8, 19),
        date_to=datetime(2026, 8, 25),
        ownership=OWNERSHIP,
        report_dir=tmp_path,
    )

    jobs = rows(engine, annotation_jobs)
    routed = {
        (row["target_id"], row["subject_code"])
        for row in jobs
        if row["target_id"] >= 18 and row["status"] != "superseded"
    }
    assert routed == set()
    old_job = next(row for row in jobs if row["target_id"] == 18)
    assert old_job["status"] == "superseded"
    assert old_job["last_error"] == "Excluded by parent-feed-v1:parent_feed_filter"
    irrelevant = rows(
        engine,
        annotations,
        annotations.c.kind == "relevance",
        annotations.c.target_id.in_((18, 19, 20)),
    )
    assert {(row["target_id"], row["subject_code"], json.loads(row["value_json"])) for row in irrelevant} == {
        (18, OWN, "irrelevant"),
        (19, OWN, "irrelevant"),
        (20, OWN, "irrelevant"),
    }


def test_parent_edit_withdraws_stale_rule_while_v3_job_is_pending(engine, cfg, tmp_path):
    with engine.begin() as conn:
        conn.execute(insert(feeds).values(
            feed_id=7,
            code=OWN,
            source_ticker="03033.HK",
            posted_at=datetime(2026, 8, 22, 9),
            feed_type=1,
            title="尚未明确产品",
            content="恒指行情",
            author_name="路人",
            like_count=0,
            comment_count=1,
            image_count=0,
            raw_json_broken=False,
        ))
        conn.execute(insert(mentions).values(
            feed_id=7, code=OWN, source="anchor", in_pool=True
        ))
        conn.execute(insert(comments).values(
            comment_id=24,
            feed_id=7,
            content="手续费太高",
            author_uid="edited-parent",
        ))

    kwargs = dict(
        codes=[OWN],
        date_from=datetime(2026, 8, 19),
        date_to=datetime(2026, 8, 25),
        ownership=OWNERSHIP,
        report_dir=tmp_path,
    )
    first = extract.run(engine, cfg, **kwargs)
    assert first["comments"]["dropped"]["parent_feed_filter"] >= 1
    assert current_annotations(
        engine, "relevance", "comment", ids=[24], subject_code=OWN
    )[(24, OWN)]["value"] == "irrelevant"

    with engine.begin() as conn:
        conn.execute(insert(feed_mentions).values(
            feed_id=7, raw_ticker="03033.HK", market="HK", occurrences=1
        ))

    second = extract.run(engine, cfg, **kwargs)

    assert second["comments"]["rule_rows_withdrawn"] == 1
    assert current_annotations(
        engine, "relevance", "comment", ids=[24], subject_code=OWN
    ) == {}
    job = rows(engine, annotation_jobs, annotation_jobs.c.target_id == 24)[0]
    assert job["status"] == "pending"
    chain = rows(
        engine,
        annotations,
        annotations.c.target_id == 24,
        annotations.c.kind == "relevance",
    )
    assert len(chain) == 2
    assert chain[1]["review_state"] == "rejected"
    assert chain[1]["supersedes_id"] == chain[0]["annotation_id"]

    # A completed job must also become superseded if a later parent edit makes
    # it ineligible.  Restoring the same parent cashtag must then revive the
    # identical job instead of leaving no current relevance and no work queued.
    with engine.begin() as conn:
        conn.execute(
            annotation_jobs.update()
            .where(annotation_jobs.c.target_id == 24)
            .values(status="done")
        )
        conn.execute(
            feed_mentions.delete().where(
                feed_mentions.c.feed_id == 7,
                feed_mentions.c.raw_ticker == "03033.HK",
            )
        )

    extract.run(engine, cfg, **kwargs)
    job = rows(engine, annotation_jobs, annotation_jobs.c.target_id == 24)[0]
    assert job["status"] == "superseded"

    with engine.begin() as conn:
        conn.execute(insert(feed_mentions).values(
            feed_id=7, raw_ticker="03033.HK", market="HK", occurrences=1
        ))

    extract.run(engine, cfg, **kwargs)
    job = rows(engine, annotation_jobs, annotation_jobs.c.target_id == 24)[0]
    assert job["status"] == "pending"


def test_body_target_scope_does_not_inherit_parent_when_feed_is_anchored_elsewhere(
    engine, cfg, tmp_path
):
    """A parent B cashtag must not fan a generic A-forum reply out to B."""

    with engine.begin() as conn:
        conn.execute(insert(feeds).values(
            feed_id=6,
            code=OWN,
            source_ticker="03033.HK",
            posted_at=datetime(2026, 8, 22, 9),
            feed_type=1,
            title="正文明确提到同业",
            content="$03032.HK$",
            author_name="路人",
            like_count=0,
            comment_count=2,
            image_count=0,
            raw_json_broken=False,
        ))
        conn.execute(insert(mentions), [
            {"feed_id": 6, "code": OWN, "source": "anchor", "in_pool": True},
            {"feed_id": 6, "code": PEER, "source": "body", "in_pool": True},
        ])
        conn.execute(insert(feed_mentions).values(
            feed_id=6, raw_ticker="03032.HK", market="HK", occurrences=1
        ))
        conn.execute(insert(comments), [
            {
                "comment_id": 21,
                "feed_id": 6,
                "content": "手续费太高",
                "author_uid": "body-target-reader",
            },
            {
                "comment_id": 26,
                "feed_id": 6,
                "content": "$03032.HK$ 手续费太高",
                "author_uid": "explicit-body-target-reader",
            },
        ])

    extract.run(
        engine,
        cfg,
        codes=[PEER],
        date_from=datetime(2026, 8, 19),
        date_to=datetime(2026, 8, 25),
        ownership=OWNERSHIP,
        report_dir=tmp_path,
    )

    jobs = rows(engine, annotation_jobs, annotation_jobs.c.target_id.in_((21, 26)))
    assert jobs == []
    exact_rows = rows(engine, annotations, annotations.c.target_id == 21)
    assert exact_rows == []


def test_removed_body_target_reconciles_old_subject_outside_current_candidates(
    engine, cfg, tmp_path
):
    """A removed/changed parent ticker must retire a legacy off-anchor route."""

    old_at = datetime(2026, 8, 22, 10)
    with engine.begin() as conn:
        conn.execute(insert(feeds).values(
            feed_id=8,
            code=OWN,
            source_ticker="03033.HK",
            posted_at=datetime(2026, 8, 22, 9),
            feed_type=1,
            title="父帖已改投另一产品",
            content="$03037.HK$",
            author_name="路人",
            like_count=0,
            comment_count=1,
            image_count=0,
            raw_json_broken=False,
        ))
        conn.execute(insert(mentions), [
            {"feed_id": 8, "code": OWN, "source": "anchor", "in_pool": True},
            {"feed_id": 8, "code": "3037", "source": "body", "in_pool": True},
        ])
        conn.execute(insert(feed_mentions).values(
            feed_id=8, raw_ticker="03037.HK", market="HK", occurrences=1
        ))
        conn.execute(insert(comments).values(
            comment_id=25,
            feed_id=8,
            content="手续费太高",
            author_uid="legacy-body-route",
        ))
        conn.execute(insert(annotation_runs).values(
            run_id="old-route-run",
            task="comment_product",
            provider="openai_compatible",
            model_id="m",
            prompt_version="comment-product-v2",
            taxonomy_version="v2",
            schema_version="v2",
            started_at=old_at,
            status="done",
            input_count=1,
            success_count=1,
            error_count=0,
        ))
        conn.execute(insert(annotation_jobs).values(
            target_type="comment",
            target_id=25,
            subject_code=PEER,
            task="comment_product",
            input_hash="legacy-off-anchor-job",
            status="done",
            priority=0,
            attempts=1,
            scope_id=None,
            stage="llm",
            created_at=old_at,
            updated_at=old_at,
        ))
        old_result = conn.execute(insert(annotations).values(
            target_type="comment",
            target_id=25,
            subject_code=PEER,
            kind="relevance",
            value_json=json.dumps("relevant"),
            calibrated_confidence=None,
            run_id="old-route-run",
            input_hash="legacy-off-anchor-result",
            review_state="pending",
            created_at=old_at,
        ))
        old_annotation_id = old_result.inserted_primary_key[0]

    stats = extract.run(
        engine,
        cfg,
        codes=[PEER],
        date_from=datetime(2026, 8, 19),
        date_to=datetime(2026, 8, 25),
        ownership=OWNERSHIP,
        report_dir=tmp_path,
    )

    job = rows(engine, annotation_jobs, annotation_jobs.c.target_id == 25)[0]
    assert job["status"] == "superseded"
    assert job["last_error"] == "Excluded by parent-feed-v1:parent_feed_filter"
    current = current_annotations(
        engine, "relevance", "comment", ids=[25], subject_code=PEER
    )[(25, PEER)]
    assert current["value"] == "irrelevant"
    assert current["annotation_id"] != old_annotation_id
    chain = rows(
        engine,
        annotations,
        annotations.c.target_id == 25,
        annotations.c.kind == "relevance",
    )
    assert chain[-1]["supersedes_id"] == old_annotation_id
    assert stats["comments"]["rule_rows_written"] >= 1

    rerun = extract.run(
        engine,
        cfg,
        codes=[PEER],
        date_from=datetime(2026, 8, 19),
        date_to=datetime(2026, 8, 25),
        ownership=OWNERSHIP,
        report_dir=tmp_path,
    )
    assert rerun["comments"]["rule_rows_written"] == 0
    assert len(rows(
        engine,
        annotations,
        annotations.c.target_id == 25,
        annotations.c.kind == "relevance",
    )) == len(chain)


def test_candidate_paging_is_section_scoped_even_with_body_association(engine):
    with engine.begin() as conn:
        conn.execute(insert(mentions).values(
            feed_id=1,
            code=PEER,
            source="body",
            in_pool=True,
        ))

    candidates = list(extract._comment_candidates_hkt(
        engine,
        codes=[OWN, PEER],
        since=datetime(2026, 8, 19),
        until=datetime(2026, 8, 26),
        page_size=1,
    ))

    assert [(row.comment_id, row.code) for row in candidates if row.comment_id == 11] == [
        (11, OWN),
    ]


def test_reply_cashtag_never_routes_outside_parent_section(engine, cfg, tmp_path):
    with engine.begin() as conn:
        conn.execute(insert(mentions).values(
            feed_id=1,
            code=PEER,
            source="body",
            in_pool=True,
        ))
        conn.execute(insert(comments).values(
            comment_id=22,
            feed_id=1,
            content="$03037.HK$ 費率較低",
            author_uid="explicit-third-product",
        ))

    stats = extract.run(
        engine,
        cfg,
        codes=[OWN, PEER],
        date_from=datetime(2026, 8, 19),
        date_to=datetime(2026, 8, 25),
        ownership=OWNERSHIP,
        report_dir=tmp_path,
    )

    jobs = rows(engine, annotation_jobs, annotation_jobs.c.target_id == 22)
    assert [(row["target_id"], row["subject_code"]) for row in jobs] == [(22, OWN)]
    assert stats["comments"]["near_duplicate_members"] == 0


def test_resolve_codes_rejects_unknown():
    class A:
        codes = "3033,9999"
        all = False
    with pytest.raises(SystemExit):
        extract.resolve_codes(A(), OWNERSHIP)
