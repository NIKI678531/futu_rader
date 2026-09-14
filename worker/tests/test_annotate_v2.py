"""ADR-0020：v2 七维标注、产品别名、scope 隔离、并发领取。

与 `test_annotate.py` 同一套夹具思路，但配置是 v2（`comment-product-v2` / schema v2 / taxonomy v2）。
"""

import json
import os
import sys
import threading
from datetime import datetime

import pytest
from sqlalchemy import insert, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from ai import config, schemas  # noqa: E402
from ai.prompts import get as get_prompt  # noqa: E402
from ai.providers.base import Completion, Usage  # noqa: E402
from jobs import annotate  # noqa: E402
from radar_db import create_all, make_engine  # noqa: E402
from radar_db.schema import annotation_evidence, annotation_jobs, annotations, comments, feeds  # noqa: E402

CODE = "3033"
TEXT_A = "这只ETF点差太大，来回一趟就蚀掉不少，不太适合短线"
TEXT_B = "听说下季度要清盘，还没卖的赶紧走"
TEXT_C = "恒指要崩了"


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "t.db").as_posix())
    create_all(eng)
    with eng.begin() as conn:
        conn.execute(
            insert(feeds).values(
                feed_id=1, code=CODE, posted_at=datetime(2026, 8, 1, 10, 0), feed_type=1,
                title="恒科今日走势", content="今日恒科低开高走，" * 30,
                like_count=0, comment_count=3, image_count=0, raw_json_broken=False,
            )
        )
        conn.execute(
            insert(comments),
            [
                {"comment_id": 11, "feed_id": 1, "content": TEXT_A, "author_uid": "u1"},
                {"comment_id": 12, "feed_id": 1, "content": TEXT_B, "author_uid": "u2"},
                {"comment_id": 13, "feed_id": 1, "content": TEXT_C, "author_uid": "u3"},
            ],
        )
    return eng


@pytest.fixture()
def cfg():
    return config.load(model="test-model", micro_batch_size=30, max_retries=3,
                       prompt_version="comment-product-v2", schema_version="v2",
                       taxonomy_version="v2", concurrency=4)


class FakeProvider:
    def __init__(self, script, usage=None):
        self.script = list(script)
        self.calls = []
        self.messages = []
        self.usage = usage or Usage(100, 50, 10, 20)
        self.model = "test-model-2026-08-snapshot"
        self._lock = threading.Lock()

    def complete_json(self, system, user, schema, schema_name):
        ids = [line.split('"')[3] for line in user.splitlines() if '"item_id"' in line]
        with self._lock:
            self.calls.append(ids)
            self.messages.append(user)
            step = self.script.pop(0) if self.script else {"results": []}
        if callable(step):
            step = step(ids)
        if isinstance(step, Exception):
            raise step
        return Completion(data=step, model=self.model, usage=self.usage,
                          raw_text=json.dumps(step), response_id="resp_1")


def v2_item(item_id, **over):
    base = {
        "item_id": item_id,
        "relevance": "relevant",
        "attitude": "negative",
        "aspects": ["spread"],
        "evidence": "点差太大",
        "market_direction": None,
        "compliance_tags": [],
        "compliance_rationale": None,
        "compliance_evidence": None,
        "needs_review": False,
        "uncertainty_reasons": [],
    }
    base.update(over)
    return base


def by_target(ids):
    return {int(i.split(":")[1].split("|")[0]): i for i in ids}


def script_realistic(ids):
    out = []
    for cid, i in by_target(ids).items():
        if cid == 11:
            out.append(v2_item(i))
        elif cid == 12:
            out.append(v2_item(
                i, relevance="relevant", attitude="negative", aspects=["other"],
                evidence="还没卖的赶紧走",
                compliance_tags=["unverified_claim", "mobilization"],
                compliance_rationale="传播未附依据的清盘传闻并号召卖出",
                compliance_evidence="听说下季度要清盘",
            ))
        else:
            out.append(v2_item(i, relevance="irrelevant", attitude=None, aspects=[],
                               evidence=None, market_direction="bearish"))
    return {"results": out}


def rows(engine, table, *where):
    with engine.connect() as conn:
        return conn.execute(select(table).where(*where)).mappings().all()


# ── schema v2 ───────────────────────────────────────────────────────────


