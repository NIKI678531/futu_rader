"""Layer B 读路径（ADR-0020）：有标注时九个面板给真值；数来自 core，字来自 synthesis_outputs。

三态在每个面板上都要分得开：

- 没有一条态度标注 ⇒ 整块 None（`test_sql_provider.TestAiAndPriceSurfacesAreNone`）；
- 有标注、模型还没写字 ⇒ 数是真的，文字位是固定名／None，`labelStatus='unavailable'`；
- 生成过 ⇒ 模型的名字与句子进来，带 `reviewState` 与 `evidenceIds`。
"""

import json
from datetime import datetime

import pytest
from sqlalchemy import insert

from radar_db.schema import synthesis_outputs
from sql_fixture import (
    KOL_NAME, OWN_CODE, PEER_CODE, add_annotations, add_comments, add_evidence, make_sql_provider,
)


@pytest.fixture
def provider():
    return make_sql_provider()


def annotated(p, n_pos=8, n_neg=4, kol_n=3):
    """c11 负（点差）、c12 正（费率）＋ 补出足量样本：正 `n_pos`、负 `n_neg`，都挂在 f1（08-25 09:00）。"""
    rows = []
    add_comments(p, [{"comment_id": 100 + i, "content": f"补一条评论 {i}", "author_name": KOL_NAME if i < kol_n else "路人丙"}
                     for i in range(n_pos + n_neg)])
    aid = 1
    for i in range(n_pos + n_neg):
        cid = 100 + i
        att = "positive" if i < n_pos else "negative"
        asp = ["fee"] if i < n_pos else ["spread"]
        rows += [
            {"annotation_id": aid, "target_id": cid, "kind": "relevance", "value": "relevant"},
            {"annotation_id": aid + 1, "target_id": cid, "kind": "attitude", "value": att},
            {"annotation_id": aid + 2, "target_id": cid, "kind": "aspect", "value": asp},
        ]
        aid += 3
    # 两条无关但有市场方向
    rows += [
        {"annotation_id": aid, "target_id": 11, "kind": "relevance", "value": "irrelevant"},
        {"annotation_id": aid + 1, "target_id": 11, "kind": "market_direction", "value": "bearish"},
    ]
    return add_annotations(p, rows)


def add_synth(p, rows):
    payload = [
        {
            "code": r.get("code", OWN_CODE), "range_key": r.get("range_key", "d1"), "anchor": "2026-08-25",
            "kind": r["kind"], "subkey": r.get("subkey", ""), "input_fingerprint": r.get("fp", f"fp{i}"),
            "value_json": json.dumps(r["value"], ensure_ascii=False),
            "evidence_ids_json": json.dumps(r.get("evidence_ids", ["c100"])),
            "run_id": "synth-test", "review_state": r.get("review_state", "pending"),
            "created_at": datetime(2026, 8, 26, 10, i), "supersedes_id": r.get("supersedes_id"),
        }
        for i, r in enumerate(rows)
    ]
    with p._engine.begin() as conn:
        conn.execute(insert(synthesis_outputs), payload)
    p._cache.clear()
    return p


