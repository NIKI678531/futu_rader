"""`ai/prefilter.py` —— 五条规则各自的正例与**反例**（不许误杀）。"""

import json
import os
import sys
from datetime import datetime, timedelta

import pytest
from sqlalchemy import insert, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from ai import prefilter  # noqa: E402
from ai.lexicon import offpool_stocks, product_aliases  # noqa: E402
from radar_db import create_all, make_engine  # noqa: E402
from radar_db.schema import annotation_runs, annotations, meta_kv  # noqa: E402


@pytest.fixture(scope="module")
def plex():
    return product_aliases.ProductLexicon()


@pytest.fixture(scope="module")
def slex():
    return offpool_stocks.StockLexicon()


@pytest.fixture()
def pf(plex, slex):
    return prefilter.Prefilter(plex, slex)


def cls(pf, text, code="3033", cid=1, uid="u1", fid=100):
    return pf.classify(text, code, comment_id=cid, author_uid=uid, feed_id=fid)


@pytest.mark.parametrize("text", ["", "   ", None, "\n\t"])
def test_empty(pf, text):
    assert cls(pf, text).rule == "empty"


@pytest.mark.parametrize("text", ["[捂脸][捂脸]", "[表情]", "😂😂😂", "666", "？？？", "。。。", "👍🏽"])
def test_sticker_only(pf, text):
    assert cls(pf, text).rule == "sticker_only"


@pytest.mark.parametrize("text", ["$00700.HK$", "$03033.HK$ $00700.HK$", "$NVDA.US$ 👍", "@[用户] $00700.HK$"])
def test_tag_only(pf, text):
    d = cls(pf, text)
    assert d.rule == "tag_only"
    assert d.detail["tags"]


def test_exact_duplicate_keeps_first_only(pf):
    a = pf.classify("呢隻可以入", "3033", comment_id=1, author_uid="u1", feed_id=9)
    b = pf.classify("呢隻可以入 ", "3033", comment_id=2, author_uid="u1", feed_id=9)
    c = pf.classify("呢隻可以入", "3033", comment_id=3, author_uid="u2", feed_id=9)  # 别的作者
    d = pf.classify("呢隻可以入", "3033", comment_id=4, author_uid="u1", feed_id=10)  # 别的帖
    assert a.rule is None
    assert b.rule == "exact_duplicate" and b.detail["kept_comment_id"] == 1
    assert c.rule is None
    assert d.rule is None


def test_same_comment_classified_twice_is_not_its_own_duplicate(pf):
    assert pf.classify("呢隻可以入", "3033", comment_id=1, author_uid="u1", feed_id=9).rule is None
    assert pf.classify("呢隻可以入", "3033", comment_id=1, author_uid="u1", feed_id=9).rule is None


def test_unknown_author_is_never_a_duplicate(pf):
    assert pf.classify("同意", "3033", comment_id=1, author_uid=None, feed_id=9).rule is None
    assert pf.classify("同意", "3033", comment_id=2, author_uid=None, feed_id=9).rule is None


@pytest.mark.parametrize(
    "text",
    [
        "$00700.HK$ 今日爆升",
        "騰訊業績好過預期",
        "腾讯涨了，美团也涨了",
        "Tencent beat, Meta beat",
        "阿里 9988 值得入",
    ],
)
def test_offpool_stock_only_dropped(pf, text):
    d = cls(pf, text)
    assert d.rule == "offpool_stock_only", text
    assert d.detail["stocks"]


@pytest.mark.parametrize(
    "text",
    [
        "騰訊拖累呢隻",                  # 指代 ETF
        "腾讯涨了 3033 却不动",           # 点名 ETF
        "騰訊升但恒科跌",                # 族叫法
        "騰訊業績好，加倉",              # 交易动作词
        "騰訊拖累，這個 ETF 費率又貴",   # 产品属性词
        "$03033.HK$ 騰訊佈局",           # 本产品标签
        "今日大市好淡",                  # 没有个股
        "有",                            # 短回复：无个股 ⇒ 放行交模型配父评论判
    ],
)
def test_offpool_stock_only_not_dropped(pf, text):
    assert cls(pf, text).rule is None, text


def test_underlying_stock_is_not_offpool_for_single_stock_products(pf):
    # 7788 是英偉達兩倍：讨论区里聊英偉達是在聊标的，不是跑题。
    assert pf.classify("英偉達業績好", "7788", comment_id=1).rule is None
    # 同一句话放到 3033 下：英偉達不在静态个股表，也放行 —— 交给模型。
    assert pf.classify("英偉達業績好", "3033", comment_id=2).rule is None


def test_offpool_switch_off(plex, slex):
    pf = prefilter.Prefilter(plex, slex, drop_offpool=False)
    assert cls(pf, "騰訊業績好過預期").rule is None


# ── 落库 ───────────────────────────────────────────────────────────────


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "t.db").as_posix())
    create_all(eng)
    return eng


