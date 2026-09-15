"""`core/themes.py`、`core/lifecycle.py`、`core/stages.py`、`core/topics.py` —— 主题、
负面类别、阶段观点、话题情绪的**计数**口径（ADR-0020）。

四个叶子模块共同的立场：模型只给名字与句子，数全部在这里。所以测试盯的是：分桶对不对、
基准未知时是不是 None（不是「新增」）、样本不足时段是否并入相邻、模型没写时是 None
而不是编一句。
"""

from datetime import datetime

import pytest

from core import lifecycle, stages, themes, topics
from core.calendar import build
from core.delta import UNAVAILABLE_TEXT


def unit(attitude, aspects, day, hour=10, direction=None):
    return {"attitude": attitude, "aspects": list(aspects), "market_direction": direction,
            "posted_at": datetime(2026, 8, day, hour)}


def bucket_index_for(rng):
    from datetime import date

    origin = date.fromisoformat(rng["from"])

    def bi(u):
        ts = u["posted_at"]
        off = (ts.date() - origin).days
        if rng["gran"] == "hour":
            return off * 24 + ts.hour
        if rng["gran"] == "day":
            return off
        return off // 7
    return bi


# ── lifecycle ──────────────────────────────────────────────────────────


class TestLifecycle:
    def test_unknown_base_is_none_not_new(self):
        assert lifecycle.lifecycle(5, None) is None
        assert lifecycle.lifecycle(None, 3) is None

    def test_rules_verbatim_from_design_source(self):
        assert lifecycle.lifecycle(3, 0) == "new"
        assert lifecycle.lifecycle(0, 0) is None
        assert lifecycle.lifecycle(12, 10) == "continuing"   # > 1.15x
        assert lifecycle.lifecycle(6, 10) == "fading"        # < 0.7x
        assert lifecycle.lifecycle(9, 10) == "continuing"    # 之间

    def test_severity_thresholds(self):
        assert lifecycle.severity(30, "continuing") == "high"
        assert lifecycle.severity(30, "fading") == "medium"  # 消退中的不算高
        assert lifecycle.severity(12, "new") == "medium"
        assert lifecycle.severity(5, "new") == "low"
        assert lifecycle.severity(None, "new") is None


# ── themes ─────────────────────────────────────────────────────────────


class TestThemes:
    def setup_method(self):
        self.rng = build("d7", datetime(2026, 8, 25).date())  # 08-19 ～ 08-25
        self.bi = bucket_index_for(self.rng)

    def test_buckets_by_polarity_and_aspect(self):
        units = [unit("positive", ["fee"], 19), unit("positive", ["fee", "dividend"], 20),
                 unit("negative", ["spread"], 21), unit("negative", [], 22)]
        out = themes.themes("3033", themes.group_by_polarity(units), None, self.rng["buckets"], self.bi)
        pos = {t["aspect"]: t for t in out["positive"]}
        assert pos["fee"]["mentions"] == 2 and pos["dividend"]["mentions"] == 1
        assert pos["fee"]["share"] == 100.0           # 两条积极都涉及费率
        assert pos["fee"]["buckets"][0]["mentions"] == 1 and pos["fee"]["buckets"][1]["mentions"] == 1
        neg = {t["aspect"]: t for t in out["negative"]}
        assert neg["spread"]["mentions"] == 1
        assert neg["other"]["mentions"] == 1          # aspects=[] 进 other
        assert out["positive"][0]["mentions"] >= out["positive"][-1]["mentions"]

    def test_confidence_is_none_and_labels_fall_back_to_facts(self):
        units = [unit("positive", ["fee"], 19)]
        out = themes.themes("3033", themes.group_by_polarity(units), None, self.rng["buckets"], self.bi)
        t = out["positive"][0]
        assert t["confidence"] is None
        assert t["title"] == "费率与管理费" and t["labelStatus"] == "unavailable"
        assert "1 条积极评论" in t["summary"]

    def test_model_labels_are_used_when_present(self):
        units = [unit("positive", ["fee"], 19)]
        labels = {("positive", "fee"): {"title": "费率同类最低", "summary": "多条评论把费率当作长期持有理由。",
                                        "evidence_ids": ["ev-1"]}}
        out = themes.themes("3033", themes.group_by_polarity(units), None, self.rng["buckets"], self.bi, labels)
        t = out["positive"][0]
        assert t["title"] == "费率同类最低" and t["labelStatus"] == "ok" and t["evidenceIds"] == ["ev-1"]

    def test_delta_unavailable_without_baseline_and_computed_with(self):
        units = [unit("positive", ["fee"], 19), unit("positive", ["fee"], 20)]
        no_base = themes.themes("3033", themes.group_by_polarity(units), None, self.rng["buckets"], self.bi)
        assert no_base["positive"][0]["delta"]["text"] == UNAVAILABLE_TEXT
        base = themes.group_by_polarity([unit("positive", ["fee"], 12)])
        with_base = themes.themes("3033", themes.group_by_polarity(units), base, self.rng["buckets"], self.bi)
        assert with_base["positive"][0]["delta"]["abs"] == 1
        # 基准期有标注但该 aspect 为 0 ⇒ 「新增」，不是暂不可用。
        assert themes.themes("3033", themes.group_by_polarity([unit("negative", ["spread"], 19)]),
                             base, self.rng["buckets"], self.bi)["negative"][0]["delta"]["short"] == "新增"