class TestThemesAndNegCats:
    def test_counts_come_from_annotations_labels_from_synth(self, provider):
        p = annotated(provider)
        th = p.themes_for(OWN_CODE, "d1")
        assert [t["aspect"] for t in th["positive"]] == ["fee"]
        assert th["positive"][0]["mentions"] == 8 and th["positive"][0]["labelStatus"] == "unavailable"
        assert th["positive"][0]["title"] == "费率与管理费"      # 固定名兜底
        assert th["positive"][0]["confidence"] is None
        assert th["negative"][0]["aspect"] == "spread" and th["negative"][0]["mentions"] == 4
        assert th["negative"][0]["hasMeta"] is True
        assert th["negative"][0]["lifecycleLabel"] is None
        assert th["positive"][0]["hasMeta"] is False
        # 基准期（08-24）没有态度标注 ⇒ 环比暂不可用，不是 0%。
        assert th["positive"][0]["delta"]["text"] == "数据暂不可用"

        add_synth(p, [{"kind": "theme_label", "subkey": "positive|fee",
                       "value": {"key": "positive|fee", "title": "费率同类最低", "summary": "多条评论把费率当持有理由。"},
                       "review_state": "pending"}])
        th = p.themes_for(OWN_CODE, "d1")
        t = th["positive"][0]
        assert t["title"] == "费率同类最低" and t["labelStatus"] == "ok" and t["evidenceIds"] == ["c100"]
        assert t["mentions"] == 8, "名字换了，数不变"

    def test_neg_categories_and_lifecycle_unknown_without_baseline(self, provider):
        p = annotated(provider)
        nc = p.neg_cats_for(OWN_CODE, "d1")
        assert [c["aspect"] for c in nc] == ["spread"]
        assert nc[0]["shareOfNegative"] == 100.0 and nc[0]["lifecycle"] is None
        assert nc[0]["severity"] == "high"

    def test_baseline_annotations_enable_delta_and_lifecycle(self, provider):
        p = annotated(provider)
        # c14 在 08-24（基准期），标成负面点差 ⇒ 基准 1 条。
        add_annotations(p, [
            {"annotation_id": 900, "target_id": 14, "kind": "relevance", "value": "relevant"},
            {"annotation_id": 901, "target_id": 14, "kind": "attitude", "value": "negative"},
            {"annotation_id": 902, "target_id": 14, "kind": "aspect", "value": ["spread"]},
        ])
        nc = p.neg_cats_for(OWN_CODE, "d1")[0]
        assert nc["lifecycle"] == "continuing" and nc["delta"]["abs"] == 3
        th = p.themes_for(OWN_CODE, "d1")
        assert th["positive"][0]["delta"]["short"] == "新增"   # 基准期 0 条积极


