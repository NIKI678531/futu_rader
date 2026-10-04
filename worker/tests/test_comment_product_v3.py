"""comment-product-v3 relevance/context contract tests."""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai import config, redact, schemas  # noqa: E402
from ai.prompts import (  # noqa: E402
    get as get_prompt,
    requires_full_reannotation,
    schema_version_for,
)
from jobs import annotate  # noqa: E402


def _v2_result(**over):
    row = {
        "item_id": "comment:1|product:3037",
        "relevance": "needs_context",
        "attitude": None,
        "aspects": [],
        "evidence": None,
        "market_direction": None,
        "compliance_tags": [],
        "compliance_rationale": None,
        "compliance_evidence": None,
        "needs_review": True,
        "uncertainty_reasons": ["三层父回复仍无法确定指代"],
    }
    row.update(over)
    return row


def test_comment_product_v3_is_default_and_reuses_strict_v2_shape():
    prompt = get_prompt("comment_product")

    assert prompt.VERSION == "comment-product-v3"
    assert schema_version_for(prompt) == "v2"
    wire = schemas.batch_json_schema("comment_product", "v2")
    item = wire["$defs"]["CommentAnnotationV2"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"])


def test_v3_requires_full_reannotation_even_when_gap_fill_is_enabled():
    assert requires_full_reannotation("comment_product", "comment-product-v3") is True
    assert requires_full_reannotation("comment_product", "comment-product-v2") is False


def test_v3_comment_jobs_bypass_the_unvalidated_local_student():
    cfg = config.load(
        _allow_missing_key=True,
        model="test-model",
        prompt_version="comment-product-v3",
        schema_version="v2",
        taxonomy_version="v2",
    )
    prompt = get_prompt("comment_product", cfg.prompt_version)
    row = SimpleNamespace(
        comment_id=1,
        code="3037",
        content="$03037.HK$ 点差太大",
        title="$恒生指数ETF (03037.HK)$",
        post_content="讨论产品",
        parent_content=None,
        grandparent_content=None,
        great_grandparent_content=None,
    )

    job = annotate.job_row_for_comment(cfg, prompt, "v2", row)

    assert job["stage"] == annotate.STAGE_LLM


def test_production_config_defaults_to_v3_prompt_with_v2_wire_schema(monkeypatch):
    monkeypatch.setenv("AI_PRIMARY_MODEL", "test-model")
    monkeypatch.delenv("AI_PROMPT_VERSION", raising=False)
    monkeypatch.delenv("AI_SCHEMA_VERSION", raising=False)
    monkeypatch.delenv("AI_MICRO_BATCH_SIZE", raising=False)

    cfg = config.load(_allow_missing_key=True)

    assert cfg.prompt_version == "comment-product-v3"
    assert cfg.schema_version == "v2"
    assert cfg.micro_batch_size == 5


def test_v3_prompt_separates_direct_identity_from_context_only_terms():
    system = get_prompt("comment_product").SYSTEM

    assert "`code`、`name` 与 `aliases`" in system
    assert "aliases" in system
    assert "family_terms" in system
    assert "underlying_terms" in system
    assert "不能单独证明" in system
    assert "其他 ETF" in system
    assert "$03037.HK$" in system
    assert "恒指" in system and "HSI" in system
    assert "BTC" in system and "ETH" in system


def test_v3_prompt_limits_parent_chain_and_post_context_to_disambiguation():
    system = get_prompt("comment_product").SYSTEM

    assert "parent_comments" in system
    assert "最多三层" in system
    assert "由近到远" in system
    assert "只用于消歧" in system
    assert "needs_context" in system


def test_three_parent_levels_are_scrubbed_and_a_fourth_is_not_sent():
    payload = redact.comment_payload(
        "comment:1|product:3037",
        {
            "code": "3037",
            "name": "恒生指數ETF",
            "aliases": ["南方恒指"],
            "family_terms": ["恒指", "HSI"],
            "underlying_terms": [],
        },
        "佢點解咁？",
        parent_comments=[
            "@甲 呢隻跟得差",
            "見 https://example.test/private",
            "$03037.HK$ 今日有折價",
            "第四层不应外发",
        ],
    )

    assert payload["parent_comments"] == [
        "@[用户] 呢隻跟得差",
        "見 [链接]",
        "$03037.HK$ 今日有折價",
    ]
    assert set(payload["product"]) == {
        "code",
        "name",
        "aliases",
        "family_terms",
        "underlying_terms",
    }


def test_v3_context_terms_do_not_expand_unrelated_post_payloads():
    payload = redact.post_payload(
        "post:1",
        "正文",
        product={
            "code": "3037",
            "name": "恒生指數ETF",
            "aliases": ["南方恒指"],
            "family_terms": ["恒指", "HSI"],
            "underlying_terms": [],
        },
    )

    assert set(payload["product"]) == {"code", "name", "aliases"}


def test_needs_context_survives_strict_batch_parsing():
    item_id = "comment:1|product:3037"
    parsed = schemas.parse_batch(
        "comment_product",
        {"results": [_v2_result()]},
        [item_id],
        "v2",
    )

    assert parsed[item_id].relevance == "needs_context"
    assert parsed[item_id].needs_review is True
    assert parsed[item_id].uncertainty_reasons == ["三层父回复仍无法确定指代"]


@pytest.mark.parametrize(
    "bad",
    [
        {"attitude": "neutral"},
        {"aspects": ["performance"]},
        {"evidence": "佢"},
    ],
)
def test_needs_context_cannot_smuggle_a_product_judgement(bad):
    with pytest.raises(schemas.SchemaError):
        schemas.parse("comment_product", _v2_result(**bad), "v2")