class TestNegCategories:
    def setup_method(self):
        self.rng = build("d7", datetime(2026, 8, 25).date())
        self.bi = bucket_index_for(self.rng)

    def test_excludes_performance_and_trading_intent(self):
        neg = [unit("negative", ["performance"], 19), unit("negative", ["trading_intent"], 19),
               unit("negative", ["spread"], 20), unit("negative", ["fee"], 21, 15)]
        out = themes.neg_categories("3033", neg, None, self.rng["buckets"], self.bi)
        assert {c["aspect"] for c in out} == {"spread", "fee"}
        # 分母是全部消极（4 条），所以各占比之和 < 100%。
        assert sum(c["shareOfNegative"] for c in out) == 50.0

    def test_lifecycle_none_without_baseline(self):
        neg = [unit("negative", ["spread"], 20)]
        c = themes.neg_categories("3033", neg, None, self.rng["buckets"], self.bi)[0]
        assert c["lifecycle"] is None and c["lifecycleLabel"] is None
        assert c["severity"] == "high"     # 100% 占比；生命周期未知不影响「高」
        assert c["firstSeenAt"] == "08-20 10:00"

    def test_lifecycle_with_baseline(self):
        neg = [unit("negative", ["spread"], 20)] * 3
        base = [unit("negative", ["spread"], 13)] * 10
        c = themes.neg_categories("3033", neg, base, self.rng["buckets"], self.bi)[0]
        assert c["lifecycle"] == "fading" and c["lifecycleLabel"] == "消退"
        assert c["severity"] == "medium"   # 100% 但消退 ⇒ 不算高
        assert c["delta"]["abs"] == -7

    def test_empty_when_no_negative(self):
        assert themes.neg_categories("3033", [], None, self.rng["buckets"], self.bi) == []

    def test_capped_at_six(self):
        neg = [unit("negative", [a], 19) for a in themes.NEG_CATEGORY_ASPECTS] * 2
        assert len(themes.neg_categories("3033", neg, None, self.rng["buckets"], self.bi)) == 6


# ── topics ─────────────────────────────────────────────────────────────


class TestTopics:
    def test_tricolor_maps_direction_not_attitude(self):
        rng = build("d7", datetime(2026, 8, 25).date())
        units = [unit(None, [], 19, direction="bearish"), unit(None, [], 19, direction="bearish"),
                 unit(None, [], 20, direction="bullish"), unit(None, [], 21, direction="neutral")]
        out = topics.market_topic("3033", units, rng["buckets"], bucket_index_for(rng))
        assert len(out) == 1
        t = out[0]
        assert (t["positive"], t["negative"], t["neutral"]) == (1, 2, 1)
        assert t["mentions"] == 4 and "08-19" in t["peak"]
        assert isinstance(t["split"], str) and "看空 2 条" in t["split"]
        assert t["evidenceCount"] == 4
        assert t["delta"]["short"] == "暂不可用"
        assert t["labelStatus"] == "unavailable" and "看空 2" in t["summary"]

    def test_topic_delta_distinguishes_unknown_and_known_empty_baseline(self):
        rng = build("d7", datetime(2026, 8, 25).date())
        units = [unit(None, [], 19, direction="bearish")]
        args = ("3033", units, rng["buckets"], bucket_index_for(rng))
        assert topics.market_topic(*args)[0]["delta"]["pct"] is None
        assert topics.market_topic(*args, base_units=[])[0]["delta"]["short"] == "新增"
        assert topics.market_topic(*args, base_units=units)[0]["delta"]["abs"] == 0

    def test_no_units_no_topic(self):
        rng = build("d7", datetime(2026, 8, 25).date())
        assert topics.market_topic("3033", [], rng["buckets"], bucket_index_for(rng)) == []


# ── stages ─────────────────────────────────────────────────────────────