class TestPoolNegativeRollup:
    """`pool()` 的 `alerts`／`negMentions`／`own.neg`／`dNeg` 从负面类别派生（原来硬编码 None）。

    数只有一份口径：`core/themes.neg_categories`。`pool()` 整窗批量走一遍
    （`_neg_units_by_code` → `_neg_rollup`），产品监控页逐只走 `neg_cats_for` ——
    两条路对同一只产品必须给同一个数。
    """

    def test_neg_mentions_and_alerts_equal_the_neg_category_rows(self, provider):
        p = annotated(provider)  # 4 条消极全在 spread ⇒ 占比 100%、关注程度「高」
        pool = p.pool("d1")
        cats = p.neg_cats_for(OWN_CODE, "d1")
        assert pool["negMentions"][OWN_CODE] == 4 == sum(c["mentions"] for c in cats)
        assert pool["alerts"][OWN_CODE] == 1 == sum(1 for c in cats if c["severity"] == "high")

    def test_unannotated_products_stay_none_not_zero(self, provider):
        p = annotated(provider)
        pool = p.pool("d1")
        assert pool["negMentions"][PEER_CODE] is None and pool["alerts"][PEER_CODE] is None
        assert sum(v is not None for v in pool["negMentions"].values()) == 1, "只有 3033 标过"

    def test_annotated_without_any_negative_is_zero(self, provider):
        """标过态度、一条消极都没有 ⇒ 0（查过了确实为零），与「没标过」的 None 分开。"""
        p = annotated(provider)
        add_annotations(p, [
            {"annotation_id": 800, "target_id": 13, "subject_code": PEER_CODE, "kind": "attitude", "value": "neutral"},
            {"annotation_id": 801, "target_id": 13, "subject_code": PEER_CODE, "kind": "relevance", "value": "relevant"},
        ])
        pool = p.pool("d1")
        assert pool["negMentions"][PEER_CODE] == 0 and pool["alerts"][PEER_CODE] == 0
        assert p.neg_cats_for(PEER_CODE, "d1") == []

    def test_below_high_severity_is_not_an_alert(self, provider):
        """`alerts` 数的是关注程度为「高」的类别（设计源），不是「有没有消极」。"""
        p = annotated(provider, n_pos=8, n_neg=0)
        # 十条消极分散在五个类别，各占 20% —— 都够不上「高」（≥25%）。
        aspects = ["fee", "liquidity", "spread", "tracking", "dividend"]
        add_comments(p, [{"comment_id": 200 + i} for i in range(10)])
        rows = []
        for i in range(10):
            rows += [
                {"annotation_id": 700 + 3 * i, "target_id": 200 + i, "kind": "relevance", "value": "relevant"},
                {"annotation_id": 701 + 3 * i, "target_id": 200 + i, "kind": "attitude", "value": "negative"},
                {"annotation_id": 702 + 3 * i, "target_id": 200 + i, "kind": "aspect", "value": [aspects[i % 5]]},
            ]
        add_annotations(p, rows)
        pool = p.pool("d1")
        assert pool["negMentions"][OWN_CODE] == 10 and pool["alerts"][OWN_CODE] == 0
        assert all(c["severity"] == "medium" for c in p.neg_cats_for(OWN_CODE, "d1"))

    def test_own_total_is_unknown_while_any_own_product_is_unannotated(self, provider):
        """`own.neg` 是「已标注子集」的计数：61 只里有一只没标过，合计就是未知，不是把它当 0。"""
        p = annotated(provider)
        own = p.pool("d1")["own"]
        assert own["neg"] is None and own["dNeg"]["text"] == "数据暂不可用"

    def test_own_total_and_delta_once_every_own_product_is_annotated(self, provider):
        p = annotated(provider)
        own_codes = [x["code"] for x in p._products if x["ownership"] == "own"]
        rows, aid = [], 2000
        for code in own_codes:
            # 当期（c12 在 08-25）与基准期（c14 在 08-24）各给一条中性态度：标过、但不是消极。
            rows += [
                {"annotation_id": aid, "target_id": 12, "subject_code": code, "kind": "attitude", "value": "neutral"},
                {"annotation_id": aid + 1, "target_id": 14, "subject_code": code, "kind": "attitude", "value": "neutral"},
            ]
            aid += 2
        # 3033 基准期另有 1 条消极点差 ⇒ 基准 1、当期 4。
        rows += [
            {"annotation_id": aid, "target_id": 14, "kind": "relevance", "value": "relevant"},
            {"annotation_id": aid + 1, "target_id": 14, "kind": "attitude", "value": "negative"},
            {"annotation_id": aid + 2, "target_id": 14, "kind": "aspect", "value": ["spread"]},
        ]
        add_annotations(p, rows)
        own = p.pool("d1")["own"]
        assert own["neg"] == 4
        assert own["dNeg"]["abs"] == 3 and own["dNeg"]["text"] == "+3（+300.0%）"

    def test_base_unannotated_leaves_delta_unavailable_but_current_counts_given(self, provider):
        p = annotated(provider)
        pool = p.pool("d1")
        assert pool["negMentions"][OWN_CODE] == 4
        assert p._neg_rollup(p.build_range("d1"))[OWN_CODE]["base"] is None


class TestTopicsAndHot:
    def test_market_topic_counts_direction_not_attitude(self, provider):
        p = annotated(provider)
        tp = p.topics_for(OWN_CODE, "d1")
        assert len(tp) == 1 and tp[0]["negative"] == 1 and tp[0]["mentions"] == 1
        assert tp[0]["labelStatus"] == "unavailable"
        assert tp[0]["delta"]["short"] == "暂不可用"
        assert tp[0]["evidenceCount"] == 1
        assert isinstance(tp[0]["peak"], str) and isinstance(tp[0]["split"], str)
        add_synth(p, [{"kind": "topic_label", "subkey": "market",
                       "value": {"title": "恒指方向争论", "summary": "多条评论看空大市。"}}])
        assert p.topics_for(OWN_CODE, "d1")[0]["title"] == "恒指方向争论"

    def test_hot_summary_three_states(self, provider):
        p = annotated(provider)
        hs = p.hot_summaries("d1")[OWN_CODE]
        assert hs["status"] == "unavailable" and hs["sample"] == 12   # 标了 12 条、模型没写
        add_synth(p, [{"kind": "hot_summary", "value": {"text": "费率获认可，倾向长期持有", "needs_review": False}}])
        hs = p.hot_summaries("d1")[OWN_CODE]
        assert hs["status"] == "ok" and hs["tone"] == "pos" and hs["text"].startswith("费率")
        assert hs["reviewState"] == "pending"
        # 同业 3032：只有一条中性标注都没有 ⇒ unavailable；给它标一条中性 ⇒ low_sample
        add_annotations(p, [{"annotation_id": 800, "target_id": 13, "subject_code": PEER_CODE,
                             "kind": "attitude", "value": "neutral"},
                            {"annotation_id": 801, "target_id": 13, "subject_code": PEER_CODE,
                             "kind": "relevance", "value": "relevant"}])
        peer = p.hot_summaries("d1")[PEER_CODE]
        assert peer["status"] == "low_sample" and peer["text"] == "样本不足，暂无主流观点"

    def test_hot_summary_low_sample_row_does_not_override_counts(self, provider):
        p = annotated(provider, n_pos=3, n_neg=2)
        hs = p.hot_summaries("d1")[OWN_CODE]
        assert hs["status"] == "low_sample" and hs["sample"] == 5


