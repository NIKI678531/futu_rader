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
from radar_db.comment_filter import filter_readiness_values, load_comment_filter_config  # noqa: E402
from radar_db.revisions import synthesis_generation_key  # noqa: E402
from radar_db.schema import (  # noqa: E402
    annotation_runs,
    annotations,
    comments,
    feed_mentions,
    feeds,
    meta_kv,
    synthesis_outputs,
)

CODE = "3033"


# ── schema ──────────────────────────────────────────────────────────────


class TestSynthSchemas:
    def test_generation_fingerprint_tracks_model(self):
        original = config.load(model="first-model")
        replacement = config.load(model="second-model")
        assert synthesize.fingerprint("summary", [1], {}, original) != synthesize.fingerprint("summary", [1], {}, replacement)

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
        assert synth.parse(
            "topic_label",
            {"title": "恒科反弹", "summary": "长" * 89 + "。" + "短" * 89 + "。", "evidence_ids": ["c1"]},
            {"c1"},
        ).summary == "长" * 89 + "。" + "短" * 89 + "。"
        with pytest.raises(SchemaError, match="上限"):
            synth.parse(
                "topic_label",
                {"title": "恒科反弹", "summary": "长" * 90 + "。" + "短" * 89 + "。", "evidence_ids": ["c1"]},
                {"c1"},
            )

    def test_topic_summary_allows_one_grounded_sentence_for_one_evidence(self):
        parsed = synth.parse(
            "topic_label",
            {"title": "存储景气修复", "summary": "评论关注存储行业景气修复。", "evidence_ids": ["c1"]},
            {"c1"},
        )
        assert parsed.summary == "评论关注存储行业景气修复。"
        with pytest.raises(SchemaError, match="2–4 句"):
            synth.parse(
                "topic_label",
                {"title": "存储景气修复", "summary": "评论关注存储行业景气修复。", "evidence_ids": ["c1", "c2"]},
                {"c1", "c2"},
            )
        with pytest.raises(SchemaError, match="至少引用"):
            synth.parse(
                "topic_label",
                {"title": "存储景气修复", "summary": "评论关注存储行业景气修复。短线仍有分歧。", "evidence_ids": []},
                {"c1"},
            )
        with pytest.raises(SchemaError, match="泛化套话"):
            synth.parse(
                "topic_label",
                {"title": "市场方向", "summary": "评论预期市场回升。短线观点存在分歧。", "evidence_ids": ["c1"]},
                {"c1"},
            )
        with pytest.raises(SchemaError, match="泛化套话"):
            synth.parse(
                "topic_label",
                {"title": "市场方向", "summary": "市场关注度升温。整体情绪偏乐观。", "evidence_ids": ["c1"]},
                {"c1"},
            )

    def test_evidence_ids_must_be_subset_of_input(self):
        with pytest.raises(SchemaError, match="没有的证据"):
            synth.parse("hot_summary", {"text": "费率获认可", "evidence_ids": ["c999"], "needs_review": False}, {"c1"})
        with pytest.raises(SchemaError, match="至少引用"):
            synth.parse("hot_summary", {"text": "费率获认可", "evidence_ids": [], "needs_review": False}, {"c1"})

    def test_batch_keys_must_match(self):
        raw = {"results": [{"key": "positive|fee", "title": "费率同类最低", "summary": "多条评论把费率当持有理由。",
                            "key_points": [{"text": "管理费低于同类产品", "evidence_ids": ["c1"]}],
                            "evidence_ids": ["c1"]}]}
        synth.parse("theme_label", raw, {"c1"}, expected_keys=["positive|fee"])
        with pytest.raises(SchemaError, match="键集合"):
            synth.parse("theme_label", raw, {"c1"}, expected_keys=["positive|fee", "negative|spread"])

    def test_summary_is_capped_at_five_detailed_points(self):
        point = {"text": "评论明确提到管理费低于同类产品，因此更愿意长期持有。", "evidence_ids": ["c1"]}
        assert len(synth.parse("summary", {"points": [point] * 5, "needs_review": False}, {"c1"}).points) == 5
        with pytest.raises(SchemaError, match="1–5"):
            synth.parse("summary", {"points": [point] * 6, "needs_review": False}, {"c1"})

    def test_summary_prompt_is_experience_led_instead_of_polarity_led(self):
        prompt = synth.system_prompt("summary")
        assert synth.VERSION == "synth-v8"
        assert synth.VERSION == synthesize.core_topics.MARKET_OUTLOOK_SYNTH_VERSION
        assert "具体产品体验／使用场景／讨论现象" in prompt
        assert "不得按「积极观点／消极观点」分组" in prompt

    def test_topic_prompt_returns_a_standalone_market_outlook(self):
        prompt = synth.system_prompt("topic_label")
        assert "2–4 句、不超过 180 字" in prompt
        assert "市场观点" in prompt
        assert "不能把群体观点写成系统自己的预测" in prompt
        assert "不评价 ETF 的费率" in prompt
        assert "具体市场对象与行情 → 驱动或制约因素 → 对相关行业／成分股的影响链路 → 短线取向与分歧" in prompt
        assert "产品态度" in prompt
        assert "押注" in prompt
        assert "每句至少保留一个证据原文中的具体锚点" in prompt
        assert "美光财报超预期、存储缺货、回购、景气修复、海力士补涨" in prompt
        assert "示例名词不是默认事实" in prompt
        assert "sampleSufficient=false" in prompt
        assert "只总结带 `market_direction` 的行情证据" in prompt

    def test_theme_rejects_generic_product_experience_title(self):
        raw = {"results": [{"key": "positive|other_product", "title": "产品整体体验获认可",
                            "summary": "评论认可该产品。",
                            "key_points": [{"text": "具体体验未说明", "evidence_ids": ["c1"]}],
                            "evidence_ids": ["c1"]}]}
        with pytest.raises(SchemaError, match="具体产品体验"):
            synth.parse("theme_label", raw, {"c1"}, expected_keys=["positive|other_product"])

    def test_stage_unit_category_enum(self):
        raw = {"results": [{"key": "2026-08-20", "category": "made_up", "digest": "x", "evidence_ids": [], "needs_review": False}]}
        with pytest.raises(SchemaError):
            synth.parse("stage_unit", raw, set(), expected_keys=["2026-08-20"])

    def test_wire_schema_is_strict(self):
        for kind in synth.KINDS:
            sch = synth.json_schema(kind)
            assert sch["additionalProperties"] is False
            assert set(sch["required"]) == set(sch["properties"])

    def test_wire_schema_preserves_business_title_fields(self):
        topic = synth.json_schema("topic_label")
        bucket = synth.json_schema("theme_label")["$defs"]["BucketLabel"]
        for schema in (topic, bucket):
            assert "title" in schema["properties"]
            assert "title" in schema["required"]
            assert schema["properties"]["title"]["type"] == "string"
            assert "title" not in schema["properties"]["title"]


