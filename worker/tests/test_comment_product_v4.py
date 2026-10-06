"""v4 keeps product feedback, market talk and issuer opinion separate."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai import schemas  # noqa: E402
from ai.prompts import get as get_prompt  # noqa: E402
from jobs.annotate import _kinds_for  # noqa: E402


def result(**overrides):
    row = {
        "item_id": "comment:1|product:3037",
        "relevance": "irrelevant",
        "attitude": None,
        "aspects": [],
        "evidence": None,
        "market_direction": None,
        "compliance_tags": [],
        "compliance_rationale": None,
        "compliance_evidence": None,
        "issuer_relevance": "irrelevant",
        "issuer_attitude": None,
        "issuer_aspects": [],
        "issuer_evidence": None,
        "needs_review": False,
        "uncertainty_reasons": [],
    }
    row.update(overrides)
    return row


def values_by_kind(item):
    return {
        kind: value
        for kind, value, _spans, _expect in _kinds_for(
            "comment_product", item, {}, "v3"
        )
    }


def test_market_direction_is_retained_but_not_product_feedback():
    item = schemas.parse(
        "comment_product",
        result(market_direction="bearish"),
        "v3",
    )

    values = values_by_kind(item)

    assert values["feedback_eligible"] is False
    assert values["market_direction"] == "bearish"


def test_fee_feedback_is_eligible_and_evidence_bound():
    item = schemas.parse(
        "comment_product",
        result(
            relevance="relevant",
            attitude="negative",
            aspects=["fee"],
            evidence="管理费太贵",
        ),
        "v3",
    )

    assert values_by_kind(item)["feedback_eligible"] is True


def test_pure_trading_call_is_not_product_feedback():
    item = schemas.parse(
        "comment_product",
        result(
            relevance="relevant",
            attitude="positive",
            aspects=["trading_intent"],
            evidence="今日加仓3037",
        ),
        "v3",
    )

    assert values_by_kind(item)["feedback_eligible"] is False


def test_company_opinion_is_independent_from_etf_attitude():
    item = schemas.parse(
        "comment_product",
        result(
            issuer_relevance="relevant",
            issuer_attitude="negative",
            issuer_aspects=["service", "disclosure"],
            issuer_evidence="南方东英客服和披露都很慢",
        ),
        "v3",
    )
    values = values_by_kind(item)

    assert values["relevance"] == "irrelevant"
    assert values["feedback_eligible"] is False
    assert values["issuer_relevance"] == "relevant"
    assert values["issuer_attitude"] == "negative"
    assert values["issuer_aspect"] == ["service", "disclosure"]


def test_v4_prompt_explicitly_lists_market_exclusions_and_issuer_split():
    system = get_prompt("comment_product", "comment-product-v4").SYSTEM

    assert "只谈涨跌、指数方向、宏观、成分股" in system
    assert "纯买卖喊单" in system
    assert "发行商观点（独立于单只 ETF）" in system