class TestSummary:
    def test_dirty_source_keeps_the_old_points_and_marks_them_stale(self, provider):
        """脏标记不再让生成物消失：旧要点照发，`stale=True` 让页面挂「待更新」。

        原来脏了就整块 `unavailable`：每一批标注落库到下一次汇总跑完之间，页面上的总结
        会消失几分钟到几小时，而库里明明有一份昨天的结论。
        """
        p = annotated(provider)
        add_synth(p, [{"kind": "summary", "value": {"points": [
            {"text": "多条评论认可费率", "evidence_ids": ["c100"]}]} }])
        s = p.summary_for(OWN_CODE, "d1")
        assert s["aiStatus"] == "ok" and s["stale"] is False
        p._meta[f"synth_dirty_{OWN_CODE}_d1"] = "1"
        s = p.summary_for(OWN_CODE, "d1")
        assert s["aiStatus"] == "ok" and s["stale"] is True
        assert s["points"][0]["text"] == "多条评论认可费率。", "旧要点还在"
        assert "积极 8 条、消极 4 条" in s["text"], "计数句是刚扫的事实，不随脏标记变"

    def test_summary_text_is_facts_plus_model_points(self, provider):
        p = annotated(provider)
        s = p.summary_for(OWN_CODE, "d1")
        assert s["low"] is False and s["sample"] == 12 and s["aiStatus"] == "unavailable"
        assert "积极 8 条、消极 4 条" in s["text"] and "积极比消极多 4 条" in s["text"]
        add_synth(p, [{"kind": "summary", "value": {"points": [
            {"text": "多条评论认可费率", "evidence_ids": ["c100"]},
            {"text": "点差是主要抱怨", "evidence_ids": ["c108"]}], "needs_review": False}}])
        s = p.summary_for(OWN_CODE, "d1")
        assert s["aiStatus"] == "ok" and len(s["points"]) == 2
        assert s["text"].endswith("多条评论认可费率。点差是主要抱怨。")
        assert s["evidenceIds"] == ["c100", "c108"]

    def test_summary_low_sample_wording_is_verbatim(self, provider):
        p = annotated(provider, n_pos=3, n_neg=2)
        s = p.summary_for(OWN_CODE, "d1")
        assert s["low"] is True
        assert "有效态度提及为 5 条，低于 10 条的判定阈值，本区间不输出整体倾向结论" in s["text"]