# ── job ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "s.db").as_posix())
    create_all(eng)
    now = datetime(2026, 8, 26, 12)
    with eng.begin() as conn:
        conn.execute(insert(meta_kv), [
            {"k": "anchor", "v": "2026-08-25"},
            *(
                {"k": key, "v": value}
                for key, value in filter_readiness_values(
                    load_comment_filter_config()
                ).items()
            ),
        ])
        conn.execute(insert(feeds).values(
            # UTC-naive 08-21 16:00 is HKT midnight at the start of 08-22.
            feed_id=1, code=CODE, source_ticker="03033.HK",
            posted_at=datetime(2026, 8, 21, 16), feed_type=1, title="t", content="c",
            like_count=0, comment_count=0, image_count=0, raw_json_broken=False))
        conn.execute(insert(feed_mentions).values(
            feed_id=1, raw_ticker="03033.HK", market="HK", occurrences=1,
        ))
        rows, anns = [], []
        aid = 0
        # 12 条相关：8 正（费率）、4 负（点差）；旧的市场／合规结论不得穿透当前相关性。
        for i in range(14):
            cid = 100 + i
            text = f"费率係同類最低第{i}條" if i < 8 else (f"點差太大第{i}條" if i < 12 else "恒指要崩了")
            rows.append({"comment_id": cid, "feed_id": 1, "content": text, "author_uid": f"u{i}"})
            rel = "relevant" if i < 12 else "irrelevant"
            for kind, value in (("relevance", rel),
                                ("attitude", "positive" if i < 8 else "negative" if i < 12 else None),
                                ("aspect", ["fee"] if i < 8 else ["spread"] if i < 12 else None),
                                ("market_direction", "bearish" if i in (10, 12, 13) else None),
                                ("compliance", {"tags": ["mobilization"], "rationale": "号召"}
                                 if i in (11, 12) else {"tags": [], "rationale": None})):
                if value is None:
                    continue
                aid += 1
                anns.append({"annotation_id": aid, "target_type": "comment", "target_id": cid, "subject_code": CODE,
                             "kind": kind, "value_json": json.dumps(value, ensure_ascii=False), "run_id": "r1",
                             "input_hash": f"h{cid}", "review_state": "pending", "created_at": now})
        conn.execute(insert(comments), rows)
        conn.execute(insert(annotations), anns)
        conn.execute(insert(annotation_runs).values(
            run_id="r1", task="comment_product", provider="openai_compatible",
            model_id="test", prompt_version="comment-product-v3",
            taxonomy_version="v2", schema_version="v2", started_at=now,
            finished_at=now, status="done", input_count=14, success_count=14,
            error_count=0,
        ))
    return eng


