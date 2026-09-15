"""ADR-0021 L1：学生分类作业 —— 四条路由规则、簇成员直接 done、没模型时放行 Luna、事件与 run 记录。

学生模型用假对象注入（`student=`），不依赖 torch／onnxruntime；每条评论的预测由文本决定。
"""

import json
import os
import sys
from datetime import datetime

import pytest
from sqlalchemy import insert, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ai import config  # noqa: E402
from ai import neardup  # noqa: E402
from jobs import annotate, classify  # noqa: E402
from radar_db import create_all, make_engine  # noqa: E402
from radar_db.events import recent  # noqa: E402
from radar_db.schema import annotation_jobs, annotation_runs, annotations, comments, feeds  # noqa: E402

CODE = "3033"
# 每条文本对应一条固定预测（见 FakeStudent.TABLE）。
T_CONFIDENT = "这只ETF点差太大，来回一趟就蚀掉不少，不太适合短线"
T_LOWCONF = "感觉还行吧这只"
T_NEEDCTX = "有"
T_MARGIN = "涨了不少但也可能回调，这只再看看"
T_COMPLIANCE = "听说下季度要清盘，还没卖的赶紧走"
T_IRRELEVANT = "恒指要崩了"
T_MEMBER = "这只ETF点差太大，来回一趟就蚀掉不少，不太适合短线。"  # 与 T_CONFIDENT 只差一个句号


def pred(rel, rel_p, att=None, att_p=None, margin=None):
    out = {"relevance": {"label": rel, "max": rel_p, "margin": rel_p - (1 - rel_p) / 2,
                         "probs": {rel: rel_p}}}
    if att is not None:
        out["attitude"] = {"label": att, "max": att_p, "margin": margin if margin is not None else att_p - 0.05,
                           "probs": {att: att_p}}
    else:
        out["attitude"] = {"label": "neutral", "max": 0.5, "margin": 0.2, "probs": {"neutral": 0.5}}
    return out


class FakeStudent:
    model_id = "fake/student@test"
    TABLE = {
        T_CONFIDENT: pred("relevant", 0.97, "negative", 0.95),
        T_LOWCONF: pred("relevant", 0.62, "positive", 0.9),
        T_NEEDCTX: pred("needs_context", 0.93),
        T_MARGIN: pred("relevant", 0.95, "positive", 0.9, margin=0.08),
        T_COMPLIANCE: pred("relevant", 0.98, "negative", 0.96),
        T_IRRELEVANT: pred("irrelevant", 0.99),
        T_MEMBER: pred("relevant", 0.97, "negative", 0.95),
    }

    def __init__(self):
        self.calls = []

    def predict(self, texts):
        self.calls.append(list(texts))
        out = []
        for t in texts:
            body = t.split("\n", 1)[0].replace("评论：", "", 1)
            out.append(self.TABLE[body])
        return out


@pytest.fixture()
def cfg():
    return config.load(model="test-model", micro_batch_size=30, max_retries=3,
                       prompt_version="comment-product-v2", schema_version="v2", taxonomy_version="v2")


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "t.db").as_posix())
    create_all(eng)
    with eng.begin() as conn:
        conn.execute(insert(feeds).values(
            feed_id=1, code=CODE, posted_at=datetime(2026, 8, 20, 10, 0), feed_type=1,
            title="恒科今日走势", content="今日恒科低开高走", like_count=0, comment_count=7, image_count=0,
            raw_json_broken=False))
        conn.execute(insert(comments), [
            {"comment_id": 11, "feed_id": 1, "content": T_CONFIDENT, "author_uid": "u1"},
            {"comment_id": 12, "feed_id": 1, "content": T_LOWCONF, "author_uid": "u2"},
            {"comment_id": 13, "feed_id": 1, "content": T_NEEDCTX, "author_uid": "u3"},
            {"comment_id": 14, "feed_id": 1, "content": T_MARGIN, "author_uid": "u4"},
            {"comment_id": 15, "feed_id": 1, "content": T_COMPLIANCE, "author_uid": "u5"},
            {"comment_id": 16, "feed_id": 1, "content": T_IRRELEVANT, "author_uid": "u6"},
            {"comment_id": 17, "feed_id": 1, "content": T_MEMBER, "author_uid": "u7"},
        ])
    return eng


def _jobs(engine):
    with engine.connect() as conn:
        return {r.target_id: r for r in conn.execute(select(annotation_jobs))}


