"""`jobs/pipeline.py` 一次过 ＋ `jobs/audit.py` 报表：端到端（假 provider），不出网。"""

import json
import os
import sys
from datetime import datetime

import pytest
from sqlalchemy import insert, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "backend"))

from ai import config  # noqa: E402
from ai.providers.base import Completion, Usage  # noqa: E402
from jobs import audit, extract, pipeline  # noqa: E402
from radar_db import create_all, make_engine  # noqa: E402
from radar_db.schema import annotations, comments, feeds, meta_kv, synthesis_outputs  # noqa: E402

CODE = "3033"
KOL = "孫子的末代傳人"


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "p.db").as_posix())
    create_all(eng)
    with eng.begin() as conn:
        conn.execute(insert(meta_kv).values(k="anchor", v="2026-08-25"))
        conn.execute(insert(feeds).values(feed_id=1, code=CODE, posted_at=datetime(2026, 8, 22, 10), feed_type=1,
                                          title="恒科", content="正文", author_name=KOL,
                                          like_count=0, comment_count=0, image_count=0, raw_json_broken=False))
        rows = []
        for i in range(14):
            rows.append({"comment_id": 100 + i, "feed_id": 1,
                         "content": f"費率係同類最低第{i}條" if i < 9 else f"點差太大第{i}條" if i < 13 else "騰訊業績好",
                         "author_name": KOL if i < 2 else "路人", "author_uid": f"u{i}"})
        conn.execute(insert(comments), rows)
    return eng


@pytest.fixture()
def cfg():
    return config.load(model="m", prompt_version="comment-product-v2", schema_version="v2", taxonomy_version="v2",
                       micro_batch_size=30, concurrency=2)


class UniversalFake:
    """按 schema_name 分派：评论 v2 / KOL 观点 / 帖子 / 七种 synth。"""

    def __init__(self):
        self.calls = []

    def complete_json(self, system, user, schema, schema_name):
        self.calls.append(schema_name)
        if schema_name.startswith("synth_"):
            kind = schema_name[6:]
            payload = json.loads(user.split("\n\n", 1)[1])
            ids = [e["id"] for e in payload["evidence"]]
            f = payload["facts"]
            if kind == "hot_summary":
                data = {"text": "費率獲認可", "evidence_ids": ids[:1], "needs_review": False}
            elif kind == "summary":
                data = {"points": [{"text": "多條評論認可費率", "evidence_ids": ids[:1]}], "needs_review": False}
            elif kind in ("theme_label", "neg_category"):
                data = {"results": [{"key": b["key"], "title": "費率同類最低", "summary": "如此。",
                                     "evidence_ids": b["evidence_ids"][:1]} for b in f["buckets"]]}
            elif kind == "stage_unit":
                data = {"results": [{"key": u["key"], "category": "add_opportunity", "digest": "加倉為主",
                                     "evidence_ids": u["evidence_ids"][:1], "needs_review": False} for u in f["units"]]}
            elif kind == "stage_summary":
                data = {"results": [{"key": s["key"], "summary": "連續看多", "evidence_ids": s["evidence_ids"][:1]} for s in f["stages"]]}
            elif kind == "topic_label":
                data = {"title": "大市方向", "summary": "看空為主", "evidence_ids": ids[:1]}
            else:
                data = {"results": [{"code": c["code"], "like_reasons": [], "dislike_reasons": [], "evidence_ids": [],
                                     "needs_review": True} for c in f["competitors"]]}
        else:
            ids = [line.split('"')[3] for line in user.splitlines() if '"item_id"' in line]
            if schema_name == "kol_comment_opinion_batch":
                data = {"results": [{"item_id": i, "summary": "費率低長期持有", "action": "加仓", "evidence": "費率係同類最低",
                                     "needs_review": False} for i in ids]}
            elif schema_name == "post_annotation_batch":
                data = {"results": [{"item_id": i, "post_type": "market", "direction": None, "direction_pending": False,
                                     "summary": "恒科走勢", "evidence_spans": ["正文"], "needs_review": False} for i in ids]}
            else:
                res = []
                for i in ids:
                    cid = int(i.split(":")[1].split("|")[0])
                    neg = cid >= 109
                    res.append({"item_id": i, "relevance": "relevant", "attitude": "negative" if neg else "positive",
                                "aspects": ["spread"] if neg else ["fee"],
                                "evidence": "點差太大" if neg else "費率係同類最低",
                                "market_direction": None, "compliance_tags": [], "compliance_rationale": None,
                                "compliance_evidence": None, "needs_review": False, "uncertainty_reasons": []})
                data = {"results": res}
        return Completion(data=data, model="m-snap", usage=Usage(100, 40, 5, 0), raw_text="", response_id="x")


