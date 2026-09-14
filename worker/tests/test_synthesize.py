"""`ai/synth.py` 与 `jobs/synthesize.py` —— Layer B：模型只写字，数在 core；指纹幂等；样本不足不调模型。"""

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

from ai import config, synth  # noqa: E402
from ai.providers.base import Completion, PermanentError, Usage  # noqa: E402
from ai.schemas import SchemaError  # noqa: E402
from jobs import synthesize  # noqa: E402
from radar_db import create_all, make_engine  # noqa: E402
from radar_db.schema import annotation_runs, annotations, comments, feeds, meta_kv, synthesis_outputs  # noqa: E402

CODE = "3033"


# ── schema ──────────────────────────────────────────────────────────────


class TestSynthSchemas:
    def test_ratio_words_are_rejected_everywhere(self):
        with pytest.raises(SchemaError, match="比例"):
            synth.parse("hot_summary", {"text": "六成用户看好费率", "evidence_ids": ["c1"], "needs_review": False}, {"c1"})
        with pytest.raises(SchemaError, match="比例"):
            synth.parse("topic_label", {"title": "恒科反弹", "summary": "看多占比过半", "evidence_ids": ["c1"]}, {"c1"})

    def test_length_limits(self):
        with pytest.raises(SchemaError, match="上限"):
            synth.parse("hot_summary", {"text": "长" * 31, "evidence_ids": ["c1"], "needs_review": False}, {"c1"})
        ok = synth.parse("hot_summary", {"text": "分派稳定获认可，倾向继续持有", "evidence_ids": ["c1"], "needs_review": False}, {"c1"})
        assert ok.text.startswith("分派")

    def test_evidence_ids_must_be_subset_of_input(self):
        with pytest.raises(SchemaError, match="没有的证据"):
            synth.parse("hot_summary", {"text": "费率获认可", "evidence_ids": ["c999"], "needs_review": False}, {"c1"})
        with pytest.raises(SchemaError, match="至少引用"):
            synth.parse("hot_summary", {"text": "费率获认可", "evidence_ids": [], "needs_review": False}, {"c1"})

    def test_batch_keys_must_match(self):
        raw = {"results": [{"key": "positive|fee", "title": "费率同类最低", "summary": "多条评论把费率当持有理由。", "evidence_ids": ["c1"]}]}
        synth.parse("theme_label", raw, {"c1"}, expected_keys=["positive|fee"])
        with pytest.raises(SchemaError, match="键集合"):
            synth.parse("theme_label", raw, {"c1"}, expected_keys=["positive|fee", "negative|spread"])

    def test_stage_unit_category_enum(self):
        raw = {"results": [{"key": "2026-08-20", "category": "made_up", "digest": "x", "evidence_ids": [], "needs_review": False}]}
        with pytest.raises(SchemaError):
            synth.parse("stage_unit", raw, set(), expected_keys=["2026-08-20"])

    def test_wire_schema_is_strict(self):
        for kind in synth.KINDS:
            sch = synth.json_schema(kind)
            assert sch["additionalProperties"] is False
            assert set(sch["required"]) == set(sch["properties"])


# ── job ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "s.db").as_posix())
    create_all(eng)
    now = datetime(2026, 8, 26, 12)
    with eng.begin() as conn:
        conn.execute(insert(meta_kv).values(k="anchor", v="2026-08-25"))
        conn.execute(insert(feeds).values(
            feed_id=1, code=CODE, posted_at=datetime(2026, 8, 22, 10), feed_type=1, title="t", content="c",
            like_count=0, comment_count=0, image_count=0, raw_json_broken=False))
        rows, anns = [], []
        aid = 0
        # 12 条相关：8 正（费率）、4 负（点差）；2 条无关但有市场方向；1 条合规命中
        for i in range(14):
            cid = 100 + i
            text = f"费率係同類最低第{i}條" if i < 8 else (f"點差太大第{i}條" if i < 12 else "恒指要崩了")
            rows.append({"comment_id": cid, "feed_id": 1, "content": text, "author_uid": f"u{i}"})
            rel = "relevant" if i < 12 else "irrelevant"
            for kind, value in (("relevance", rel),
                                ("attitude", "positive" if i < 8 else "negative" if i < 12 else None),
                                ("aspect", ["fee"] if i < 8 else ["spread"] if i < 12 else None),
                                ("market_direction", "bearish" if i >= 12 else None),
                                ("compliance", {"tags": ["mobilization"], "rationale": "号召"} if i == 11 else {"tags": [], "rationale": None})):
                if value is None:
                    continue
                aid += 1
                anns.append({"annotation_id": aid, "target_type": "comment", "target_id": cid, "subject_code": CODE,
                             "kind": kind, "value_json": json.dumps(value, ensure_ascii=False), "run_id": "r1",
                             "input_hash": f"h{cid}", "review_state": "pending", "created_at": now})
        conn.execute(insert(comments), rows)
        conn.execute(insert(annotations), anns)
    return eng