def _rows(engine, cid, kind):
    with engine.connect() as conn:
        return conn.execute(select(annotations).where(
            annotations.c.target_id == cid, annotations.c.kind == kind,
        ).order_by(annotations.c.annotation_id)).mappings().all()


def test_route_reasons_four_rules():
    assert classify.route_reasons(FakeStudent.TABLE[T_CONFIDENT], T_CONFIDENT) == []
    assert classify.route_reasons(FakeStudent.TABLE[T_LOWCONF], T_LOWCONF) == ["low_confidence"]
    assert classify.route_reasons(FakeStudent.TABLE[T_NEEDCTX], T_NEEDCTX) == ["needs_context"]
    assert classify.route_reasons(FakeStudent.TABLE[T_MARGIN], T_MARGIN) == ["attitude_margin"]
    assert classify.route_reasons(FakeStudent.TABLE[T_COMPLIANCE], T_COMPLIANCE) == ["compliance_lexicon"]
    # 无关评论不看态度头：态度 0.5 不算低置信。
    assert classify.route_reasons(FakeStudent.TABLE[T_IRRELEVANT], T_IRRELEVANT) == []


def test_run_writes_student_rows_and_routes(engine, cfg):
    annotate.enqueue_comments(engine, cfg, codes=[CODE])
    jobs = _jobs(engine)
    assert all(j.stage == "student" for j in jobs.values())

    st = FakeStudent()
    stats = classify.run(engine, cfg, student=st)
    assert stats["input"] == 7 and stats["written"] == 7
    assert stats["relevance"] == {"relevant": 5, "irrelevant": 1, "needs_context": 1}
    assert stats["routed"] == 4 and stats["done"] == 3
    assert stats["routes"] == {"low_confidence": 1, "needs_context": 1, "attitude_margin": 1, "compliance_lexicon": 1}

    jobs = _jobs(engine)
    for cid in (12, 13, 14, 15):
        assert (jobs[cid].stage, jobs[cid].status) == ("llm", "pending"), cid
    for cid in (11, 16, 17):
        assert (jobs[cid].stage, jobs[cid].status) == ("student", "done"), cid

    # 学生行：provider=local_model、model_id 带 @revision、calibrated_confidence=预测类概率。
    rel = _rows(engine, 11, "relevance")
    assert len(rel) == 1 and json.loads(rel[0]["value_json"]) == "relevant"
    assert rel[0]["calibrated_confidence"] == pytest.approx(0.97)
    assert rel[0]["review_state"] == "pending"
    att = _rows(engine, 11, "attitude")
    assert json.loads(att[0]["value_json"]) == "negative" and att[0]["calibrated_confidence"] == pytest.approx(0.95)
    with engine.connect() as conn:
        run = conn.execute(select(annotation_runs).where(annotation_runs.c.run_id == rel[0]["run_id"])).mappings().one()
    assert run["provider"] == "local_model" and run["model_id"] == "fake/student@test"
    assert run["status"] == "done" and run["success_count"] == 7
    assert run["token_input"] is None  # 本地推理没有 token 这个量

    # 低置信的学生行挂 needs_review；无关评论不写态度行。
    assert _rows(engine, 12, "relevance")[0]["review_state"] == "needs_review"
    assert _rows(engine, 16, "attitude") == []
    assert _rows(engine, 13, "attitude") == []

    # 未路由的补一行 compliance（词表筛），provider 用规则 run；路由走的不补（Luna 来写）。
    comp = _rows(engine, 11, "compliance")
    assert len(comp) == 1 and json.loads(comp[0]["value_json"]) == {"tags": [], "rationale": None, "screen": "lexicon"}
    with engine.connect() as conn:
        assert conn.execute(select(annotation_runs.c.provider).where(
            annotation_runs.c.run_id == comp[0]["run_id"])).scalar_one() == "rule"
    assert _rows(engine, 15, "compliance") == []

    # 事件：一批一条 L1，形如「3033 批 7 → 相关 5 / 无关 1 / 需上下文 1 → 路由 Luna 4」。
    evs = [e for e in recent(engine) if e["stage"] == "L1"]
    assert len(evs) == 1
    assert evs[0]["message"].startswith("3033 批 7 → 相关 5 / 无关 1 / 需上下文 1 → 路由 Luna 4")
    assert evs[0]["data"]["routed"] == 4 and evs[0]["code"] == CODE

    # 幂等：再跑一次没有可领的，不再写行。
    again = classify.run(engine, cfg, student=st)
    assert again["input"] == 0 and len(_rows(engine, 11, "relevance")) == 1