def test_extract_then_pipeline_end_to_end(engine, cfg, tmp_path):
    ext = extract.run(engine, cfg, codes=[CODE], date_from=datetime(2026, 8, 19), date_to=datetime(2026, 8, 25),
                      task="both", ownership={CODE: "own"}, report_dir=tmp_path)
    sid = ext["scope_id"]
    assert ext["comments"]["dropped"]["offpool_stock_only"] == 1   # 騰訊那条
    assert ext["comments"]["kept"] == 13

    # 抽取时顺手把合作 KOL 的评论排进了 kol_comment_opinion（pipeline 第二步要它）
    assert ext["kol_comments"] == 2

    prov = UniversalFake()
    out = pipeline.run(engine, cfg, sid, provider=prov, ranges=["d7"])
    steps = {s["task"]: s for s in out["steps"]}
    assert steps["comment_product"]["success"] == 13 and steps["comment_product"]["error"] == 0
    assert steps["kol_comment_opinion"]["success"] == 2
    assert steps["post_annotation"]["success"] == 1
    assert steps["synthesize"]["errors"] == 0 and steps["synthesize"]["written"] >= 4
    assert "aborted" not in out
    assert out["audit"]["queue"]["comment_product"]["done"] == 13
    assert out["audit"]["evidence_located_rate"] == 1.0

    with engine.connect() as conn:
        kinds = {r[0] for r in conn.execute(select(synthesis_outputs.c.kind))}
    assert {"hot_summary", "summary", "theme_label", "neg_category", "stage_unit"} <= kinds

    # 再跑一次：队列空、生成物指纹相同 ⇒ 一次请求都不发
    prov2 = UniversalFake()
    out2 = pipeline.run(engine, cfg, sid, provider=prov2, ranges=["d7"])
    assert prov2.calls == []
    assert all("skipped" in s for s in out2["steps"] if s["task"] != "synthesize")


def test_pipeline_dry_run_estimates_only(engine, cfg, tmp_path):
    ext = extract.run(engine, cfg, codes=[CODE], date_from=datetime(2026, 8, 19), date_to=datetime(2026, 8, 25),
                      ownership={CODE: "own"}, report_dir=tmp_path)
    out = pipeline.run(engine, cfg, ext["scope_id"], dry_run=True, ranges=["d7"])
    est = out["steps"][0]["estimate"]
    assert est["pending_items"] == 13 and est["requests"] == 1
    with engine.connect() as conn:
        assert conn.execute(select(annotations).where(annotations.c.kind == "attitude")).first() is None


def test_incomplete_pipeline_does_not_synthesize(engine, cfg, tmp_path):
    ext = extract.run(engine, cfg, codes=[CODE], date_from=datetime(2026, 8, 19),
                      date_to=datetime(2026, 8, 25), ownership={CODE: "own"}, report_dir=tmp_path)
    provider = UniversalFake()
    result = pipeline.run(engine, cfg, ext["scope_id"], provider=provider,
                          max_items=1, ranges=["d7"])
    assert result["complete"] is False
    assert not any(call.startswith("synth_") for call in provider.calls)
    with engine.connect() as conn:
        assert conn.execute(select(synthesis_outputs)).first() is None


def test_audit_report_never_reports_accuracy(engine, cfg, tmp_path):
    ext = extract.run(engine, cfg, codes=[CODE], date_from=datetime(2026, 8, 19), date_to=datetime(2026, 8, 25),
                      ownership={CODE: "own"}, report_dir=tmp_path)
    pipeline.run(engine, cfg, ext["scope_id"], provider=UniversalFake(), skip_synth=True)
    rep = audit.report(engine, ext["scope_id"])
    text = json.dumps(rep, ensure_ascii=False, default=str)
    assert "准确率" not in text and "accuracy" not in text.lower()
    assert rep["estimated_cost"] is None
    assert rep["prefilter_dropped_by_rule"] == {"offpool_stock_only": 1}
    assert rep["attitude_by_product"][CODE]["positive"] == 9
    assert rep["compliance"]["scanned_units"] == 13
    assert audit.lexicon_recall(engine) == []          # 没有词表命中
    rows = audit.sample_rows(engine, 5)
    assert len(rows) == 5 and all(r["relevance"] for r in rows)


def test_ready_range_synthesizes_while_older_comments_remain(engine, cfg, tmp_path, monkeypatch):
    ext = extract.run(engine, cfg, codes=[CODE], date_from=datetime(2026, 8, 19),
                      date_to=datetime(2026, 8, 25), ownership={CODE: "own"}, report_dir=tmp_path)
    pipeline.run(engine, cfg, ext["scope_id"], provider=UniversalFake(), skip_synth=True)
    with engine.begin() as conn:
        conn.execute(insert(feeds).values(feed_id=2, code=CODE, posted_at=datetime(2026, 7, 20),
                                         feed_type=1, title="ETF", content="ETF", raw_json_broken=False,
                                         like_count=0, comment_count=1, image_count=0))
        conn.execute(insert(comments).values(comment_id=999, feed_id=2, content="ETF fee too high",
                                            author_name="reader", author_uid="old-reader"))
    extended = extract.run(engine, cfg, codes=[CODE], date_from=datetime(2026, 6, 27),
                           date_to=datetime(2026, 8, 25), ownership={CODE: "own"}, report_dir=tmp_path)
    monkeypatch.setattr(pipeline.annotate, "pending_count", lambda *args, **kwargs: 0)
    result = pipeline.run(engine, cfg, extended["scope_id"], provider=UniversalFake(), ranges=["d7", "d30"])
    assert result["complete"] is False
    with engine.connect() as conn:
        rows = conn.execute(select(synthesis_outputs)).mappings().all()
    assert rows
    assert {row["range_key"] for row in rows} == {"d7"}
