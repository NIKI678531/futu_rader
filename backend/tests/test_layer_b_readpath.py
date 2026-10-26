"""Layer B 读路径（ADR-0020）：有标注时九个面板给真值；数来自 core，字来自 synthesis_outputs。

三态在每个面板上都要分得开：

- 没有一条态度标注 ⇒ 整块 None（`test_sql_provider.TestAiAndPriceSurfacesAreNone`）；
- 有标注、模型还没写字 ⇒ 数是真的，文字位是固定名／None，`labelStatus='unavailable'`；
- 生成过 ⇒ 模型的名字与句子进来，带 `reviewState` 与 `evidenceIds`。
"""

import json
from datetime import datetime

import pytest
from sqlalchemy import insert, select, update

from core import topics as core_topics
from radar_db.schema import annotation_runs, comments, feeds, mentions, meta_kv, synthesis_outputs
from radar_db.revisions import set_synthesis_generation
from providers.demo import DemoProvider, MISSING
from sql_fixture import (
    FEED_TEXT, KOL_NAME, OWN_CODE, PEER_CODE, add_annotations, add_comments, add_evidence,
    make_sql_provider,
)


@pytest.fixture
def provider():
    return make_sql_provider()


def annotated(p, n_pos=8, n_neg=4, kol_n=3, market_direction="bearish"):
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
    # 市场方向也只在当前 relevance=relevant 时进入产品话题聚合。
    rows += [
        {"annotation_id": aid, "target_id": 11, "kind": "relevance", "value": "relevant"},
        {"annotation_id": aid + 1, "target_id": 11, "kind": "market_direction", "value": market_direction},
    ]
    return add_annotations(p, rows)


def add_synth(p, rows):
    generation_fps = {
        (r.get("code", OWN_CODE), r.get("range_key", "d1"), r["kind"]):
        r.get("fp", f"fp-{r.get('code', OWN_CODE)}-{r.get('range_key', 'd1')}-{r['kind']}")
        for r in rows
    }
    def prompt_version(row):
        return row.get("prompt_version", core_topics.MARKET_OUTLOOK_SYNTH_VERSION)

    def run_id(row):
        return row.get("run_id", f"synth-test-{prompt_version(row)}")

    payload = [
        {
            "code": r.get("code", OWN_CODE), "range_key": r.get("range_key", "d1"), "anchor": "2026-08-25",
            "kind": r["kind"], "subkey": r.get("subkey", ""),
            "input_fingerprint": generation_fps[(r.get("code", OWN_CODE), r.get("range_key", "d1"), r["kind"])],
            "value_json": json.dumps(r["value"], ensure_ascii=False),
            "evidence_ids_json": json.dumps(r.get("evidence_ids", ["c100"])),
            "run_id": run_id(r), "review_state": r.get("review_state", "pending"),
            "created_at": datetime(2026, 8, 26, 10, i), "supersedes_id": r.get("supersedes_id"),
        }
        for i, r in enumerate(rows)
    ]
    with p._engine.begin() as conn:
        run_specs = {run_id(row): prompt_version(row) for row in rows}
        existing = set(conn.execute(
            select(annotation_runs.c.run_id).where(annotation_runs.c.run_id.in_(run_specs))
        ).scalars())
        if missing := set(run_specs).difference(existing):
            conn.execute(insert(annotation_runs), [{
                "run_id": synth_run_id,
                "task": "synthesize",
                "provider": "test",
                "model_id": "test-model",
                "prompt_version": run_specs[synth_run_id],
                "taxonomy_version": "v2",
                "schema_version": "synth-v1",
                "started_at": datetime(2026, 8, 26, 9),
                "finished_at": datetime(2026, 8, 26, 10),
                "status": "done",
                "input_count": 1,
                "success_count": 1,
                "error_count": 0,
            } for synth_run_id in missing])
        conn.execute(insert(synthesis_outputs), payload)
        for (code, range_key, kind), fp in generation_fps.items():
            set_synthesis_generation(conn, code, range_key, "2026-08-25", kind, fp)
            p._meta[f"synth_current_{code}_{range_key}_{kind}"] = f"2026-08-25|{fp}"
    p._cache.clear()
    return p


def add_purchase_intents(p, attitude, content, *, first_comment_id=400, count=10):
    comment_ids = list(range(first_comment_id, first_comment_id + count))
    add_comments(p, [
        {"comment_id": comment_id, "content": content}
        for comment_id in comment_ids
    ])
    rows = []
    annotation_id = first_comment_id * 10
    for comment_id in comment_ids:
        rows.extend([
            {"annotation_id": annotation_id, "target_id": comment_id,
             "kind": "relevance", "value": "relevant"},
            {"annotation_id": annotation_id + 1, "target_id": comment_id,
             "kind": "attitude", "value": attitude},
            {"annotation_id": annotation_id + 2, "target_id": comment_id,
             "kind": "aspect", "value": ["trading_intent"]},
            {"annotation_id": annotation_id + 3, "target_id": comment_id,
             "kind": "feedback_eligible", "value": False},
        ])
        annotation_id += 4
    return add_annotations(p, rows)


def test_demo_market_outlook_keeps_opinion_when_topics_are_empty(monkeypatch):
    provider = DemoProvider()
    monkeypatch.setattr(provider, "topics_for", lambda code, range_key: [])

    row = provider.market_outlook("3033", "d7")["list"][0]

    assert row["status"] == "empty"
    assert row["attitude"] is not None
    assert row["sentiment"] is None
    assert row["summary"] is None