def test_today_anchor_is_observed_and_never_joins_default_batch_ranges(engine):
    with engine.begin() as conn:
        conn.execute(insert(meta_kv).values(k="data_max_ts", v="2026-08-26 09:00:00"))

    assert "d0" not in synthesize.DEFAULT_RANGES
    assert synthesize.read_anchor(engine, "d0").isoformat() == "2026-08-26"
    assert synthesize.resolve_anchor(engine, ["d0"]).isoformat() == "2026-08-26"
    with pytest.raises(ValueError, match="single range"):
        synthesize.resolve_anchor(engine, ["d0", "d1"])


def test_missing_generations_treats_dirty_outputs_as_recovery_work(engine):
    with engine.begin() as conn:
        conn.execute(insert(meta_kv), [
            {
                "k": synthesis_generation_key(CODE, "d7", kind),
                "v": "2026-08-25|fingerprint",
            }
            for kind in synthesize.KINDS
        ])

    assert synthesize.missing_generations(engine, [CODE], "d7", "2026-08-25") == []

    with engine.begin() as conn:
        conn.execute(insert(meta_kv).values(k=f"synth_dirty_{CODE}_d7", v="1"))

    assert synthesize.missing_generations(engine, [CODE], "d7", "2026-08-25") == [
        {"code": CODE, "kinds": list(synthesize.KINDS)}
    ]


def test_topic_fallback_is_grounded_and_marked_for_review():
    value, evidence_ids, review = synthesize.Synthesizer._topic_fallback(
        {"code": "7747", "name": "三星電子每日槓桿產品"},
        [
            {"id": "c1", "text": "三星電子公布新一代記憶體產品"},
            {"id": "c2", "text": "評論關注記憶體需求與價格走勢"},
            {"id": "c2", "text": "重複證據不應再次引用"},
        ],
    )

    assert value["title"] == "三星電子每日槓桿產品动态"
    assert value["summary"] == (
        "原评论提到：三星電子公布新一代記憶體產品。"
        "原评论提到：評論關注記憶體需求與價格走勢。"
    )
    assert value["evidence_ids"] == evidence_ids == ["c1", "c2"]
    assert review == "needs_review"


