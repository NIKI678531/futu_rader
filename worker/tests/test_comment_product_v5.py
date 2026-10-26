"""v5 narrows 舆情 to strict high-risk negative comments."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai import schemas  # noqa: E402
from ai.prompts import get as get_prompt, schema_version_for  # noqa: E402
from jobs.annotate import _kinds_for  # noqa: E402


def result(**overrides):
    row = {
        "item_id": "comment:1|product:3037",
        "relevance": "relevant",
        "attitude": "negative",
        "aspects": ["issuer_service"],
        "evidence": "这个产品真差",
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


def test_v5_is_default_and_uses_v4_wire_schema():
    prompt = get_prompt("comment_product")
    assert prompt.VERSION == "comment-product-v5"
    assert schema_version_for(prompt) == "v4"
    tags = schemas.batch_json_schema("comment_product", "v4")["$defs"]["CommentAnnotationV4"]["properties"]["compliance_tags"]
    assert "abusive_attack" in str(tags)
    assert "compliance_concern" not in str(tags)


def test_negative_product_abuse_is_a_valid_high_risk_signal():
    item = schemas.parse(
        "comment_product",
        result(
            compliance_tags=["abusive_attack"],
            compliance_rationale="直接辱骂当前产品",
            compliance_evidence="这个产品真是垃圾",
        ),
        "v4",
    )
    kinds = {kind: value for kind, value, _spans, _expect in _kinds_for("comment_product", item, {}, "v4")}
    assert kinds["compliance"]["tags"] == ["abusive_attack"]


def test_non_negative_comment_cannot_emit_a_high_risk_signal():
    with pytest.raises(schemas.SchemaError, match="高风险标签"):
        schemas.parse(
            "comment_product",
            result(
                attitude="neutral",
                compliance_tags=["regulatory_complaint"],
                compliance_rationale="提到监管投诉",
                compliance_evidence="我已经向 SFC 投诉",
            ),
            "v4",
        )


def test_negative_issuer_abuse_is_valid_even_when_product_is_irrelevant():
    parsed = schemas.parse(
        "comment_product",
        result(
            relevance="irrelevant",
            attitude=None,
            aspects=[],
            evidence=None,
            issuer_relevance="relevant",
            issuer_attitude="negative",
            issuer_aspects=["service"],
            issuer_evidence="南方东英就是骗子公司",
            compliance_tags=["abusive_attack"],
            compliance_rationale="直接辱骂发行商",
            compliance_evidence="南方东英就是骗子公司",
        ),
        "v4",
    )
    assert parsed.compliance_tags == ["abusive_attack"]


def test_v5_prompt_excludes_ordinary_complaints_and_parent_posts():
    system = get_prompt("comment_product", "comment-product-v5").SYSTEM
    assert "普通费率、点差、跟踪误差" in system
    assert "不得输出" in system
    assert "不要把父帖观点" in system