def test_write_rule_annotations_is_idempotent_and_traceable(engine, pf):
    now = datetime(2026, 9, 1, 12, 0)
    prefilter.open_rule_run(engine, "run-rule-1", "comment_product", now,
                            taxonomy_version="v2", schema_version="v2")
    d = cls(pf, "騰訊業績好過預期")
    decisions = [(11, "3033", "騰訊業績好過預期", d)]
    assert prefilter.write_rule_annotations(engine, "run-rule-1", decisions, now) == 1
    assert prefilter.write_rule_annotations(engine, "run-rule-1", decisions, now) == 0

    with engine.connect() as conn:
        rows = conn.execute(select(annotations).order_by(annotations.c.annotation_id)).mappings().all()
        run = conn.execute(select(annotation_runs)).mappings().one()
    assert [r["kind"] for r in rows] == ["relevance", "text_quality"]
    assert rows[0]["value_json"] == '"irrelevant"'
    assert '"rule": "offpool_stock_only"' in rows[1]["value_json"]
    assert run["provider"] == "rule"
    # 规则不花 token：三列是 NULL，不是 0。
    assert run["token_input"] is None and run["token_output"] is None


def test_rule_row_does_not_override_human_settled(engine, pf):
    now = datetime(2026, 9, 1, 12, 0)
    with engine.begin() as conn:
        conn.execute(
            insert(annotations).values(
                target_type="comment", target_id=11, subject_code="3033", kind="relevance",
                value_json='"relevant"', run_id="r0", input_hash="x", review_state="approved",
                created_at=now,
            )
        )
    prefilter.open_rule_run(engine, "run-rule-2", "comment_product", now,
                            taxonomy_version="v2", schema_version="v2")
    d = cls(pf, "騰訊業績好過預期")
    prefilter.write_rule_annotations(engine, "run-rule-2", [(11, "3033", "騰訊業績好過預期", d)], now)
    with engine.connect() as conn:
        new = conn.execute(
            select(annotations).where(annotations.c.run_id == "run-rule-2",
                                      annotations.c.kind == "relevance")
        ).mappings().one()
    assert new["supersedes_id"] is None  # 人看过的旧行不被顶掉


def test_rule_can_become_current_again_after_an_llm_result(engine, pf):
    """同一 exact 上下文来回出现时，历史 hash 不能挡住新的现行规则结论。"""

    first_at = datetime(2026, 9, 1, 12, 0)
    second_at = first_at + timedelta(seconds=1)
    third_at = second_at + timedelta(seconds=1)
    decision = prefilter.Decision(
        "exact_parent_mismatch",
        {
            "reason": "parent_missing_target",
            "matched_tickers": ["3032"],
            "rule_version": "exact-v1",
        },
    )
    prefilter.open_rule_run(
        engine,
        "run-rule-first",
        "comment_product",
        first_at,
        taxonomy_version="v2",
        schema_version="v2",
    )
    assert prefilter.write_rule_annotations(
        engine, "run-rule-first", [(11, "3033", "手续费太高", decision)], first_at
    ) == 1

    with engine.begin() as conn:
        first_rule_id = conn.execute(
            select(annotations.c.annotation_id).where(
                annotations.c.run_id == "run-rule-first",
                annotations.c.kind == "relevance",
            )
        ).scalar_one()
        conn.execute(insert(annotation_runs).values(
            run_id="run-llm",
            task="comment_product",
            provider="openai_compatible",
            model_id="m",
            prompt_version="comment-product-v3",
            taxonomy_version="v2",
            schema_version="v2",
            started_at=second_at,
            status="done",
        ))
        result = conn.execute(insert(annotations).values(
            target_type="comment",
            target_id=11,
            subject_code="3033",
            kind="relevance",
            value_json='"relevant"',
            run_id="run-llm",
            input_hash="llm-hash",
            review_state="pending",
            created_at=second_at,
            supersedes_id=first_rule_id,
        ))
        llm_id = result.inserted_primary_key[0]

    prefilter.open_rule_run(
        engine,
        "run-rule-third",
        "comment_product",
        third_at,
        taxonomy_version="v2",
        schema_version="v2",
    )
    assert prefilter.write_rule_annotations(
        engine, "run-rule-third", [(11, "3033", "手续费太高", decision)], third_at
    ) == 1

    with engine.connect() as conn:
        newest = conn.execute(
            select(annotations)
            .where(annotations.c.kind == "relevance")
            .order_by(annotations.c.annotation_id.desc())
        ).mappings().first()
        revision = conn.execute(
            select(meta_kv.c.v).where(meta_kv.c.k == "annotation_revision")
        ).scalar_one()
        dirty = conn.execute(
            select(meta_kv.c.v).where(meta_kv.c.k == "synth_dirty_3033_d30")
        ).scalar_one()
    assert newest["run_id"] == "run-rule-third"
    assert newest["supersedes_id"] == llm_id
    assert json.loads(newest["value_json"]) == "irrelevant"
    assert revision
    assert dirty == "1"