def test_7709_topic_payload_keeps_specific_comment_context_instead_of_short_direction_quote():
    class TopicMaterial:
        code = "7709"
        rng = {
            "from": "2026-08-25",
            "gran": "day",
            "buckets": [
                {"label": "08-25", "tip": "08-25（周二）全天"},
                {"label": "08-26", "tip": "08-26（周三）全天"},
            ],
        }
        units = []
        opinion_units = []
        pos = neg = neu = 0
        opinion_pos = opinion_neg = opinion_neu = 0

        def __init__(self):
            specific = {
                20: "美光财报超预期，管理层谈到存储缺货与供应偏紧，评论据此关注海力士后续补涨。",
                21: "HBM订单与DRAM价格上修被视为存储景气修复的信号，但也有评论担心估值已经提前反映。",
                22: "公司回购预期叠加供应链库存去化，讨论认为可能改善半导体板块的短线风险偏好。",
            }
            generic = {cid: "继续看多，等上涨。" for cid in range(1, 9)}
            self._texts = {**generic, **specific}
            self._quotes = {cid + 100: "看涨" for cid in self._texts}
            self.market_units = [
                {
                    "comment_id": cid,
                    "market_direction": "bullish",
                    "posted_at": datetime(2026, 8, 25 + (cid % 2), 12),
                    "annotation_id": cid + 100,
                }
                for cid in self._texts
            ]

        def texts(self):
            return self._texts

        def quotes(self):
            return self._quotes

    facts, evidence, allowed = synthesize.build_topic_payload(TopicMaterial())
    market_evidence = [item for item in evidence if item.get("market_direction")]
    texts = [item["text"] for item in market_evidence]

    assert facts["topic"] == {"bullish": 11, "bearish": 0, "neutral": 0, "mentions": 11}
    assert any("美光财报超预期" in text and "海力士后续补涨" in text for text in texts)
    assert any("HBM订单" in text and "DRAM价格上修" in text for text in texts)
    assert sum(text == "继续看多，等上涨。" for text in texts) == 1
    assert all(item.get("direction_quote") == "看涨" for item in market_evidence)
    assert allowed == {item["id"] for item in evidence}