def test_v2_wire_schema_has_the_seven_dimensions():
    props = schemas.json_schema("comment_product", "v2")["properties"]
    assert {"relevance", "attitude", "aspects", "evidence", "market_direction",
            "compliance_tags", "compliance_rationale", "compliance_evidence"} <= set(props)
    # strict：全部必填、不许额外键。
    wire = schemas.batch_json_schema("comment_product", "v2")
    item = wire["$defs"]["CommentAnnotationV2"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"])


def test_v2_compliance_cross_field_rules():
    with pytest.raises(schemas.SchemaError, match="命中依据"):
        schemas.parse("comment_product", v2_item("x", compliance_tags=["mobilization"]), "v2")
    with pytest.raises(schemas.SchemaError, match="必须为 null"):
        schemas.parse("comment_product", v2_item("x", compliance_rationale="多余"), "v2")
    with pytest.raises(schemas.SchemaError, match="上限"):
        schemas.parse("comment_product", v2_item(
            "x", compliance_tags=["mobilization"], compliance_rationale="长" * 41), "v2")
    ok = schemas.parse("comment_product", v2_item(
        "x", relevance="irrelevant", attitude=None, aspects=[], evidence=None,
        market_direction="bearish"), "v2")
    assert ok.market_direction == "bearish"


def test_v1_schema_rejects_v2_shape_and_vice_versa():
    with pytest.raises(schemas.SchemaError):
        schemas.parse("comment_product", v2_item("x"), "v1")
    v1 = {k: v for k, v in v2_item("x").items()
          if k not in ("market_direction", "compliance_tags", "compliance_rationale",
                       "compliance_evidence")}
    with pytest.raises(schemas.SchemaError):
        schemas.parse("comment_product", v1, "v2")


def test_prompt_registry_defaults_to_v2_and_pairs_schema():
    assert get_prompt("comment_product").VERSION == "comment-product-v2"
    assert get_prompt("comment_product", "comment-product-v1").VERSION == "comment-product-v1"
    # 一个变量盖两个任务：给帖子任务传评论的版本号 ⇒ 落到帖子任务的默认版本。
    assert get_prompt("post_annotation", "comment-product-v2").VERSION == "post-annotation-v2"
    with pytest.raises(KeyError):
        get_prompt("comment_product", "comment-product-v9")


# ── payload：产品有名字、有帖子正文开头 ───────────────────────────────────


def test_payload_carries_product_name_aliases_and_post_context(engine, cfg):
    annotate.enqueue_comments(engine, cfg)
    prov = FakeProvider([script_realistic])
    annotate.run(engine, cfg, max_items=10, provider=prov)
    sent = "\n".join(prov.messages)
    assert "恒生科技指數ETF" in sent, "模型必须知道 3033 叫什么"
    assert "南方恒科" in sent
    assert '"post_context"' in sent
    # 正文只带开头，不带全文。
    ctx = [line for line in sent.splitlines() if '"post_context"' in line][0]
    assert len(ctx) < annotate.POST_CONTEXT_CHARS + 40


# ── 写库：五个 kind，合规空数组也写，合规证据挂在合规行 ─────────────────


def test_v2_writes_five_kinds_and_empty_compliance(engine, cfg):
    annotate.enqueue_comments(engine, cfg)
    stats = annotate.run(engine, cfg, max_items=10, provider=FakeProvider([script_realistic]))
    assert (stats["success"], stats["error"]) == (3, 0)
    assert stats["requests"] == 1
    assert stats["tok_cached"] == 20

    kinds = {(a["target_id"], a["kind"]) for a in rows(engine, annotations)}
    # 11：相关、负面、点差、合规空
    assert {(11, "relevance"), (11, "attitude"), (11, "aspect"), (11, "compliance")} <= kinds
    assert (11, "market_direction") not in kinds
    # 12：合规两类
    assert (12, "compliance") in kinds
    # 13：无关但有市场方向；合规仍要写「查过了没有」
    assert {(13, "relevance"), (13, "market_direction"), (13, "compliance")} <= kinds
    assert (13, "attitude") not in kinds

    comp = {a["target_id"]: json.loads(a["value_json"])
            for a in rows(engine, annotations, annotations.c.kind == "compliance")}
    assert comp[11] == {"tags": [], "rationale": None}
    assert comp[12]["tags"] == ["unverified_claim", "mobilization"]
    assert comp[13] == {"tags": [], "rationale": None}


def test_compliance_evidence_is_located_and_attached_to_the_compliance_row(engine, cfg):
    annotate.enqueue_comments(engine, cfg)
    annotate.run(engine, cfg, max_items=10, provider=FakeProvider([script_realistic]))
    comp_row = [a for a in rows(engine, annotations, annotations.c.target_id == 12)
                if a["kind"] == "compliance"][0]
    ev = rows(engine, annotation_evidence, annotation_evidence.c.annotation_id == comp_row["annotation_id"])
    assert [e["quote_text"] for e in ev] == ["听说下季度要清盘"]
    assert TEXT_B[ev[0]["start_offset"]:ev[0]["end_offset"]] == "听说下季度要清盘"
    # 态度证据挂在 relevance 行，不混到合规行。
    rel_row = [a for a in rows(engine, annotations, annotations.c.target_id == 12)
               if a["kind"] == "relevance"][0]
    ev2 = rows(engine, annotation_evidence, annotation_evidence.c.annotation_id == rel_row["annotation_id"])
    assert [e["quote_text"] for e in ev2] == ["还没卖的赶紧走"]


def test_compliance_tags_without_locatable_evidence_flag_review(engine, cfg):
    annotate.enqueue_comments(engine, cfg)

    def script(ids):
        return {"results": [
            v2_item(i, compliance_tags=["mobilization"], compliance_rationale="号召卖出",
                    compliance_evidence="这句原文里没有")
            for i in ids
        ]}
    annotate.run(engine, cfg, max_items=10, provider=FakeProvider([script]))
    anns = rows(engine, annotations)
    assert anns and all(a["review_state"] == "needs_review" for a in anns)


def test_irrelevant_with_market_direction_is_not_flagged(engine, cfg):
    annotate.enqueue_comments(engine, cfg)

    def script(ids):
        return {"results": [
            v2_item(i, relevance="irrelevant", attitude=None, aspects=[], evidence=None,
                    market_direction="bearish") for i in ids
        ]}
    annotate.run(engine, cfg, max_items=10, provider=FakeProvider([script]))
    anns = rows(engine, annotations)
    assert anns and all(a["review_state"] == "pending" for a in anns)


# ── scope 隔离 ─────────────────────────────────────────────────────────


def test_claim_is_scoped(engine, cfg):
    annotate.enqueue_comments(engine, cfg, codes=[CODE], scope_id="scope-A")
    # 同样的候选换个 scope 不会重复排队（唯一键不含 scope）。
    assert annotate.enqueue_comments(engine, cfg, codes=[CODE], scope_id="scope-B") == 0
    assert annotate.claim(engine, "comment_product", 10, scope_id="scope-B") == []
    assert len(annotate.claim(engine, "comment_product", 10, scope_id="scope-A")) == 3
    assert annotate.pending_count(engine, "comment_product", "scope-A") == 3  # claimed 也算在途


def test_run_with_scope_leaves_other_scopes_alone(engine, cfg):
    annotate.enqueue_comments(engine, cfg, scope_id="scope-A")
    with engine.begin() as conn:
        conn.execute(insert(annotation_jobs).values(
            target_type="comment", target_id=13, subject_code="7226", task="comment_product",
            input_hash="other", status="pending", priority=0, attempts=0, scope_id="scope-Z",
            created_at=datetime(2026, 8, 1), updated_at=datetime(2026, 8, 1)))
    annotate.run(engine, cfg, max_items=50, scope_id="scope-A",
                 provider=FakeProvider([script_realistic]))
    other = rows(engine, annotation_jobs, annotation_jobs.c.scope_id == "scope-Z")[0]
    assert other["status"] == "pending"


# ── 并发 ─────────────────────────────────────────────────────────────────


def test_concurrent_batches_do_not_double_process(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "c.db").as_posix())
    create_all(eng)
    n = 40
    with eng.begin() as conn:
        conn.execute(insert(feeds).values(
            feed_id=1, code=CODE, posted_at=datetime(2026, 8, 1), feed_type=1,
            like_count=0, comment_count=n, image_count=0, raw_json_broken=False))
        conn.execute(insert(comments), [
            {"comment_id": 100 + i, "feed_id": 1, "content": f"点差太大 第{i}条", "author_uid": f"u{i}"}
            for i in range(n)
        ])
    cfg = config.load(model="m", micro_batch_size=5, max_retries=3, concurrency=4,
                      prompt_version="comment-product-v2", schema_version="v2", taxonomy_version="v2")
    annotate.enqueue_comments(eng, cfg)
    prov = FakeProvider([lambda ids: {"results": [v2_item(i) for i in ids]}] * 20)
    stats = annotate.run(eng, cfg, max_items=n, provider=prov)
    assert stats["success"] == n and stats["error"] == 0
    assert stats["requests"] == n // 5
    seen = [i for call in prov.calls for i in call]
    assert len(seen) == len(set(seen)) == n, "同一条任务不许被两个线程各发一遍"
    assert all(j["status"] == "done" for j in rows(eng, annotation_jobs))


def test_budget_requests_caps_batches(engine, cfg):
    cfg_small = config.load(model="m", micro_batch_size=1, max_retries=3, concurrency=1,
                            prompt_version="comment-product-v2", schema_version="v2",
                            taxonomy_version="v2")
    annotate.enqueue_comments(engine, cfg_small)
    prov = FakeProvider([lambda ids: {"results": [v2_item(i) for i in ids]}] * 5)
    stats = annotate.run(engine, cfg_small, max_items=10, provider=prov, budget_requests=2)
    assert stats["requests"] == 2
    assert annotate.pending_count(engine, "comment_product") == 1


def test_estimate_is_a_range_not_a_price(engine, cfg):
    annotate.enqueue_comments(engine, cfg)
    est = annotate.estimate(engine, cfg, "comment_product")
    assert est["pending_items"] == 3 and est["requests"] == 1
    assert est["tokens_in_low"] < est["tokens_in_high"]
    assert "estimated_cost" not in est or est.get("estimated_cost") is None