def test_demo_market_outlook_keeps_topics_when_opinion_is_unavailable(monkeypatch):
    provider = DemoProvider()
    monkeypatch.setattr(provider, "pool", lambda range_key: {
        "list": [{"code": "3033", "attitude": None}],
    })

    row = provider.market_outlook("3033", "d7")["list"][0]

    assert row["status"] == "ok"
    assert row["attitude"] is None
    assert row["attitudeStatus"] == "unavailable"
    assert row["sentiment"] is not None
    assert row["summary"]


def test_demo_market_outlook_filters_product_topics_and_merges_selected_evidence(monkeypatch):
    provider = DemoProvider()
    monkeypatch.setattr(provider, "topics_for", lambda code, range_key: [
        {
            "id": "legacy-product", "title": "与同类产品的选择比较",
            "summary": "比较同类 ETF 的产品选择。", "mentions": 999,
            "positive": 999, "negative": 0, "neutral": 0,
            "evidenceIds": ["c-product"],
        },
        {
            "id": "market-hbm", "title": "海力士 HBM 产能进展",
            "summary": "供应链信息指向 HBM 产能变化。", "mentions": 30,
            "positive": 3, "negative": 1, "neutral": 0,
            "evidenceIds": ["c1", "c-shared"],
        },
        {
            "id": "market-fed", "title": "美联储议息预期",
            "summary": "利率路径影响风险资产表现。", "mentions": 20,
            "positive": 0, "negative": 3, "neutral": 0,
            "evidenceIds": ["c2", "c-shared"],
        },
        {
            "id": "market-gold", "title": "黄金避险与美元流向",
            "summary": "美元、黄金与避险资金的具体讨论" * 12, "mentions": 10,
            "positive": 0, "negative": 0, "neutral": 10,
            "evidenceIds": ["c-not-selected"],
        },
    ])

    row = provider.market_outlook("3033", "d7")["list"][0]

    assert "海力士 HBM" in row["summary"]
    assert "美联储议息预期" in row["summary"]
    assert "同类产品" not in row["summary"]
    assert "黄金避险" not in row["summary"]
    assert row["evidenceIds"] == ["c1", "c-shared", "c2"]
    assert row["sentiment"] == "neutral_bearish"


def test_demo_market_outlook_distinguishes_unknown_from_known_empty(monkeypatch):
    provider = DemoProvider()
    monkeypatch.setattr(provider, "pool", lambda range_key: None)
    monkeypatch.setattr(provider, "topics_for", lambda code, range_key: MISSING)
    assert provider.market_outlook("3033", "d7") is None

    monkeypatch.setattr(provider, "pool", lambda range_key: {
        "list": [{
            "code": "3033",
            "attitude": {"positive": 0, "negative": 0, "neutral": 0},
        }],
    })
    monkeypatch.setattr(provider, "topics_for", lambda code, range_key: [])
    assert provider.market_outlook("3033", "d7") == {"scope": "product", "list": []}