@pytest.fixture()
def cfg():
    return config.load(model="m", prompt_version="comment-product-v2", schema_version="v2", taxonomy_version="v2")


class FakeSynthProvider:
    """按 kind 生成一份合规输出，证据 id 从 payload 里抄。"""

    def __init__(self, fail_kinds=()):
        self.calls = []
        self.fail_kinds = set(fail_kinds)

    def complete_json(self, system, user, schema, schema_name):
        kind = schema_name.replace("synth_", "")
        payload = json.loads(user.split("\n\n", 1)[1])
        ids = [e["id"] for e in payload["evidence"]]
        self.calls.append((kind, payload))
        if kind in self.fail_kinds:
            raise PermanentError("HTTP 401")
        f = payload["facts"]
        if kind == "hot_summary":
            data = {"text": "費率獲認可，傾向長期持有", "evidence_ids": ids[:2], "needs_review": False}
        elif kind == "summary":
            data = {"points": [{"text": "多條評論認可費率", "evidence_ids": ids[:2]}], "needs_review": False}
        elif kind in ("theme_label", "neg_category"):
            data = {"results": [{"key": b["key"], "title": "費率同類最低" if "fee" in b["key"] else "點差過大",
                                 "summary": "多條評論如此表述。", "evidence_ids": b["evidence_ids"][:1]} for b in f["buckets"]]}
        elif kind == "stage_unit":
            data = {"results": [{"key": u["key"], "category": "add_opportunity", "digest": "討論以加倉為主",
                                 "evidence_ids": u["evidence_ids"][:1], "needs_review": False} for u in f["units"]]}
        elif kind == "stage_summary":
            data = {"results": [{"key": s["key"], "summary": "連續看多", "evidence_ids": s["evidence_ids"][:1]} for s in f["stages"]]}
        elif kind == "topic_label":
            data = {"title": "恒指方向", "summary": "多條評論看空大市", "evidence_ids": ids[:1]}
        else:
            data = {"results": [{"code": c["code"], "like_reasons": [], "dislike_reasons": [], "evidence_ids": [], "needs_review": True}
                                for c in f["competitors"]]}
        return Completion(data=data, model="m-snap", usage=Usage(100, 40, 5, 0), raw_text="", response_id="x")


def rows(engine, table, *where):
    with engine.connect() as conn:
        return conn.execute(select(table).where(*where)).mappings().all()


def test_full_run_writes_each_kind_with_facts_only_from_core(engine, cfg):
    prov = FakeSynthProvider()
    stats = synthesize.run(engine, cfg, codes=[CODE], ranges=["d7"], provider=prov)
    kinds = {(r["kind"], r["subkey"]) for r in rows(engine, synthesis_outputs)}
    assert ("hot_summary", "") in kinds and ("summary", "") in kinds
    assert ("theme_label", "positive|fee") in kinds and ("theme_label", "negative|spread") in kinds
    assert ("neg_category", "spread") in kinds
    assert ("topic_label", "market") in kinds
    assert stats["errors"] == 0 and stats["written"] >= 5
    # 发给模型的事实里只有计数，没有比例；证据是我们的 id。
    hot = [p for k, p in prov.calls if k == "hot_summary"][0]
    assert hot["facts"]["attitude"] == {"positive": 8, "negative": 4, "neutral": 0, "relevant": 12}
    assert hot["facts"]["compliance"]["hits"] == 1
    assert all(e["id"].startswith("c") for e in hot["evidence"])
    assert hot["language"] in ("zh-Hant", "yue")
    # 热议总结拿到的是刚起好的主题名，不是 aspect 固定名。
    assert hot["facts"]["themes"]["positive"][0]["title"] == "費率同類最低"
    run = rows(engine, annotation_runs)[0]
    assert run["task"] == "synthesize" and run["token_input"] == 100 * stats["calls"]


