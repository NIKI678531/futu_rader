"""Worker integration tests for parent-feed qualification."""

import json
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from collection.exact import (  # noqa: E402
    EXACT_RULE_VERSION,
    ExactProductMatcher,
    extract_futu_cashtags,
    normalize_futu_ticker,
    route_exact_comment,
    route_parent_feed,
)
from collection.normalization import normalize_feed  # noqa: E402
from radar_db.comment_filter import FilterPolicy, extract_feed_mentions  # noqa: E402


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("03037.HK", "3037"),
        ("3037.hk", "3037"),
        ("NVDA.US", "us:NVDA"),
        ("BRK.B.US", "us:BRK.B"),
        ("not a ticker", None),
    ],
)
def test_normalize_futu_ticker_uses_project_code_format(raw, expected):
    assert normalize_futu_ticker(raw) == expected


def test_extract_futu_cashtags_accepts_strict_futu_forms_in_order():
    text = "$03037.HK$ / $产品名 (03037.HK)$ / $產品名（07226.HK）$ / $NVDA.US$"
    assert extract_futu_cashtags(text) == ("3037", "3037", "7226", "us:NVDA")


def test_strict_cashtag_keeps_the_platform_display_name():
    mentions = extract_feed_mentions(
        None,
        "$易方達亞太高股息ETF (03483.HK)$ / $03456.HK$",
    )

    assert [(item.raw_ticker, item.display_name) for item in mentions] == [
        ("03483.HK", "易方達亞太高股息ETF"),
        ("03456.HK", None),
    ]


@pytest.mark.parametrize(
    "text",
    ["$03037$", "$产品名 (03037)$", "$50、普通文本$", "$03037.HK extra$"],
)
def test_extract_futu_cashtags_rejects_non_ticker_dollar_text(text):
    assert extract_futu_cashtags(text) == ()


def test_normalize_feed_builds_filter_mentions_only_from_parent_title_and_content():
    row = {
        "feed_id": 1,
        "stock_id": 1,
        "feed_type": 1,
        "posted_at": datetime(2026, 9, 20, 2, 0),
        "feed_title": "$恒生指數ETF (03037.HK)$",
        "content_text": "比较 $07226.HK$",
        "like_count": 0,
        "comment_count": 1,
        "image_count": 0,
        "raw_json": json.dumps(
            {
                "comment": {
                    "comment_items": [{"comment_id": 9, "content": "回复 $09999.HK$"}],
                    "has_more": False,
                },
                "summary": {
                    "rich_text": [
                        {"stock": {"stock_code": "09999", "market_type_label": "HK"}}
                    ]
                },
            }
        ),
    }

    observation = normalize_feed(
        row, "3037", datetime(2026, 9, 20, 3, 0), source_ticker="HK.03037"
    )

    assert observation.feed["source_ticker"] == "03037.HK"
    assert {(m["code"], m["source"]) for m in observation.mentions} >= {
        ("3037", "anchor"),
        ("3037", "body"),
        ("7226", "body"),
        ("9999", "body"),
    }
    assert {(m["raw_ticker"], m["occurrences"]) for m in observation.feed_mentions} == {
        ("03037.HK", 1),
        ("07226.HK", 1),
    }

    unresolved = normalize_feed(row, "3037", datetime(2026, 9, 20, 3, 0))
    assert unresolved.feed["source_ticker"] is None


def test_normalize_feed_retains_instrument_type_for_strict_etf_display():
    row = {
        "feed_id": 2,
        "feed_type": 1,
        "posted_at": datetime(2026, 10, 21, 15, 26),
        "feed_title": None,
        "content_text": None,
        "like_count": 0,
        "comment_count": 0,
        "image_count": 0,
        "raw_json": {
            "comment": {"comment_items": [], "has_more": False},
            "summary": {"rich_text": [
                {"stock": {"stock_code": "QQQ", "market_type_label": "US",
                           "display_symbol": "QQQ.US", "stock_name": "Invesco QQQ Trust",
                           "instrument_type_v2": 4, "sub_instrument_type_v2": 4001}},
                {"stock": {"stock_code": "800000", "market_type_label": "HK",
                           "display_symbol": "800000.HK", "stock_name": "恒生指数",
                           "instrument_type_v2": 6, "sub_instrument_type_v2": 6001}},
                {"stock": {"stock_code": "68882", "market_type_label": "HK",
                           "display_symbol": "68882.HK", "stock_name": "恒指瑞银九三熊N.P",
                           "instrument_type_v2": 5, "sub_instrument_type_v2": 5003}},
            ]},
        },
    }

    observation = normalize_feed(row, "3037", datetime(2026, 10, 21, 16, 0))
    body = {m["code"]: m for m in observation.mentions if m["source"] == "body"}

    assert body["us:QQQ"]["display_symbol"] == "QQQ.US"
    assert body["us:QQQ"]["instrument_type_v2"] == 4
    assert body["800000"]["instrument_type_v2"] == 6
    assert body["68882"]["instrument_type_v2"] == 5