def test_topic_opinion_includes_trading_intent_while_p9_themes_stay_strict(engine):
    """The market card's opinion facts match the left column; P9 stays product-feedback only."""
    with engine.begin() as conn:
        conn.execute(annotations.update().values(review_state="rejected"))
        conn.execute(insert(comments), [
            {
                "comment_id": 200,
                "feed_id": 1,
                "content": "财报后准备买入并加仓，继续看好指数反弹。",
                "author_uid": "trade-positive",
            },
            {
                "comment_id": 201,
                "feed_id": 1,
                "content": "点差太大所以减仓，短线看空。",
                "author_uid": "feedback-negative-1",
            },
            {
                "comment_id": 202,
                "feed_id": 1,
                "content": "盘口价差吃掉收益，暂时不想持有。",
                "author_uid": "feedback-negative-2",
            },
        ])
        rows_to_insert = []
        annotation_id = 1000
        values = {
            200: {
                "relevance": "relevant",
                "attitude": "positive",
                "aspect": ["trading_intent"],
                "feedback_eligible": False,
                "market_direction": "bullish",
            },
            201: {
                "relevance": "relevant",
                "attitude": "negative",
                "aspect": ["spread", "trading_intent"],
                "feedback_eligible": True,
                "market_direction": "bearish",
            },
            202: {
                "relevance": "relevant",
                "attitude": "negative",
                "aspect": ["spread"],
                "feedback_eligible": True,
                "market_direction": "bearish",
            },
        }
        for comment_id, by_kind in values.items():
            for kind, value in by_kind.items():
                annotation_id += 1
                rows_to_insert.append({
                    "annotation_id": annotation_id,
                    "target_type": "comment",
                    "target_id": comment_id,
                    "subject_code": CODE,
                    "kind": kind,
                    "value_json": json.dumps(value, ensure_ascii=False),
                    "run_id": "r1",
                    "input_hash": f"opinion-{comment_id}-{kind}",
                    "review_state": "pending",
                    "created_at": datetime(2026, 8, 26, 13),
                })
        conn.execute(insert(annotations), rows_to_insert)

    rng = synthesize.build_range("d7", synthesize.read_anchor(engine))
    mat = synthesize.Material(
        engine,
        CODE,
        rng,
        object(),
        load_comment_filter_config(),
    )

    assert {unit["comment_id"] for unit in mat.opinion_units} == {200, 201, 202}
    assert {unit["comment_id"] for unit in mat.units} == {201, 202}
    assert (mat.opinion_pos, mat.opinion_neg, mat.opinion_neu) == (1, 2, 0)
    assert (mat.pos, mat.neg, mat.neu) == (0, 2, 0)

    topic_facts, topic_evidence, _allowed = synthesize.build_topic_payload(mat)
    assert topic_facts["productAttitude"] == {
        "positive": 1,
        "negative": 2,
        "neutral": 0,
        "sampleSufficient": False,
    }
    opinion_evidence = {
        item["id"] for item in topic_evidence if item.get("product_attitude")
    }
    assert opinion_evidence == {"c200", "c201", "c202"}

    theme_facts, theme_evidence, _ids, keys = synthesize.build_theme_payload(mat, "theme_label")
    assert keys == ["negative|spread"]
    assert theme_facts["buckets"][0]["mentions"] == 1
    assert theme_facts["buckets"][0]["comment_count"] == 2
    assert set(theme_facts["buckets"][0]["evidence_ids"]) == {"f1", "c202"}
    assert {item["id"] for item in theme_evidence} == {"f1", "c202"}

    summary_facts, _summary_evidence, _summary_ids = synthesize.build_summary_payload(
        mat,
        {"theme_label": {}, "neg_category": {}},
    )
    assert summary_facts["attitude"] == {
        "positive": 0,
        "negative": 2,
        "neutral": 0,
        "relevant": 2,
    }


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
                                 "summary": "多條評論如此表述。",
                                 "key_points": [{"text": "評論點出具體產品體驗", "evidence_ids": b["evidence_ids"][:1]}],
                                 "evidence_ids": b["evidence_ids"][:1]} for b in f["buckets"]]}
        elif kind == "stage_unit":
            data = {"results": [{"key": u["key"], "category": "add_opportunity", "digest": "討論以加倉為主",
                                 "evidence_ids": u["evidence_ids"][:1], "needs_review": False} for u in f["units"]]}
        elif kind == "stage_summary":
            data = {"results": [{"key": s["key"], "summary": "連續看多", "evidence_ids": s["evidence_ids"][:1]} for s in f["stages"]]}
        elif kind == "topic_label":
            data = {
                "title": "恒指方向",
                "summary": "評論把恒指回落與風險偏好轉弱相聯繫。部分評論等待恒指成交量確認後再調整倉位。",
                "evidence_ids": ids[:2],
            }
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
    topic = [p for k, p in prov.calls if k == "topic_label"][0]
    assert topic["facts"]["topic"] == {
        "bullish": 0, "bearish": 3, "neutral": 0, "mentions": 3,
    }
    assert topic["facts"]["productAttitude"] == {
        "positive": 8, "negative": 4, "neutral": 0, "sampleSufficient": True,
    }
    market_evidence = [item for item in topic["evidence"] if item.get("market_direction")]
    assert {item["id"] for item in market_evidence} == {"c110", "c113"}
    assert {item.get("product_attitude") for item in topic["evidence"]} >= {"positive", "negative"}
    assert all(e["id"].startswith("c") for e in hot["evidence"])
    assert hot["language"] in ("zh-Hant", "yue")
    assert hot["facts"]["themes"]["positive"][0]["title"] == "費率同類最低"
    run = rows(engine, annotation_runs, annotation_runs.c.task == "synthesize")[0]
    assert run["task"] == "synthesize" and run["token_input"] == 100 * stats["calls"]


def test_incomplete_batch_is_repaired_instead_of_skipped(engine, cfg):
    from sqlalchemy import delete
    synthesize.run(engine, cfg, codes=[CODE], ranges=["d7"], kinds=["theme_label"], provider=FakeSynthProvider())
    with engine.begin() as conn:
        conn.execute(delete(synthesis_outputs).where(synthesis_outputs.c.subkey == "negative|spread"))
    provider = FakeSynthProvider()
    result = synthesize.run(engine, cfg, codes=[CODE], ranges=["d7"], kinds=["theme_label"], provider=provider)
    assert result["errors"] == 0 and provider.calls
    assert {row["subkey"] for row in rows(engine, synthesis_outputs)} == {"positive|fee", "negative|spread"}


def test_rerun_with_same_annotations_costs_nothing(engine, cfg):
    prov = FakeSynthProvider()
    synthesize.run(engine, cfg, codes=[CODE], ranges=["d7"], provider=prov)
    n = len(prov.calls)
    prov2 = FakeSynthProvider()
    stats = synthesize.run(engine, cfg, codes=[CODE], ranges=["d7"], provider=prov2)
    assert prov2.calls == [] and stats["skipped_same"] >= 1 and n > 0


