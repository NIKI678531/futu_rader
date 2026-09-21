"""`kol_comment_opinion`：v1 写观点/操作，v2 另写评论自身的内容形式。"""

import json
import os
import sys
from datetime import datetime

import pytest
from sqlalchemy import insert, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ai import config, schemas  # noqa: E402
from ai.providers.base import Completion, Usage  # noqa: E402
from jobs import annotate  # noqa: E402
from radar_db import create_all, make_engine  # noqa: E402
from radar_db.schema import annotation_evidence, annotation_jobs, annotations, comments, feeds  # noqa: E402

KOL = "孫子的末代傳人"


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "k.db").as_posix())
    create_all(eng)
    with eng.begin() as conn:
        conn.execute(insert(feeds).values(feed_id=1, code="3033", posted_at=datetime(2026, 8, 20), feed_type=1,
                                          like_count=0, comment_count=2, image_count=0, raw_json_broken=False))
        conn.execute(insert(comments), [
            {"comment_id": 1, "feed_id": 1, "content": "費率低，準備長期定投呢隻", "author_name": KOL, "author_uid": "k"},
            {"comment_id": 2, "feed_id": 1, "content": "路人的評論", "author_name": "路人", "author_uid": "r"},
        ])
    return eng


@pytest.fixture()
def cfg():
    return config.load(model="m", prompt_version="kol-opinion-v1", schema_version="v1", taxonomy_version="v2")


@pytest.fixture()
def cfg_v2():
    return config.load(model="m", prompt_version="kol-opinion-v2", schema_version="v2", taxonomy_version="v2")


class Prov:
    def __init__(self, summary="準備長期定投", action="加仓", evidence="準備長期定投"):
        self.args = (summary, action, evidence)
        self.messages = []

    def complete_json(self, system, user, schema, name):
        self.messages.append(user)
        ids = [line.split('"')[3] for line in user.splitlines() if '"item_id"' in line]
        s, a, e = self.args
        data = {"results": [{"item_id": i, "summary": s, "action": a, "evidence": e, "needs_review": False} for i in ids]}
        return Completion(data=data, model="m", usage=Usage(10, 5, 0, 0), raw_text="", response_id="r")


class ProvV2(Prov):
    def __init__(self, summary="準備長期定投", action="加仓", evidence="準備長期定投",
                 post_type="action"):
        super().__init__(summary, action, evidence)
        self.post_type = post_type

    def complete_json(self, system, user, schema, name):
        self.messages.append(user)
        ids = [line.split('"')[3] for line in user.splitlines() if '"item_id"' in line]
        s, a, e = self.args
        data = {
            "results": [
                {
                    "item_id": i,
                    "summary": s,
                    "post_type": self.post_type,
                    "action": a,
                    "evidence": e,
                    "needs_review": False,
                }
                for i in ids
            ]
        }
        return Completion(data=data, model="m", usage=Usage(10, 5, 0, 0), raw_text="", response_id="r")


def test_schema_enum_and_length():
    with pytest.raises(schemas.SchemaError):
        schemas.parse("kol_comment_opinion", {"item_id": "x", "summary": "s", "action": "抄底", "evidence": None, "needs_review": False})
    with pytest.raises(schemas.SchemaError, match="上限"):
        schemas.parse("kol_comment_opinion", {"item_id": "x", "summary": "长" * 31, "action": "加仓", "evidence": None, "needs_review": False})


def test_v2_schema_requires_a_valid_content_type():
    row = {
        "item_id": "x",
        "summary": "準備長期定投",
        "post_type": "action",
        "action": "加仓",
        "evidence": "準備長期定投",
        "needs_review": False,
    }
    assert schemas.parse("kol_comment_opinion", row, "v2").post_type == "action"
    with pytest.raises(schemas.SchemaError):
        schemas.parse("kol_comment_opinion", {k: v for k, v in row.items() if k != "post_type"}, "v2")
    with pytest.raises(schemas.SchemaError):
        schemas.parse("kol_comment_opinion", {**row, "post_type": "unknown"}, "v2")


def test_shared_v1_config_selects_matching_kol_prompt():
    shared = config.load(
        model="m",
        prompt_version="comment-product-v1",
        schema_version="v1",
        taxonomy_version="v2",
    )
    prompt, schema_version = annotate.resolve("kol_comment_opinion", shared)
    assert prompt.VERSION == "kol-opinion-v1"
    assert schema_version == "v1"


def test_explicit_prompt_schema_mismatch_fails_fast():
    mismatched = config.load(
        model="m",
        prompt_version="kol-opinion-v2",
        schema_version="v1",
        taxonomy_version="v2",
    )
    with pytest.raises(config.ConfigError, match="kol-opinion-v2.*v2.*v1"):
        annotate.resolve("kol_comment_opinion", mismatched)


