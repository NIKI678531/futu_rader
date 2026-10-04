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
from radar_db.comment_filter import FilterPolicy  # noqa: E402


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
    assert EXACT_RULE_VERSION == "parent-feed-v1"


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