def test_no_material_publishes_empty_generation_instead_of_reusing_old_output(engine, cfg):
    synthesize.run(
        engine, cfg, codes=[CODE], ranges=["d7"], kinds=["hot_summary"],
        provider=FakeSynthProvider(),
    )
    with engine.connect() as conn:
        before = conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == f"synth_current_{CODE}_d7_hot_summary"
        )).scalar_one()
    with engine.begin() as conn:
        conn.execute(
            annotations.update()
            .where(annotations.c.kind == "relevance")
            .values(review_state="rejected")
        )

    provider = FakeSynthProvider()
    stats = synthesize.run(
        engine, cfg, codes=[CODE], ranges=["d7"], kinds=["hot_summary"],
        provider=provider,
    )
    with engine.connect() as conn:
        after = conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == f"synth_current_{CODE}_d7_hot_summary"
        )).scalar_one()
        current_fp = after.split("|", 1)[1]
        current_rows = conn.execute(select(synthesis_outputs.c.synthesis_id).where(
            synthesis_outputs.c.code == CODE,
            synthesis_outputs.c.range_key == "d7",
            synthesis_outputs.c.kind == "hot_summary",
            synthesis_outputs.c.input_fingerprint == current_fp,
        )).all()
    assert stats["no_material"] == 1
    assert provider.calls == []
    assert after != before
    assert current_rows == []


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


def test_low_sample_generates_one_evidenced_hot_summary_but_not_an_overall_summary(engine, cfg):
    # 把 8 条正面里的 6 条改成 rejected ⇒ 有效态度 2+4=6 < 10
    with engine.begin() as conn:
        for cid in range(100, 106):
            conn.execute(annotations.update().where(annotations.c.kind == "attitude", annotations.c.target_id == cid)
                         .values(review_state="rejected"))
    prov = FakeSynthProvider()
    stats = synthesize.run(engine, cfg, codes=[CODE], ranges=["d7"], provider=prov,
                           kinds=["hot_summary", "summary"])
    assert stats["low_sample"] == 1
    assert [kind for kind, _payload in prov.calls] == ["hot_summary"]
    hot_payload = prov.calls[0][1]
    assert hot_payload["facts"]["sampleSufficient"] is False
    assert hot_payload["evidence"]
    hot = rows(engine, synthesis_outputs, synthesis_outputs.c.kind == "hot_summary")[0]
    assert json.loads(hot["value_json"])["text"] == "費率獲認可，傾向長期持有"
    summary = rows(engine, synthesis_outputs, synthesis_outputs.c.kind == "summary")[0]
    assert json.loads(summary["value_json"]) == {"status": "low_sample", "sample": 6}


def test_low_sample_hot_summary_falls_back_to_a_real_evidence_quote(engine, cfg):
    with engine.begin() as conn:
        # Keep one neutral product-feedback row.  Theme grouping deliberately
        # excludes neutral rows, but the home-page observation must still have
        # one real comment to cite.
        conn.execute(
            annotations.update()
            .where(annotations.c.kind == "attitude", annotations.c.target_id == 100)
            .values(value_json='"neutral"')
        )
        conn.execute(
            annotations.update()
            .where(
                annotations.c.kind == "attitude",
                annotations.c.target_id.in_(range(101, 112)),
            )
            .values(review_state="rejected")
        )

    class MissingEvidenceProvider(FakeSynthProvider):
        def complete_json(self, system, user, schema, schema_name):
            self.calls.append((schema_name, json.loads(user.split("\n\n", 1)[1])))
            return Completion(
                data={"text": "暂无可引用证据", "evidence_ids": [], "needs_review": True},
                model="m-snap",
                usage=Usage(100, 40, 5, 0),
                raw_text="",
                response_id="x",
            )

    stats = synthesize.run(
        engine,
        cfg,
        codes=[CODE],
        ranges=["d7"],
        provider=MissingEvidenceProvider(),
        kinds=["hot_summary"],
    )

    hot = rows(engine, synthesis_outputs, synthesis_outputs.c.kind == "hot_summary")[0]
    value = json.loads(hot["value_json"])
    evidence_ids = json.loads(hot["evidence_ids_json"])
    assert stats["errors"] == 0
    assert value["text"].startswith("有评论提到")
    assert len(value["text"]) <= 30
    assert evidence_ids and value["evidence_ids"] == evidence_ids
    assert value["needs_review"] is True