def series_day(days, per_day):
    """日粒度序列，从 2026-08-19 起。`per_day[i] = (mentions, pos, neg, neu)`。"""
    from datetime import date, timedelta

    out = []
    for i, (m, p, n, u) in enumerate(per_day):
        d = date(2026, 8, 19) + timedelta(days=i)
        out.append({"i": i, "day": d.isoformat(), "hour": None, "label": d.strftime("%m-%d"),
                    "tip": "", "heat": m * 3, "mentions": m, "comments": m, "positive": p, "negative": n, "neutral": u})
    return out


class TestStages:
    def test_units_half_day_split(self):
        series = [{"i": h, "day": "2026-08-25", "hour": h, "label": "", "tip": "", "heat": 1,
                   "mentions": 1, "comments": 1, "positive": 1, "negative": 0, "neutral": 0} for h in range(24)]
        units = stages.units_from_series(series, half_day=True)
        assert [u["period"] for u in units] == ["am", "pm", "post"]
        assert [u["mentions"] for u in units] == [12, 5, 7]
        assert stages.unit_key(units[0]) == "2026-08-25|am"

    def test_threshold_and_tone(self):
        series = series_day(3, [(20, 12, 2, 6), (20, 3, 3, 14), (3, 1, 1, 1)])
        units = stages.units_from_series(series, False)
        thr = stages.classify_units(units, False)
        assert thr == 10
        assert [u["sufficient"] for u in units] == [True, False, False]   # 第 2 天有效 6 条 <10
        assert units[0]["tone"] == "positive"
        assert units[1]["digest"] == stages.LOW_DIGEST

    def test_merge_adjacent_same_category_and_absorb_low(self):
        series = series_day(4, [(20, 12, 2, 6), (20, 13, 1, 6), (2, 1, 0, 1), (20, 2, 14, 4)])
        cats = {"2026-08-19": "add_opportunity", "2026-08-20": "add_opportunity", "2026-08-22": "reduce"}
        out = stages.build("3033", series, "day", 7, cat_of=lambda u: cats.get(u["day"]),
                           digest_of=lambda u: f"{u['day']} 摘要", summary_of=lambda s: None)
        assert out["status"] == "ok"
        assert [s["category"] for s in out["stages"]] == ["add_opportunity", "reduce"]
        first = out["stages"][0]
        assert first["unitCount"] == 3 and first["lowUnits"] == 1       # 样本不足的第 3 天并入
        assert first["from"] == "2026-08-19" and first["to"] == "2026-08-21"
        assert first["sentiment"] == "positive"
        # 多时段阶段、模型没写总结 ⇒ None（暂不可用），不是编一句。
        assert first["summary"] is None
        # 单时段阶段沿用时段摘要。
        assert out["stages"][1]["summary"] == "2026-08-22 摘要"

    def test_fourteen_day_view_absorbs_isolated_single_day(self):
        per = [(20, 12, 2, 6)] * 5 + [(20, 2, 14, 4)] + [(20, 12, 2, 6)] * 8
        series = series_day(14, per)
        cats = {p["day"]: ("reduce" if i == 5 else "add_opportunity") for i, p in enumerate(series)}
        out = stages.build("3033", series, "day", 14, cat_of=lambda u: cats[u["day"]])
        # 设计源逐字：孤立单日并入**前一**阶段，之后不再把同类的前后两段二次合并。
        assert [s["category"] for s in out["stages"]] == ["add_opportunity", "add_opportunity"]
        assert out["stages"][0]["absorbed"] == 1 and out["stages"][0]["unitCount"] == 6
        assert "reduce" not in [s["category"] for s in out["stages"]]

    def test_no_model_output_is_unavailable_not_low_sample(self):
        series = series_day(2, [(20, 12, 2, 6), (20, 13, 1, 6)])
        out = stages.build("3033", series, "day", 7)
        assert out["status"] == "unavailable"
        assert all(s["category"] is None for s in out["stages"])

    def test_no_discussion_is_empty_and_low_only_is_low_sample(self):
        assert stages.build("3033", series_day(2, [(0, 0, 0, 0)] * 2), "day", 7)["status"] == "empty"
        assert stages.build("3033", series_day(2, [(3, 1, 1, 1)] * 2), "day", 7)["status"] == "low_sample"

    def test_sentiment_follows_counts_not_model(self):
        series = series_day(1, [(20, 2, 14, 4)])
        out = stages.build("3033", series, "day", 7, cat_of=lambda u: "add_opportunity")
        s = out["stages"][0]
        assert s["category"] == "add_opportunity" and s["sentiment"] == "negative"
        assert out["units"][0]["tone"] == "negative"

    def test_unknown_category_is_ignored(self):
        series = series_day(1, [(20, 12, 2, 6)])
        out = stages.build("3033", series, "day", 7, cat_of=lambda u: "made_up")
        assert out["stages"][0]["category"] is None