class TestThemesAndNegCats:
    def test_published_generation_hides_removed_subkeys_and_can_be_empty(self, provider):
        p = annotated(provider)
        add_synth(p, [
            {"kind": "theme_label", "subkey": "positive|fee", "fp": "old",
             "value": {"key": "positive|fee", "title": "费率", "summary": "旧正面"}},
            {"kind": "theme_label", "subkey": "negative|spread", "fp": "old",
             "value": {"key": "negative|spread", "title": "点差", "summary": "旧负面"}},
        ])
        assert set(p._synth(OWN_CODE, "d1", "theme_label")) == {
            "positive|fee", "negative|spread"
        }

        add_synth(p, [
            {"kind": "theme_label", "subkey": "positive|fee", "fp": "shrunk",
             "value": {"key": "positive|fee", "title": "新费率", "summary": "只剩正面"}},
        ])
        assert set(p._synth(OWN_CODE, "d1", "theme_label")) == {"positive|fee"}

        with p._engine.begin() as conn:
            set_synthesis_generation(
                conn, OWN_CODE, "d1", "2026-08-25", "theme_label", "intentional-empty"
            )
        p._meta[f"synth_current_{OWN_CODE}_d1_theme_label"] = (
            "2026-08-25|intentional-empty"
        )
        p._cache.clear()
        assert p._synth(OWN_CODE, "d1", "theme_label") == {}

    def test_units_reuse_window_queries_across_products(self, provider, monkeypatch):
        import providers.sql as sql_module
        from unittest.mock import Mock

        annotated(provider)
        query = Mock(wraps=sql_module.current_annotations)
        monkeypatch.setattr(sql_module, "current_annotations", query)
        window = provider.build_range("d1")
        provider._units(OWN_CODE, window)
        provider._base_units(OWN_CODE, window)
        for code in provider._by_code:
            provider._units(code, window)
            provider._base_units(code, window)
        calls = query.call_count
        assert calls > 0
        for code in provider._by_code:
            provider._units(code, window)
            provider._base_units(code, window)
        assert query.call_count == calls

    def test_pool_uses_negative_categories_not_all_negative_attitudes(self, provider):
        annotated(provider)
        add_annotations(provider, [
            {"annotation_id": 900, "target_id": 14, "kind": "relevance", "value": "relevant"},
            {"annotation_id": 901, "target_id": 14, "kind": "attitude", "value": "negative"},
            {"annotation_id": 902, "target_id": 14, "kind": "aspect", "value": ["spread"]},
            {"annotation_id": 903, "target_id": 11, "kind": "relevance", "value": "relevant"},
            {"annotation_id": 904, "target_id": 11, "kind": "attitude", "value": "negative"},
            {"annotation_id": 905, "target_id": 11, "kind": "aspect", "value": ["performance"]},
        ])
        pool = provider.pool("d1")
        assert pool["negMentions"][OWN_CODE] == 4
        assert pool["alerts"][OWN_CODE] == 1
        assert pool["negMentions"][PEER_CODE] is None
        assert pool["own"]["neg"] is None
        provider._meta["own_analysis_progress"] = json.dumps({
            "anchor": "2026-08-25", "baselineFrom": "2026-08-12", "sourceVersion": {},
            "products": {code: {"complete": True, "queue": {}} for code in provider._by_code},
        })
        provider._cache.clear()
        pool = provider.pool("d1")
        assert pool["own"]["neg"] == 4
        assert pool["own"]["dNeg"]["abs"] == 3
        # 通用分析完成标记来自旧 Prompt，不能证明严格 v5 舆情已扫描。
        assert pool["own"]["risk"] is None
        assert pool["complianceCount"][PEER_CODE] is None

    def test_old_completed_scope_does_not_claim_v5_risk_was_scanned(self, provider):
        provider._meta["own_analysis_progress"] = json.dumps({
            "anchor": "2026-08-25", "baselineFrom": "2026-08-12", "sourceVersion": {},
            "products": {OWN_CODE: {"complete": True, "queue": {}}},
        })
        assert provider.compliance_for(OWN_CODE, "d1") == {"status": "unavailable", "list": []}
        assert provider.pool("d1")["complianceCount"][OWN_CODE] is None
        assert provider.pool("d1")["own"]["risk"] is None

    def test_counts_come_from_annotations_labels_from_synth(self, provider):
        p = annotated(provider)
        th = p.themes_for(OWN_CODE, "d1")
        assert [t["aspect"] for t in th["positive"]] == ["fee"]
        assert th["positive"][0]["mentions"] == 8 and th["positive"][0]["labelStatus"] == "unavailable"
        assert th["positive"][0]["title"] == "费率与持有成本获认可"  # 具体结论兜底
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

    def test_dirty_pair_keeps_theme_and_category_labels_and_marks_every_view_stale(self, provider):
        p = annotated(provider)
        add_synth(p, [
            {"kind": "theme_label", "subkey": "negative|spread",
             "value": {"key": "negative|spread", "title": "点差偏大", "summary": "多人抱怨点差"}},
            {"kind": "neg_category", "subkey": "spread",
             "value": {"key": "spread", "title": "点差偏大", "summary": "多人抱怨点差"}},
        ])
        assert p.themes_for(OWN_CODE, "d1")["stale"] is False
        p._meta[f"synth_dirty_{OWN_CODE}_d1"] = "1"
        p._cache.clear()

        themes = p.themes_for(OWN_CODE, "d1")
        categories = p.neg_cats_for(OWN_CODE, "d1")

        assert themes["stale"] is True
        assert [t["title"] for t in themes["negative"]] == ["点差偏大"]
        assert categories and all(row["stale"] is True for row in categories)
        assert p.topics_for(OWN_CODE, "d1") and all(
            row["stale"] is False for row in p.topics_for(OWN_CODE, "d1")
        )

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