class TestStale:
    """`synth_dirty_{code}_{range}=="1"` 时七个叙述面板照常给出现行生成物，并标 `stale=True`；
    不脏、或没有生成物可过期时 `stale=False`。"""

    @staticmethod
    def _dirty(p, on=True):
        p._meta[f"synth_dirty_{OWN_CODE}_d1"] = "1" if on else "0"

    def test_hot_summary_keeps_its_text_and_flags_stale(self, provider):
        p = annotated(provider)
        add_synth(p, [{"kind": "hot_summary", "value": {"text": "费率获认可，倾向长期持有", "needs_review": False}}])
        assert p.hot_summaries("d1")[OWN_CODE]["stale"] is False
        self._dirty(p)
        hs = p.hot_summaries("d1")[OWN_CODE]
        assert hs["status"] == "ok" and hs["text"].startswith("费率") and hs["stale"] is True
        # 没生成物的产品：unavailable，不是 stale —— 没有什么可过期的
        assert p.hot_summaries("d1")[PEER_CODE] == {
            "status": "unavailable", "text": "数据暂不可用", "sample": None, "ok": False, "stale": False,
        }

    def test_themes_carry_a_top_level_flag(self, provider):
        p = annotated(provider)
        add_synth(p, [{"kind": "theme_label", "subkey": "positive|fee",
                       "value": {"key": "positive|fee", "title": "费率同类最低", "summary": "…"}}])
        assert p.themes_for(OWN_CODE, "d1")["stale"] is False
        self._dirty(p)
        th = p.themes_for(OWN_CODE, "d1")
        assert th["stale"] is True
        assert th["positive"][0]["title"] == "费率同类最低" and th["positive"][0]["mentions"] == 8

    def test_neg_categories_and_topics_carry_the_flag_per_row(self, provider):
        p = annotated(provider)
        add_synth(p, [
            {"kind": "neg_category", "subkey": "spread", "value": {"title": "点差过宽", "summary": "…"}},
            {"kind": "topic_label", "subkey": "market", "value": {"title": "恒指方向争论", "summary": "…"}},
        ])
        assert p.neg_cats_for(OWN_CODE, "d1")[0]["stale"] is False
        assert p.topics_for(OWN_CODE, "d1")[0]["stale"] is False
        self._dirty(p)
        nc = p.neg_cats_for(OWN_CODE, "d1")[0]
        assert nc["label"] == "点差过宽" and nc["mentions"] == 4 and nc["stale"] is True
        tp = p.topics_for(OWN_CODE, "d1")[0]
        assert tp["title"] == "恒指方向争论" and tp["stale"] is True

    def test_stages_and_competitors_carry_a_top_level_flag(self, provider):
        p = annotated(provider)
        add_synth(p, [
            {"kind": "stage_unit", "subkey": "2026-08-25|am",
             "value": {"key": "2026-08-25|am", "category": "add_opportunity", "digest": "费率优势", "needs_review": False}},
            {"kind": "competitor_reason", "subkey": "2800",
             "value": {"code": "2800", "like_reasons": ["费率更低"], "dislike_reasons": [], "needs_review": False}},
        ])
        assert p.stages_for(OWN_CODE, "d1")["stale"] is False
        assert p.competitors_for(OWN_CODE, "d1")["stale"] is False
        self._dirty(p)
        st = p.stages_for(OWN_CODE, "d1")
        assert st["status"] == "ok" and st["stages"][0]["category"] == "add_opportunity" and st["stale"] is True
        c = p.competitors_for(OWN_CODE, "d1")
        assert c["stale"] is True
        assert next(x for x in c["list"] if x["code"] == "2800")["positiveThemes"][0]["title"] == "费率更低"

    def test_dirty_without_any_output_is_not_stale(self, provider):
        """脏了但模型从没写过 ⇒ 仍是 unavailable / 固定名，`stale=False`。"""
        p = annotated(provider)
        self._dirty(p)
        assert p.summary_for(OWN_CODE, "d1")["stale"] is False
        assert p.themes_for(OWN_CODE, "d1")["stale"] is False
        assert p.stages_for(OWN_CODE, "d1")["stale"] is False
        assert p.competitors_for(OWN_CODE, "d1")["stale"] is False
        assert p.hot_summaries("d1")[OWN_CODE]["stale"] is False
        assert all(r["stale"] is False for r in p.neg_cats_for(OWN_CODE, "d1"))
        assert all(r["stale"] is False for r in p.topics_for(OWN_CODE, "d1"))

    def test_clearing_the_flag_clears_stale(self, provider):
        p = annotated(provider)
        add_synth(p, [{"kind": "hot_summary", "value": {"text": "费率获认可", "needs_review": False}}])
        self._dirty(p)
        assert p.hot_summaries("d1")[OWN_CODE]["stale"] is True
        self._dirty(p, on=False)
        assert p.hot_summaries("d1")[OWN_CODE]["stale"] is False