def test_rerun_with_same_annotations_costs_nothing(engine, cfg):
    prov = FakeSynthProvider()
    synthesize.run(engine, cfg, codes=[CODE], ranges=["d7"], provider=prov)
    n = len(prov.calls)
    prov2 = FakeSynthProvider()
    stats = synthesize.run(engine, cfg, codes=[CODE], ranges=["d7"], provider=prov2)
    assert prov2.calls == [] and stats["skipped_same"] >= 1 and n > 0


def test_changed_annotation_changes_fingerprint_and_supersedes(engine, cfg):
    synthesize.run(engine, cfg, codes=[CODE], ranges=["d7"], provider=FakeSynthProvider())
    first = [r for r in rows(engine, synthesis_outputs, synthesis_outputs.c.kind == "hot_summary")][0]
    # 给一条评论换态度（新行 supersede 旧行）⇒ 指纹变 ⇒ 重生成并 supersede 旧生成物。
    with engine.begin() as conn:
        old = conn.execute(select(annotations).where(annotations.c.kind == "attitude", annotations.c.target_id == 100)).mappings().one()
        conn.execute(insert(annotations).values(
            target_type="comment", target_id=100, subject_code=CODE, kind="attitude", value_json='"negative"',
            run_id="r2", input_hash="h100b", review_state="pending", created_at=datetime(2026, 8, 27),
            supersedes_id=old["annotation_id"]))
    prov = FakeSynthProvider()
    synthesize.run(engine, cfg, codes=[CODE], ranges=["d7"], provider=prov)
    hots = rows(engine, synthesis_outputs, synthesis_outputs.c.kind == "hot_summary")
    assert len(hots) == 2
    new = [h for h in hots if h["synthesis_id"] != first["synthesis_id"]][0]
    assert new["supersedes_id"] == first["synthesis_id"]
    assert new["input_fingerprint"] != first["input_fingerprint"]


def test_low_sample_writes_status_without_calling_model(engine, cfg):
    # 把 8 条正面里的 6 条改成 rejected ⇒ 有效态度 2+4=6 < 10
    with engine.begin() as conn:
        for cid in range(100, 106):
            conn.execute(annotations.update().where(annotations.c.kind == "attitude", annotations.c.target_id == cid)
                         .values(review_state="rejected"))
    prov = FakeSynthProvider()
    stats = synthesize.run(engine, cfg, codes=[CODE], ranges=["d7"], provider=prov,
                           kinds=["hot_summary", "summary"])
    assert stats["low_sample"] == 2 and prov.calls == []
    hot = rows(engine, synthesis_outputs, synthesis_outputs.c.kind == "hot_summary")[0]
    assert json.loads(hot["value_json"]) == {"status": "low_sample", "sample": 6}


def test_dry_run_calls_nothing_and_writes_nothing(engine, cfg):
    stats = synthesize.run(engine, cfg, codes=[CODE], ranges=["d7"], dry_run=True)
    assert rows(engine, synthesis_outputs) == [] and stats["calls"] == 0


def test_no_annotations_means_no_material_no_calls(engine, cfg):
    prov = FakeSynthProvider()
    stats = synthesize.run(engine, cfg, codes=["7226"], ranges=["d7"], provider=prov)
    assert stats["no_material"] == 1 and prov.calls == []


def test_permanent_error_aborts(engine, cfg):
    with pytest.raises(PermanentError):
        synthesize.run(engine, cfg, codes=[CODE], ranges=["d7"], provider=FakeSynthProvider(fail_kinds={"theme_label"}))


def test_stage_units_use_annotation_counts_not_heat(engine, cfg):
    prov = FakeSynthProvider()
    synthesize.run(engine, cfg, codes=[CODE], ranges=["d7"], provider=prov, kinds=["stage_unit"])
    calls = [p for k, p in prov.calls if k == "stage_unit"]
    assert calls, "8-22 有 12 条有效态度 ≥ 10，该时段要判类别"
    unit = calls[0]["facts"]["units"][0]
    assert unit["key"] == "2026-08-22" and (unit["positive"], unit["negative"]) == (8, 4)
    st = rows(engine, synthesis_outputs, synthesis_outputs.c.kind == "stage_unit")
    assert [r["subkey"] for r in st] == ["2026-08-22"]