def test_enqueue_only_kol_authors_and_run_writes_two_kinds(engine, cfg):
    assert annotate.enqueue_kol_comments(engine, cfg, [KOL]) == 1
    with engine.connect() as conn:
        jobs = conn.execute(select(annotation_jobs)).mappings().all()
    assert [j["target_id"] for j in jobs] == [1] and jobs[0]["task"] == "kol_comment_opinion"

    prov = Prov()
    stats = annotate.run(engine, cfg, task="kol_comment_opinion", max_items=10, provider=prov)
    assert stats["success"] == 1
    assert "恒生科技指數ETF" in prov.messages[0]
    with engine.connect() as conn:
        anns = conn.execute(select(annotations).order_by(annotations.c.annotation_id)).mappings().all()
        ev = conn.execute(select(annotation_evidence)).mappings().all()
    assert {(a["kind"], json.loads(a["value_json"])) for a in anns} == {("kol_summary", "準備長期定投"), ("kol_action", "加仓")}
    assert len(ev) == 1 and ev[0]["quote_text"] == "準備長期定投"
    assert all(a["review_state"] == "pending" for a in anns)


def test_no_opinion_writes_false_placeholder(engine, cfg):
    annotate.enqueue_kol_comments(engine, cfg, [KOL])
    annotate.run(engine, cfg, task="kol_comment_opinion", max_items=10,
                 provider=Prov(summary=None, action="未提及操作", evidence=None))
    with engine.connect() as conn:
        anns = {a["kind"]: json.loads(a["value_json"]) for a in conn.execute(select(annotations)).mappings()}
    assert anns == {"kol_summary": False, "kol_action": "未提及操作"}


def test_v2_run_writes_type_summary_and_action(engine, cfg_v2):
    assert annotate.enqueue_kol_comments(engine, cfg_v2, [KOL]) == 1
    stats = annotate.run(
        engine,
        cfg_v2,
        task="kol_comment_opinion",
        max_items=10,
        provider=ProvV2(),
    )
    assert stats["success"] == 1
    with engine.connect() as conn:
        anns = {
            a["kind"]: json.loads(a["value_json"])
            for a in conn.execute(select(annotations)).mappings()
        }
    assert anns == {
        "kol_summary": "準備長期定投",
        "kol_action": "加仓",
        "post_type": "action",
    }


def test_v1_to_v2_migration_creates_new_job_and_supersedes_current_rows(
    engine, cfg, cfg_v2
):
    assert annotate.enqueue_kol_comments(engine, cfg, [KOL]) == 1
    assert annotate.run(
        engine,
        cfg,
        task="kol_comment_opinion",
        max_items=10,
        provider=Prov(),
    )["success"] == 1

    with engine.connect() as conn:
        first_job = conn.execute(select(annotation_jobs)).mappings().one()
        first_rows = conn.execute(
            select(annotations).order_by(annotations.c.annotation_id)
        ).mappings().all()

    assert annotate.enqueue_kol_comments(engine, cfg_v2, [KOL]) == 1
    with engine.connect() as conn:
        jobs = conn.execute(
            select(annotation_jobs).order_by(annotation_jobs.c.job_id)
        ).mappings().all()
    assert len(jobs) == 2
    assert jobs[1]["input_hash"] != first_job["input_hash"]

    assert annotate.run(
        engine,
        cfg_v2,
        task="kol_comment_opinion",
        max_items=10,
        provider=ProvV2(post_type="market"),
    )["success"] == 1

    with engine.connect() as conn:
        all_rows = conn.execute(
            select(annotations).order_by(annotations.c.annotation_id)
        ).mappings().all()

    first_by_kind = {row["kind"]: row for row in first_rows}
    second_by_kind = {row["kind"]: row for row in all_rows[len(first_rows):]}
    assert second_by_kind["kol_summary"]["supersedes_id"] == first_by_kind["kol_summary"]["annotation_id"]
    assert second_by_kind["kol_action"]["supersedes_id"] == first_by_kind["kol_action"]["annotation_id"]
    assert second_by_kind["post_type"]["supersedes_id"] is None
    assert {row["input_hash"] for row in second_by_kind.values()} == {jobs[1]["input_hash"]}

    superseded_ids = {
        row["supersedes_id"] for row in all_rows if row["supersedes_id"] is not None
    }
    current = {
        row["kind"]: json.loads(row["value_json"])
        for row in all_rows
        if row["annotation_id"] not in superseded_ids
        and row["review_state"] != "rejected"
    }
    assert current == {
        "kol_summary": "準備長期定投",
        "kol_action": "加仓",
        "post_type": "market",
    }


def test_kol_and_comment_product_jobs_do_not_collide(engine, cfg):
    cfg2 = config.load(model="m", prompt_version="comment-product-v2", schema_version="v2", taxonomy_version="v2")
    assert annotate.enqueue_comments(engine, cfg2) == 2
    assert annotate.enqueue_kol_comments(engine, cfg, [KOL]) == 1
    assert annotate.pending_count(engine, "comment_product") == 2
    assert annotate.pending_count(engine, "kol_comment_opinion") == 1