class TestTopicsAndHot:
    def test_completed_codes_use_the_selected_ranges_persisted_progress(self, provider):
        provider._meta["own_analysis_progress"] = json.dumps({
            "anchor": "2026-08-25", "baselineFrom": "2026-08-22", "sourceVersion": {},
            "products": {PEER_CODE: {"complete": True, "queue": {}}},
        })
        provider._meta["own_analysis_progress_d7"] = json.dumps({
            "anchor": "2026-08-25", "baselineFrom": "2026-08-12", "sourceVersion": {},
            "products": {OWN_CODE: {"complete": True, "queue": {}}},
        })

        assert provider._completed_codes("2026-08-19", "2026-08-25", "d7") == {OWN_CODE}

    def test_scope_completion_refreshes_cached_unknown_state(self, provider):
        progress = {"anchor": "2026-08-25", "baselineFrom": "2026-08-12", "sourceVersion": {},
                    "products": {OWN_CODE: {"complete": False, "queue": {}}}}
        with provider._engine.begin() as conn:
            conn.execute(insert(meta_kv).values(k="own_analysis_progress", v=json.dumps(progress)))
        provider.refresh()
        assert provider.hot_summaries("d1")[OWN_CODE]["status"] == "unavailable"
        progress["products"][OWN_CODE]["complete"] = True
        with provider._engine.begin() as conn:
            conn.execute(update(meta_kv).where(meta_kv.c.k == "own_analysis_progress")
                         .values(v=json.dumps(progress)))
        assert provider.refresh() is True
        assert provider.hot_summaries("d1")[OWN_CODE]["status"] == "empty"

    def test_completed_empty_scope_is_empty_but_unknown_products_stay_unavailable(self, provider):
        provider._meta["own_analysis_progress"] = json.dumps({
            "anchor": "2026-08-25", "baselineFrom": "2026-08-12", "sourceVersion": {},
            "products": {OWN_CODE: {"complete": True, "queue": {"done": 3}},
                         PEER_CODE: {"complete": False, "queue": {"pending": 1}}},
        })
        summaries = provider.hot_summaries("d1")
        assert summaries[OWN_CODE]["status"] == "empty"
        assert summaries[OWN_CODE]["sample"] == 0
        assert summaries[PEER_CODE]["status"] == "unavailable"
        assert provider.themes_for(OWN_CODE, "d1") == {"positive": [], "negative": [], "stale": False}
        assert provider._completed_codes("2026-07-01", "2026-08-25") == set()
        provider._meta["data_revision"] = "new-source"
        assert provider._completed_codes("2026-08-25", "2026-08-25") == set()

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

    def test_market_outlook_uses_topic_label_and_only_returns_current_product(self, provider):
        assert provider.market_outlook(OWN_CODE, "d1") is None
        p = annotated(provider)
        pending = p.market_outlook(OWN_CODE, "d1")
        assert pending["scope"] == "product"
        assert [row["code"] for row in pending["list"]] == [OWN_CODE]
        assert pending["list"][0] == {
            "code": OWN_CODE,
            "name": p._by_code[OWN_CODE]["name"],
            "status": "unavailable",
            "attitude": "positive",
            "attitudeLabel": "积极偏多",
            "attitudeStatus": "ok",
            "sentiment": "bearish",
            "sentimentLabel": "看空",
            "summary": None,
            "stale": False,
            "reviewState": None,
            "evidenceIds": [],
        }

        add_synth(p, [{
            "kind": "topic_label", "subkey": "market",
            "value": {"title": "恒指方向争论", "summary": "多条评论看空大市。"},
        }])
        current_topic = p.topics_for(OWN_CODE, "d1")[0]
        assert current_topic["labelStatus"] == "ok"
        assert current_topic["stale"] is False
        generated = p.market_outlook(OWN_CODE, "d1")["list"][0]
        assert generated["status"] == "ok"
        assert generated["attitude"] == "positive"
        assert generated["sentiment"] == "bearish"
        assert generated["summary"] == "多条评论看空大市。"
        assert generated["stale"] is False
        assert generated["reviewState"] == "pending"
        assert generated["evidenceIds"] == ["c100"]

        p._meta[f"synth_dirty_{OWN_CODE}_d1"] = "1"
        p._cache.clear()
        stale = p.market_outlook(OWN_CODE, "d1")["list"][0]
        assert stale["status"] == "ok"
        assert stale["summary"] == "多条评论看空大市。"
        assert stale["stale"] is True
        assert stale["reviewState"] == "pending"
        assert stale["evidenceIds"] == ["c100"]

    def test_market_outlook_rejects_old_topic_prompt_version_without_affecting_generation_marker(
        self, provider,
    ):
        p = annotated(provider)
        add_synth(p, [{
            "kind": "topic_label",
            "subkey": "market",
            "prompt_version": "synth-v7",
            "value": {"title": "旧版市场方向", "summary": "旧版泛化摘要。"},
            "evidence_ids": ["c-old"],
        }])

        topic = p.topics_for(OWN_CODE, "d1")[0]
        assert topic["title"] == "市场方向与指数走势讨论"
        assert topic["labelStatus"] == "unavailable"
        assert topic["stale"] is True
        assert topic["evidenceIds"] == ["c-old"]

        row = p.market_outlook(OWN_CODE, "d1")["list"][0]
        assert row["status"] == "unavailable"
        assert row["summary"] is None
        assert row["stale"] is True
        assert row["evidenceIds"] == ["c-old"]

        published = p._published(OWN_CODE, "d1", "topic_label")
        assert published == ("2026-08-25", f"fp-{OWN_CODE}-d1-topic_label")

    def test_prompt_version_guard_is_scoped_to_topic_label(self, provider):
        p = annotated(provider)
        add_synth(p, [{
            "kind": "theme_label",
            "subkey": "positive|fee",
            "prompt_version": "synth-v7",
            "value": {
                "key": "positive|fee",
                "title": "旧版但仍兼容的费率主题",
                "summary": "评论认可管理费水平。",
            },
        }])

        theme = p.themes_for(OWN_CODE, "d1")["positive"][0]
        assert theme["title"] == "旧版但仍兼容的费率主题"
        assert theme["labelStatus"] == "ok"

    def test_market_outlook_known_empty_is_not_unknown(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "_units", lambda code, rng: {
            "units": [], "opinion_units": [], "market": [],
        })
        monkeypatch.setattr(provider, "topics_for", lambda code, range_key: [])

        assert provider.market_outlook(OWN_CODE, "d1") == {
            "scope": "product", "list": [],
        }

    def test_market_outlook_attitude_does_not_follow_opposite_market_direction(self, provider):
        p = annotated(provider, n_pos=91, n_neg=209, market_direction="bullish")
        add_synth(p, [{
            "kind": "topic_label", "subkey": "market",
            "value": {"title": "韩国股市与芯片业绩", "summary": "讨论押注芯片财报带动韩国股市短线反弹。"},
        }])

        row = p.market_outlook(OWN_CODE, "d1")["list"][0]

        assert row["attitude"] == "negative"
        assert row["attitudeLabel"] == "消极偏多"
        assert row["sentiment"] == "bullish"
        assert row["sentimentLabel"] == "看多"
        assert row["summary"] == "讨论押注芯片财报带动韩国股市短线反弹。"

    @pytest.mark.parametrize(("attitude", "content", "label"), [
        ("positive", "今日加仓这只 ETF", "积极偏多"),
        ("negative", "这只 ETF 全部卖出，不再买入", "消极偏多"),
    ])
    def test_market_outlook_counts_pure_purchase_intent_as_opinion(
        self, provider, attitude, content, label,
    ):
        p = annotated(provider, n_pos=1, n_neg=1, market_direction="bullish")
        add_purchase_intents(p, attitude, content)

        units = p._units(OWN_CODE, p.build_range("d1"))
        assert len(units["units"]) == 2
        assert len(units["opinion_units"]) == 12

        row = p.market_outlook(OWN_CODE, "d1")["list"][0]
        assert row["attitude"] == attitude
        assert row["attitudeLabel"] == label
        assert row["sentiment"] == "bullish"

    def test_market_outlook_keeps_purchase_opinion_without_market_direction(self, provider):
        add_purchase_intents(provider, "positive", "今日加仓这只 ETF")

        row = provider.market_outlook(OWN_CODE, "d1")["list"][0]

        assert row["status"] == "empty"
        assert row["attitude"] == "positive"
        assert row["attitudeLabel"] == "积极偏多"
        assert row["sentiment"] is None
        assert row["summary"] is None

    def test_market_outlook_neutral_only_opinions_are_low_sample(self, provider):
        add_purchase_intents(
            provider, "neutral", "先观察成交量与折溢价", count=12,
        )

        row = provider.market_outlook(OWN_CODE, "d1")["list"][0]

        assert row["status"] == "empty"
        assert row["attitude"] is None
        assert row["attitudeStatus"] == "low_sample"
        assert row["sentiment"] is None

    def test_market_outlook_keeps_market_summary_without_purchase_opinion(self, provider):
        add_annotations(provider, [
            {"annotation_id": 2000, "target_id": 11,
             "kind": "relevance", "value": "relevant"},
            {"annotation_id": 2001, "target_id": 11,
             "kind": "market_direction", "value": "bullish"},
        ])
        add_synth(provider, [{
            "kind": "topic_label", "subkey": "market",
            "value": {"title": "市场反弹", "summary": "评论认为指数存在反弹机会。"},
        }])

        row = provider.market_outlook(OWN_CODE, "d1")["list"][0]

        assert row["status"] == "ok"
        assert row["attitude"] is None
        assert row["attitudeStatus"] == "unavailable"
        assert row["sentiment"] == "bullish"
        assert row["summary"] == "评论认为指数存在反弹机会。"

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
        add_synth(p, [{"kind": "hot_summary", "value": {
            "text": "有评论提到费率较低", "evidence_ids": ["c100"], "needs_review": False,
        }}])
        hs = p.hot_summaries("d1")[OWN_CODE]
        assert hs["status"] == "low_sample" and hs["sample"] == 5
        assert hs["text"] == "样本不足：有评论提到费率较低"
        assert hs["evidenceIds"] == ["c100"]