def test_luna_supersedes_student_row(engine, cfg):
    """路由给 Luna 的任务：Luna 的行 supersede 学生行，链上两版都在。"""
    annotate.enqueue_comments(engine, cfg, codes=[CODE], limit=None)
    classify.run(engine, cfg, student=FakeStudent())
    student_row = _rows(engine, 12, "relevance")[0]

    # Luna 只领 stage=llm 的任务。
    llm_jobs = annotate.claim(engine, "comment_product", 100, stage="llm")
    assert sorted(j["target_id"] for j in llm_jobs) == [12, 13, 14, 15]
    job = next(j for j in llm_jobs if j["target_id"] == 12)
    src = annotate._load_sources(engine, "comment_product", [job])[("comment", 12)]
    from ai import schemas
    item = schemas.parse_batch("comment_product", {"results": [{
        "item_id": annotate._item_id("comment_product", job), "relevance": "relevant", "attitude": "positive",
        "aspects": [], "evidence": "还行", "market_direction": None, "compliance_tags": [],
        "compliance_rationale": None, "compliance_evidence": None, "needs_review": False, "uncertainty_reasons": [],
    }]}, [annotate._item_id("comment_product", job)], "v2")
    with engine.begin() as conn:
        conn.execute(insert(annotation_runs).values(
            run_id="luna-1", task="comment_product", provider="openai_compatible", model_id="m",
            prompt_version="p", taxonomy_version="v2", schema_version="v2", started_at=datetime(2026, 8, 21),
            status="running", input_count=0, success_count=0, error_count=0))
    annotate._write_batch(engine, "comment_product", [(job, src, item[annotate._item_id("comment_product", job)])],
                          "luna-1", "v2")
    rows = _rows(engine, 12, "relevance")
    assert len(rows) == 2 and rows[1]["supersedes_id"] == student_row["annotation_id"]
    assert rows[1]["run_id"] == "luna-1"


def test_cluster_member_done_without_inference(engine, cfg):
    """簇成员（有现行 duplicate_cluster 行）领到就 done，不进推理；代表写完后成员拿到副本。"""
    annotate.enqueue_comments(engine, cfg, codes=[CODE])
    with engine.begin() as conn:
        conn.execute(insert(annotation_runs).values(
            run_id="rule-x", task="comment_product", provider="rule", model_id="prefilter-v1",
            prompt_version="prefilter-v1", taxonomy_version="v2", schema_version="v2",
            started_at=datetime(2026, 8, 21), status="done", input_count=0, success_count=0, error_count=0))
    neardup.write_cluster_rows(engine, "rule-x", [(17, CODE, 11, 1)], datetime(2026, 8, 21))

    st = FakeStudent()
    stats = classify.run(engine, cfg, student=st)
    assert stats["members"] == 1
    assert 17 not in {t for call in st.calls for t in call}  # 文本里没有成员
    assert _jobs(engine)[17].status == "done"
    prop = _rows(engine, 17, "relevance")
    assert len(prop) == 1 and json.loads(prop[0]["value_json"]) == "relevant"
    with engine.connect() as conn:
        assert conn.execute(select(annotation_runs.c.provider).where(
            annotation_runs.c.run_id == prop[0]["run_id"])).scalar_one() == "propagated"


def test_no_student_model_routes_everything_to_llm(engine, cfg, tmp_path):
    annotate.enqueue_comments(engine, cfg, codes=[CODE])
    stats = classify.run(engine, cfg, model_dir=tmp_path / "nope")
    assert stats["student_available"] is False and stats["routed"] == 7
    jobs = _jobs(engine)
    assert all((j.stage, j.status) == ("llm", "pending") for j in jobs.values())
    evs = recent(engine)
    assert evs and evs[-1]["level"] == "warn" and "放行 Luna" in evs[-1]["message"]
    with pytest.raises(Exception):
        classify.run(engine, cfg, model_dir=tmp_path / "nope", require_model=True)


def test_dry_run_and_limit(engine, cfg):
    annotate.enqueue_comments(engine, cfg, codes=[CODE])
    dry = classify.run(engine, cfg, student=FakeStudent(), dry_run=True)
    assert dry["dry_run"] is True and dry["pending_before"] == 7 and dry["written"] == 0
    part = classify.run(engine, cfg, student=FakeStudent(), limit=3, batch_size=2)
    assert part["input"] == 3 and part["batches"] == 2
    assert annotate.pending_count(engine, "comment_product", stage="student") == 4