class TestStages:
    def test_stages_unavailable_until_model_classifies_then_ok(self, provider):
        p = annotated(provider)
        st = p.stages_for(OWN_CODE, "d1")
        assert st["granularity"] == "half_day" and st["status"] == "unavailable"
        # 08-25 上午有 12 条有效态度（阈值 5）⇒ 一个足量时段
        assert [u["key"] for u in st["units"] if u["sufficient"]] == ["2026-08-25|am"]
        add_synth(p, [{"kind": "stage_unit", "subkey": "2026-08-25|am",
                       "value": {"key": "2026-08-25|am", "category": "add_opportunity",
                                 "digest": "费率优势被视为加仓理由", "needs_review": False}}])
        st = p.stages_for(OWN_CODE, "d1")
        assert st["status"] == "ok"
        s0 = st["stages"][0]
        assert s0["category"] == "add_opportunity" and s0["sentiment"] == "positive"
        assert s0["digests"][0]["digest"] == "费率优势被视为加仓理由"


class TestCompetitorsAndKol:
    def test_competitors_fixed_pairs_without_model_and_candidates_with(self, provider):
        p = annotated(provider)
        c = p.competitors_for(OWN_CODE, "d1")
        codes = {x["code"]: x for x in c["list"]}
        assert PEER_CODE in codes and codes[PEER_CODE]["relation"] == "confirmed"
        assert codes[PEER_CODE]["reasonStatus"] == "unavailable" and codes[PEER_CODE]["positiveThemes"] == []
        add_synth(p, [{"kind": "competitor_reason", "subkey": "2800",
                       "value": {"code": "2800", "like_reasons": ["费率更低"], "dislike_reasons": [], "needs_review": False},
                       "evidence_ids": ["c100", "c101"]}])
        c = p.competitors_for(OWN_CODE, "d1")
        auto = next(x for x in c["list"] if x["code"] == "2800")
        assert auto["relation"] == "auto_candidate" and auto["positiveThemes"][0]["title"] == "费率更低"
        assert auto["evidencePos"] == 2 and auto["evidenceNeg"] == 0

    def test_kol_mentions_derive_from_attitude_annotations(self, provider):
        p = annotated(provider)
        km = p.kol_mentions_for(OWN_CODE, "d1")
        assert km["status"] == "ok" and len(km["list"]) == 1
        k = km["list"][0]
        assert k["kolName"] == KOL_NAME and k["mentionCommentCount"] == 3
        assert k["dominantAttitude"] == "positive"          # 3 条 ⇒ 达到阈值
        assert k["evidence"][0]["authorType"] == "合作 KOL"

    def test_kol_mentions_below_three_have_no_dominant(self, provider):
        p = annotated(provider, n_pos=2, n_neg=8, kol_n=2)   # KOL 只写了前 2 条
        k = p.kol_mentions_for(OWN_CODE, "d1")["list"][0]
        assert k["mentionCommentCount"] == 2 and k["dominantAttitude"] is None

    def test_kol_opinions_need_the_kol_task(self, provider):
        p = annotated(provider)
        assert p.kol_opinions(KOL_NAME, "d1") is None
        add_annotations(p, [
            {"annotation_id": 950, "target_id": 100, "kind": "kol_summary", "value": "费率低，准备长期定投"},
            {"annotation_id": 951, "target_id": 100, "kind": "kol_action", "value": "加仓"},
        ])
        add_evidence(p, [{"evidence_id": 1, "annotation_id": 950, "source_target_id": 100,
                          "start_offset": 0, "end_offset": 5, "quote_text": "补一条评论"}])
        ops = p.kol_opinions(KOL_NAME, "d1")
        assert len(ops) == 1
        o = ops[0]
        assert o["code"] == OWN_CODE and o["action"] == "加仓" and o["actionTone"] == "pos"
        assert o["summary"] == "费率低，准备长期定投" and o["excerpt"] == "补一条评论"
        assert o["postType"] is None and o["confidence"] is None