def test_full_article_etfs_are_identified_beyond_the_abbreviated_summary():
    row = {
        "feed_id": 117479595180036,
        "feed_type": 1,
        "posted_at": datetime(2026, 10, 21, 15, 26),
        "feed_title": "AI是否已經走到了盡頭？- 10月12至20日覆盤",
        "content_text": (
            "$恒生指數 (800000.HK)$ $恒指瑞銀九三熊N.P (68882.HK)$ "
            "$騰訊控股 (00700.HK)$ "
            "$易方達亞太高股息ETF (03483.HK)$ "
            "$易方達港交所科技100 ETF (03456.HK)$"
        ),
        "like_count": 0,
        "comment_count": 0,
        "image_count": 0,
        "raw_json": {
            "comment": {"comment_items": [], "has_more": False},
            # The real source row marks this abbreviated prefix incomplete and
            # carries structured identity only for these first two products.
            "summary": {"is_complete": False, "rich_text": [
                {"stock": {"stock_code": "800000", "market_type_label": "HK",
                           "display_symbol": "800000.HK", "stock_name": "恒生指数",
                           "instrument_type_v2": 6, "sub_instrument_type_v2": 6001}},
                {"stock": {"stock_code": "68882", "market_type_label": "HK",
                           "display_symbol": "68882.HK", "stock_name": "恒指瑞银九三熊N.P",
                           "instrument_type_v2": 5, "sub_instrument_type_v2": 5003}},
            ]},
        },
    }
    catalog = {
        "3483": {
            "display_symbol": "03483.HK",
            "display_name": "易方達亞太高股息ETF",
            "is_etf": True,
        },
        "700": {
            "display_symbol": "00700.HK",
            "display_name": "騰訊控股",
            "is_etf": False,
        },
    }

    observation = normalize_feed(
        row,
        "3037",
        datetime(2026, 10, 22, 10, 30),
        instrument_catalog=catalog,
    )
    body = {m["code"]: m for m in observation.mentions if m["source"] == "body"}

    assert body["3483"]["instrument_type_v2"] == 4
    assert body["3483"]["display_name"] == "易方達亞太高股息ETF"
    assert body["3456"]["instrument_type_v2"] == 4
    assert body["3456"]["display_name"] == "易方達港交所科技100 ETF"
    assert body["800000"]["instrument_type_v2"] == 6
    assert body["68882"]["instrument_type_v2"] == 5
    assert body["700"]["instrument_type_v2"] is None


def test_exact_parent_qualifies_and_all_replies_inherit_anchor():
    policy = FilterPolicy(mode="exact")
    route = route_parent_feed(
        anchor_code="3033",
        source_ticker="03033.HK",
        mentioned_tickers=("03033.HK", "03032.HK"),
        policy=policy,
    )
    assert route.subject_codes == ("3033",)
    assert route.anchor_rejected is False
    assert route.reason == "parent_exact_qualified"
    assert EXACT_RULE_VERSION == "parent-feed-v3"


def test_reply_ticker_cannot_reroute_a_qualified_parent():
    route = route_exact_comment(
        "$03032.HK$ 更值得买",
        anchor_code="3033",
        parent_body_codes=("3033",),
        matcher=ExactProductMatcher({"3032": (), "3033": ()}),
        policy=FilterPolicy(mode="exact"),
    )
    assert route.subject_codes == ("3033",)
    assert route.anchor_rejected is False


def test_reply_ticker_and_product_name_cannot_rescue_unqualified_parent():
    matcher = ExactProductMatcher({"3033": ("恒生科技指数ETF",)})
    for text in ("$03033.HK$ 的费率太高", "恒生科技指数ETF 的费率太高"):
        route = route_exact_comment(
            text,
            anchor_code="3033",
            parent_body_codes=(),
            matcher=matcher,
            policy=FilterPolicy(mode="exact"),
        )
        assert route.subject_codes == ()
        assert route.anchor_rejected is True
        assert route.reason == "parent_exact_filtered"


def test_exclude_policy_is_parent_scoped_and_self_mention_wins():
    policy = FilterPolicy(mode="exclude", exclude_tickers=("800000.HK",))
    open_route = route_parent_feed(
        anchor_code="3037",
        source_ticker="03037.HK",
        mentioned_tickers=(),
        policy=policy,
    )
    dropped = route_parent_feed(
        anchor_code="3037",
        source_ticker="03037.HK",
        mentioned_tickers=("800000.hk",),
        policy=policy,
    )
    self_wins = route_parent_feed(
        anchor_code="3037",
        source_ticker="03037.HK",
        mentioned_tickers=("800000.HK", "03037.HK"),
        policy=policy,
    )
    assert open_route.subject_codes == ("3037",)
    assert dropped.subject_codes == ()
    assert self_wins.subject_codes == ("3037",)


def test_missing_source_ticker_fails_closed():
    route = route_parent_feed(
        anchor_code="3033",
        source_ticker=None,
        mentioned_tickers=("03033.HK",),
        policy=FilterPolicy(mode="exact"),
    )
    assert route.subject_codes == ()


def test_compatibility_matcher_does_not_match_plain_product_names():
    matcher = ExactProductMatcher({"3033": ("恒生科技指数ETF",)})
    assert matcher.codes_in("恒生科技指数ETF 手续费太高") == ()
    assert matcher.codes_in("$03033.HK$ 手续费太高") == ("3033",)