def test_low_sample_hot_summary_can_quote_relevant_non_feedback_comment(engine, cfg):
    with engine.begin() as conn:
        conn.execute(
            annotations.update()
            .where(annotations.c.kind == "attitude")
            .values(review_state="rejected")
        )
        conn.execute(
            annotations.update()
            .where(annotations.c.kind == "attitude", annotations.c.target_id == 100)
            .values(review_state="pending")
        )
        conn.execute(
            annotations.update()
            .where(annotations.c.kind == "aspect", annotations.c.target_id == 100)
            .values(value_json='["performance"]')
        )

    class MissingEvidenceProvider(FakeSynthProvider):
        def complete_json(self, system, user, schema, schema_name):
            payload = json.loads(user.split("\n\n", 1)[1])
            self.calls.append((schema_name, payload))
            return Completion(
                data={"text": "暂无可引用证据", "evidence_ids": [], "needs_review": True},
                model="m-snap",
                usage=Usage(100, 40, 5, 0),
                raw_text="",
                response_id="x",
            )

    prov = MissingEvidenceProvider()
    stats = synthesize.run(
        engine,
        cfg,
        codes=[CODE],
        ranges=["d7"],
        provider=prov,
        kinds=["hot_summary"],
    )

    hot = rows(engine, synthesis_outputs, synthesis_outputs.c.kind == "hot_summary")[0]
    value = json.loads(hot["value_json"])
    assert stats["errors"] == 0
    assert value["text"].startswith("有评论提到")
    assert json.loads(hot["evidence_ids_json"])


def test_dry_run_calls_nothing_and_writes_nothing(engine, cfg):
    stats = synthesize.run(engine, cfg, codes=[CODE], ranges=["d7"], dry_run=True)
    assert rows(engine, synthesis_outputs) == [] and stats["calls"] == 0


def test_no_annotations_means_no_material_no_calls(engine, cfg):
    prov = FakeSynthProvider()
    stats = synthesize.run(engine, cfg, codes=["7226"], ranges=["d7"], provider=prov)
    assert stats["no_material"] == 1 and prov.calls == []


def test_synthesis_excludes_unqualified_and_cross_product_annotations(engine, cfg):
    now = datetime(2026, 8, 26, 12)
    with engine.begin() as conn:
        conn.execute(insert(feeds).values(
            feed_id=2,
            code=CODE,
            source_ticker="03033.HK",
            posted_at=datetime(2026, 8, 21, 16),
            feed_type=1,
            title="t",
            content="$800000.HK$",
            like_count=0,
            comment_count=1,
            image_count=0,
            raw_json_broken=False,
        ))
        conn.execute(insert(feed_mentions).values(
            feed_id=2, raw_ticker="800000.HK", market="HK", occurrences=1,
        ))
        conn.execute(insert(comments).values(
            comment_id=999, feed_id=2, content="must not reach synthesis", author_uid="u999",
        ))
        conn.execute(insert(annotations), [
            {
                "target_type": "comment",
                "target_id": 999,
                "subject_code": CODE,
                "kind": kind,
                "value_json": json.dumps(value),
                "run_id": "legacy",
                "input_hash": "legacy",
                "review_state": "pending",
                "created_at": now,
            }
            for kind, value in (("relevance", "relevant"), ("attitude", "positive"))
        ])
        conn.execute(insert(annotations), [
            {
                "target_type": "comment",
                "target_id": 100,
                "subject_code": "7226",
                "kind": kind,
                "value_json": json.dumps(value),
                "run_id": "legacy-cross-route",
                "input_hash": "legacy-cross-route",
                "review_state": "pending",
                "created_at": now,
            }
            for kind, value in (("relevance", "relevant"), ("attitude", "positive"))
        ])

    material = synthesize.Material(
        engine,
        CODE,
        synthesize.build_range("d7", datetime(2026, 8, 25).date()),
        synthesize.product_aliases.ProductLexicon(),
        load_comment_filter_config(),
    )
    assert material.relevant_count == 12
    assert len(material.units) == 12


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