class TestSummary:
    def test_dirty_source_keeps_the_previous_summary_and_marks_it_stale(self, provider):
        p = annotated(provider)
        add_synth(p, [{"kind": "summary", "value": {"points": [
            {"text": "多条评论认可费率", "evidence_ids": ["c100"]}]} }])
        fresh = p.summary_for(OWN_CODE, "d1")
        assert fresh["aiStatus"] == "ok" and fresh["stale"] is False
        p._meta[f"synth_dirty_{OWN_CODE}_d1"] = "1"
        p._cache.clear()
        dirty = p.summary_for(OWN_CODE, "d1")
        assert dirty["aiStatus"] == "ok" and dirty["stale"] is True
        assert dirty["text"].endswith("多条评论认可费率。")

    def test_generation_from_an_older_anchor_is_served_as_stale(self, provider):
        p = annotated(provider)
        add_synth(p, [
            {"kind": "summary", "value": {"points": [{"text": "费率获认可", "evidence_ids": ["c100"]}]}},
            {"kind": "hot_summary", "value": {"text": "费率获认可"}},
        ])
        with p._engine.begin() as conn:
            conn.execute(update(synthesis_outputs).values(anchor="2026-08-24"))
        for kind in ("summary", "hot_summary"):
            p._meta[f"synth_current_{OWN_CODE}_d1_{kind}"] = f"2026-08-24|fp-{OWN_CODE}-d1-{kind}"
        p._cache.clear()

        summary = p.summary_for(OWN_CODE, "d1")
        hot = p.hot_summaries("d1")[OWN_CODE]

        assert summary["aiStatus"] == "ok" and summary["stale"] is True
        assert hot["status"] == "ok" and hot["text"] == "费率获认可" and hot["stale"] is True

    def test_generation_newer_than_the_anchor_is_ignored(self, provider):
        p = annotated(provider)
        add_synth(p, [{"kind": "summary", "value": {"points": [{"text": "未来结论", "evidence_ids": ["c100"]}]}}])
        p._meta[f"synth_current_{OWN_CODE}_d1_summary"] = f"2026-08-26|fp-{OWN_CODE}-d1-summary"
        p._cache.clear()

        summary = p.summary_for(OWN_CODE, "d1")

        assert summary["aiStatus"] == "unavailable" and summary["stale"] is False

    def test_summary_text_is_facts_plus_model_points(self, provider):
        p = annotated(provider)
        s = p.summary_for(OWN_CODE, "d1")
        assert s["low"] is False and s["sample"] == 12 and s["aiStatus"] == "unavailable"
        assert s["summaryMode"] == "facts" and 3 <= len(s["points"]) <= 5
        assert all("积极观点中" not in point["text"] and "消极观点中" not in point["text"]
                   for point in s["points"])
        assert "认可 8 条、质疑 4 条" in s["text"] and "认可反馈多 4 条" in s["text"]
        assert "讨论最集中的时间桶" in s["text"]
        assert s["points"][-1]["text"].startswith("较") and "讨论热度" in s["text"]
        add_synth(p, [{"kind": "summary", "value": {"points": [
            {"text": "多条评论认可费率", "evidence_ids": ["c100"]},
            {"text": "点差是主要抱怨", "evidence_ids": ["c108"]}], "needs_review": False}}])
        s = p.summary_for(OWN_CODE, "d1")
        assert s["aiStatus"] == "ok" and len(s["points"]) == 2
        assert s["text"].endswith("多条评论认可费率。点差是主要抱怨。")
        assert s["evidenceIds"] == ["c100", "c108"]

    def test_summary_read_path_caps_legacy_rows_at_five_points(self, provider):
        p = annotated(provider)
        add_synth(p, [{"kind": "summary", "value": {"points": [
            {"text": f"第 {index} 条具体产品体验", "evidence_ids": ["c100"]}
            for index in range(1, 7)
        ], "needs_review": False}}])

        summary = p.summary_for(OWN_CODE, "d1")
        assert len(summary["points"]) == 5
        assert "第 6 条具体产品体验" not in summary["text"]

    def test_summary_low_sample_wording_is_verbatim(self, provider):
        p = annotated(provider, n_pos=3, n_neg=2)
        s = p.summary_for(OWN_CODE, "d1")
        assert s["low"] is True
        assert "有效态度提及为 5 条，低于 10 条的判定阈值，本区间不输出整体倾向结论" in s["text"]


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

    def test_kol_mentions_merge_same_source_posts_with_confirmed_comment_opinions(self, provider):
        """产品页以 /kol 原帖为主，同时保留已确认的评论观点作补充证据。"""
        p = annotated(provider, n_pos=2, n_neg=0, kol_n=2)
        add_comments(p, [{"comment_id": 102, "feed_id": 3, "content": "跨区旧 KOL 观点",
                          "author_name": KOL_NAME}])
        add_annotations(p, [
            {"annotation_id": 950, "target_id": 100, "kind": "kol_summary", "value": "费率较低，适合持有"},
            {"annotation_id": 951, "target_id": 100, "kind": "kol_action", "value": "持有不动"},
            {"annotation_id": 952, "target_id": 101, "kind": "kol_summary", "value": "流动性尚可"},
            {"annotation_id": 953, "target_id": 101, "kind": "kol_action", "value": "观望"},
            {"annotation_id": 954, "target_id": 102, "kind": "kol_summary", "value": "第三条真实观点"},
            {"annotation_id": 955, "target_id": 102, "kind": "kol_action", "value": "未提及操作"},
            {"annotation_id": 956, "target_id": 102, "kind": "relevance", "value": "relevant"},
            {"annotation_id": 957, "target_id": 2, "target_type": "feed", "subject_code": "",
             "kind": "post_type", "value": "market"},
            {"annotation_id": 958, "target_id": 2, "target_type": "feed", "subject_code": "",
             "kind": "summary", "value": "AI 摘要：费率较低，适合长期持有"},
        ])
        add_evidence(p, [{"evidence_id": 1, "annotation_id": 954, "source_target_id": 102,
                          "start_offset": 0, "end_offset": 5, "quote_text": "第三条真实"}])

        km = p.kol_mentions_for(OWN_CODE, "d1")

        assert km["status"] == "ok" and len(km["list"]) == 1
        k = km["list"][0]
        assert k["kolName"] == KOL_NAME and k["mentionCommentCount"] == 3
        assert k["dominantAttitude"] is None, "只有 2 条有效态度，不能用 3 条观点越过态度阈值"
        assert k["latestPostAt"] == "2026-08-25 09:00"
        assert k["representativeSummary"] == "AI 摘要：费率较低，适合长期持有"
        assert k["representativeExcerpt"] == FEED_TEXT
        assert [e["sourceKind"] for e in k["evidence"]].count("帖子") == 1
        comments = {e["id"].rsplit("-", 1)[-1]: (e["attitude"], e["attitudeLabel"])
                    for e in k["evidence"] if e["sourceKind"] == "评论"}
        assert comments == {"100": ("positive", "积极"), "101": ("positive", "积极")}
        assert p.kol_mentions_for(PEER_CODE, "d1") == {
            "status": "empty", "scope": "合作 KOL 名单", "list": [],
        }

    @pytest.mark.parametrize("summary_value", [None, False], ids=["missing", "explicit-none"])
    def test_kol_mentions_keep_post_summary_nullable_and_raw_excerpt(self, provider, summary_value):
        rows = [
            {"annotation_id": 950, "target_id": 2, "target_type": "feed", "subject_code": "",
             "kind": "post_type", "value": "market"},
        ]
        if summary_value is not None:
            rows.append({
                "annotation_id": 951, "target_id": 2, "target_type": "feed", "subject_code": "",
                "kind": "summary", "value": summary_value,
            })
        add_annotations(provider, rows)

        row = provider.kol_mentions_for(OWN_CODE, "d1")["list"][0]

        assert row["representativeSummary"] is None
        assert row["representativeExcerpt"] == FEED_TEXT
        assert row["evidence"][0]["excerpt"] == FEED_TEXT

    def test_kol_mentions_use_attitude_only_for_the_dominant_label(self, provider):
        p = annotated(provider)
        add_annotations(p, [
            {"annotation_id": 950 + i * 2, "target_id": 100 + i, "kind": "kol_summary",
             "value": f"第 {i + 1} 条真实观点"}
            for i in range(3)
        ] + [
            {"annotation_id": 951 + i * 2, "target_id": 100 + i, "kind": "kol_action",
             "value": "未提及操作"}
            for i in range(3)
        ])
        km = p.kol_mentions_for(OWN_CODE, "d1")
        assert km["status"] == "ok" and len(km["list"]) == 1
        k = km["list"][0]
        assert k["kolName"] == KOL_NAME and k["mentionCommentCount"] == 4
        assert k["dominantAttitude"] == "positive"          # 3 条 ⇒ 达到阈值
        assert k["evidence"][0]["authorType"] == "合作 KOL"

    def test_kol_mentions_drop_a_stale_summary_after_relevance_flips(self, provider):
        p = annotated(provider, n_pos=1, n_neg=0, kol_n=1)
        add_annotations(p, [
            {"annotation_id": 950, "target_id": 100, "kind": "kol_summary",
             "value": "费率较低，适合持有"},
            {"annotation_id": 951, "target_id": 100, "kind": "relevance",
             "value": "irrelevant", "supersedes_id": 1},
        ])

        row = p.kol_mentions_for(OWN_CODE, "d1")["list"][0]

        assert row["mentionCommentCount"] == 1
        assert [item["sourceKind"] for item in row["evidence"]] == ["帖子"]

    def test_kol_mentions_below_three_have_no_dominant(self, provider):
        p = annotated(provider, n_pos=2, n_neg=8, kol_n=2)   # KOL 只写了前 2 条
        add_annotations(p, [
            {"annotation_id": 950, "target_id": 100, "kind": "kol_summary", "value": "第一条真实观点"},
            {"annotation_id": 951, "target_id": 100, "kind": "kol_action", "value": "观望"},
            {"annotation_id": 952, "target_id": 101, "kind": "kol_summary", "value": "第二条真实观点"},
            {"annotation_id": 953, "target_id": 101, "kind": "kol_action", "value": "观望"},
        ])
        k = p.kol_mentions_for(OWN_CODE, "d1")["list"][0]
        assert k["mentionCommentCount"] == 3 and k["dominantAttitude"] is None

    def test_kol_mentions_comment_coverage_never_hides_same_source_posts(self, provider):
        add_comments(provider, [
            {"comment_id": 100, "content": "只是路过，没有产品观点", "author_name": KOL_NAME},
            {"comment_id": 101, "content": "另一条没有观点", "author_name": KOL_NAME},
        ])
        add_annotations(provider, [
            {"annotation_id": 950, "target_id": 100, "kind": "kol_summary", "value": False},
            {"annotation_id": 951, "target_id": 100, "kind": "kol_action", "value": "未提及操作"},
        ])
        before = provider.kol_mentions_for(OWN_CODE, "d1")
        assert before["status"] == "ok" and before["list"][0]["evidenceCount"] == 1
        assert before["list"][0]["evidence"][0]["sourceKind"] == "帖子"

        add_annotations(provider, [
            {"annotation_id": 952, "target_id": 101, "kind": "kol_summary", "value": "   "},
            {"annotation_id": 953, "target_id": 101, "kind": "kol_action", "value": "未提及操作"},
        ])
        after = provider.kol_mentions_for(OWN_CODE, "d1")
        assert after["status"] == "ok" and after["list"][0]["evidenceCount"] == 1

    def test_kol_mentions_fall_back_to_confirmed_comments_without_a_related_kol_post(self, provider):
        with provider._engine.begin() as conn:
            conn.execute(update(feeds).where(feeds.c.feed_id == 3).values(code=PEER_CODE))
        add_comments(provider, [{"comment_id": 100, "feed_id": 3, "content": "原评论：成交很顺畅，但仍要留意价差。",
                                 "author_name": KOL_NAME}])
        add_annotations(provider, [
            {"annotation_id": 949, "target_id": 100, "subject_code": PEER_CODE,
             "kind": "relevance", "value": "relevant"},
            {"annotation_id": 950, "target_id": 100, "subject_code": PEER_CODE,
             "kind": "kol_summary", "value": "AI 摘要：竞品流动性不错"},
        ])

        km = provider.kol_mentions_for(PEER_CODE, "d1")

        assert km["status"] == "ok" and len(km["list"]) == 1
        row = km["list"][0]
        assert row["kolName"] == KOL_NAME and row["mentionCommentCount"] == 1
        assert row["latestPostAt"] is None
        assert row["representativeSummary"] == "AI 摘要：竞品流动性不错"
        assert row["representativeExcerpt"] == "原评论：成交很顺畅，但仍要留意价差。"
        assert row["evidence"][0]["sourceKind"] == "评论"

    def test_kol_mentions_sort_by_latest_post_then_put_comment_only_accounts_last(self, provider):
        names = sorted(
            k["name"] for k in provider._kols
            if k.get("active") and k["name"] != KOL_NAME
        )[:3]
        with provider._engine.begin() as conn:
            conn.execute(insert(feeds), [
                {
                    "feed_id": 20 + i, "code": OWN_CODE, "source_ticker": f"0{OWN_CODE}.HK",
                    "posted_at": datetime(2026, 8, 25, 7), "feed_type": 1,
                    "author_uid": f"tie-kol-{i}", "author_name": name,
                    "title": None, "content": f"{name} 的新原帖", "like_count": 0,
                    "comment_count": 0, "image_count": 0, "share_count": 0,
                    "browse_count": 0, "raw_json_broken": False,
                }
                for i, name in enumerate(names[:2])
            ])
            conn.execute(insert(mentions), [
                {"feed_id": 20 + i, "code": OWN_CODE, "source": source, "in_pool": True}
                for i in range(2)
                for source in ("anchor", "body")
            ])

        # 旧原帖 KOL 的评论更新且数量更多，也不得越过 15:00 的新原帖。
        # 只有评论的账号即使时间最新，仍保留在原帖账号之后。
        add_comments(provider, [
            {"comment_id": 200, "content": "旧原帖 KOL 的新评论一", "author_name": KOL_NAME},
            {"comment_id": 201, "content": "旧原帖 KOL 的新评论二", "author_name": KOL_NAME},
            {"comment_id": 202, "content": "只评论账号的最新观点", "author_name": names[2]},
        ])
        with provider._engine.begin() as conn:
            conn.execute(update(comments).where(comments.c.comment_id.in_([200, 201])).values(
                posted_at=datetime(2026, 8, 25, 9),
            ))
            conn.execute(update(comments).where(comments.c.comment_id == 202).values(
                posted_at=datetime(2026, 8, 25, 10),
            ))
        add_annotations(provider, [
            {"annotation_id": 970 + i * 2, "target_id": 200 + i,
             "kind": "relevance", "value": "relevant"}
            for i in range(3)
        ] + [
            {"annotation_id": 971 + i * 2, "target_id": 200 + i,
             "kind": "kol_summary", "value": f"第 {i + 1} 条 AI 观点"}
            for i in range(3)
        ])

        rows = provider.kol_mentions_for(OWN_CODE, "d1")["list"]

        assert [row["kolName"] for row in rows] == [names[0], names[1], KOL_NAME, names[2]]
        assert [row["latestPostAt"] for row in rows] == [
            "2026-08-25 15:00", "2026-08-25 15:00", "2026-08-25 09:00", None,
        ]
        assert rows[2]["lastMentionedAt"] == "2026-08-25 17:00"
        assert rows[3]["lastMentionedAt"] == "2026-08-25 18:00"

    def test_kol_opinions_need_the_kol_task(self, provider):
        p = annotated(provider, kol_n=2)
        assert p.kol_opinions(KOL_NAME, "d1") is None
        add_annotations(p, [
            {"annotation_id": 950, "target_id": 100, "kind": "kol_summary", "value": "费率低，准备长期定投"},
            {"annotation_id": 951, "target_id": 100, "kind": "kol_action", "value": "加仓"},
            {"annotation_id": 952, "target_id": 100, "kind": "post_type", "value": "action",
             "confidence": 0.88},
            {"annotation_id": 953, "target_id": 101, "kind": "kol_summary", "value": False},
            {"annotation_id": 954, "target_id": 101, "kind": "kol_action", "value": "未提及操作"},
            {"annotation_id": 955, "target_id": 101, "kind": "post_type", "value": "other"},
        ])
        add_evidence(p, [{"evidence_id": 1, "annotation_id": 950, "source_target_id": 100,
                          "start_offset": 0, "end_offset": 5, "quote_text": "补一条评论"}])
        ops = p.kol_opinions(KOL_NAME, "d1")
        assert len(ops) == 1
        o = ops[0]
        assert o["id"] == f"kol-op-100-{OWN_CODE}"
        assert o["code"] == OWN_CODE and o["action"] == "加仓" and o["actionTone"] == "pos"
        assert o["summary"] == "费率低，准备长期定投" and o["excerpt"] == "补一条评论"
        assert o["postType"] == "action" and o["typeLabel"] == "操作宣言"
        assert o["confidence"] == 0.88

    def test_kol_opinions_with_only_explicit_no_opinion_results_are_empty(self, provider):
        add_comments(provider, [{"comment_id": 100, "content": "只聊大盘", "author_name": KOL_NAME}])
        add_annotations(provider, [
            {"annotation_id": 950, "target_id": 100, "kind": "kol_summary", "value": False},
            {"annotation_id": 951, "target_id": 100, "kind": "kol_action", "value": "未提及操作"},
            {"annotation_id": 952, "target_id": 100, "kind": "post_type", "value": "market"},
        ])

        assert provider.kol_opinions(KOL_NAME, "d1") == []

    def test_kol_opinions_are_unavailable_until_every_candidate_has_all_v2_fields(self, provider):
        add_comments(provider, [
            {"comment_id": 100, "content": "第一条", "author_name": KOL_NAME},
            {"comment_id": 101, "content": "第二条", "author_name": KOL_NAME},
        ])
        add_annotations(provider, [
            {"annotation_id": 950, "target_id": 100, "kind": "kol_summary", "value": "第一条观点"},
            {"annotation_id": 951, "target_id": 100, "kind": "kol_action", "value": "观望"},
            {"annotation_id": 952, "target_id": 100, "kind": "post_type", "value": "qa"},
            {"annotation_id": 953, "target_id": 101, "kind": "kol_summary", "value": False},
            {"annotation_id": 954, "target_id": 101, "kind": "kol_action", "value": "未提及操作"},
        ])

        assert provider.kol_opinions(KOL_NAME, "d1") is None
