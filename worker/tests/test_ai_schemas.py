"""`ai/schemas.py` —— 结构化输出的唯一定义。

两组关注点：

**一、发出去的 Schema 和本地校验不许漂移。** 抄两份的话第一天一致、第三天不一致，
表现是整批标注莫名其妙全进 dead-letter，或者更糟 —— 本地更松，多出来的字段被静默丢弃。
这里的断言直接盯着「wire schema 的字段集 == Pydantic 模型的字段集」。

**二、跨字段规则不许被绕过。** `relevance=irrelevant` 配一个 `attitude`、
`direction` 配 `direction_pending`，都是**合法 JSON**。Schema 拦不住它们，
只有 validator 能。而它们一旦落库，界面上看不出任何异常。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from ai import schemas  # noqa: E402
from ai.schemas import IdSetMismatch, SchemaError  # noqa: E402
from radar_db.schema import NO_SUBJECT as DB_NO_SUBJECT  # noqa: E402

TASKS = ("comment_product", "post_annotation")


def good_comment(**over):
    base = {
        "item_id": "comment:1|product:3033",
        "relevance": "relevant",
        "attitude": "negative",
        "aspects": ["spread"],
        "evidence": "点差太大",
        "needs_review": False,
        "uncertainty_reasons": [],
    }
    base.update(over)
    return base


def good_post(**over):
    base = {
        "item_id": "feed:1",
        "post_type": "action",
        "direction": "add",
        "direction_pending": False,
        "summary": "作者宣布加仓该 ETF",
        "evidence_spans": ["今天又加了一手"],
        "needs_review": False,
    }
    base.update(over)
    return base


# ── 哨兵值一致性 ───────────────────────────────────────────────────────


def test_no_subject_sentinel_matches_the_database():
    """两处必须是同一个值：SQL 唯一约束依赖它。NULL 在唯一约束里互不相等，
    用 NULL 表示「不针对产品」会让幂等彻底失效。"""
    assert schemas.NO_SUBJECT == DB_NO_SUBJECT == ""


# ── wire schema 的 strict 合规性 ───────────────────────────────────────


def walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


@pytest.mark.parametrize("task", TASKS)
def test_every_object_is_closed_and_requires_all_properties(task):
    """strict 模式的硬性要求。少一个 `additionalProperties: false` 就是 400，
    而 400 的报错信息不会告诉你是哪个嵌套对象。"""
    for node in walk(schemas.batch_json_schema(task)):
        if node.get("type") == "object" or "properties" in node:
            assert node.get("additionalProperties") is False
            assert set(node.get("required", [])) == set(node.get("properties", {}))


@pytest.mark.parametrize("task", TASKS)
def test_unsupported_keywords_are_stripped(task):
    """`default` 尤其危险：它会让模型以为字段可以不给，而 strict 又要求全给。"""
    for node in walk(schemas.batch_json_schema(task)):
        assert not (schemas._UNSUPPORTED & set(node))


@pytest.mark.parametrize("task", TASKS)
def test_wire_schema_fields_match_the_validating_model(task):
    """漂移检测。发出去的字段集和本地校验的字段集必须逐字相同。"""
    wire = schemas.json_schema(task)
    assert set(wire["properties"]) == set(schemas.TASKS[task].model_fields)


def test_refs_survive_strictification():
    """批信封靠 `$defs`/`$ref` 引用条目类型。`_strictify` 若把 `$ref` 节点当普通对象
    补上 `required: []`，网关会拒绝整个 schema。实测这个网关接受带 $ref 的 strict schema。"""
    schema = schemas.batch_json_schema("comment_product")
    assert "$defs" in schema
    assert schema["properties"]["results"]["items"]["$ref"].startswith("#/$defs/")


# ── 跨字段规则（runbook §11.1、§11.2） ─────────────────────────────────


def test_irrelevant_must_not_carry_an_attitude():
    """`neutral` 表示「评价了，但态度中性」，与「没在评价这只产品」是两件事。
    混淆它们会让情绪净值的分母混进一批根本没在谈这只产品的评论。"""
    with pytest.raises(SchemaError):
        schemas.parse("comment_product",
                      good_comment(relevance="irrelevant", attitude="neutral"))


def test_needs_context_must_not_carry_an_attitude():
    with pytest.raises(SchemaError):
        schemas.parse("comment_product",
                      good_comment(relevance="needs_context", attitude="positive"))


def test_relevant_must_carry_an_attitude():
    with pytest.raises(SchemaError):
        schemas.parse("comment_product", good_comment(attitude=None))


def test_irrelevant_with_null_attitude_is_fine():
    got = schemas.parse("comment_product",
                        good_comment(relevance="irrelevant", attitude=None,
                                     aspects=[], evidence=None))
    assert got.attitude is None


def test_direction_and_pending_are_mutually_exclusive():
    """判出来了就不是「待确认」。两者同时为真会让界面同时显示方向和待确认徽章。"""
    with pytest.raises(SchemaError):
        schemas.parse("post_annotation", good_post(direction="add", direction_pending=True))


def test_direction_pending_without_direction_is_fine():
    got = schemas.parse("post_annotation", good_post(direction=None, direction_pending=True))
    assert got.direction is None and got.direction_pending


def test_summary_over_60_chars_fails_instead_of_truncating():
    """截断会把一句话砍成半句，而半句摘要在界面上看不出是被截的。"""
    with pytest.raises(SchemaError):
        schemas.parse("post_annotation", good_post(summary="产" * 61))
    assert schemas.parse("post_annotation", good_post(summary="产" * 60))


# ── 枚举与多余字段 ─────────────────────────────────────────────────────


def test_value_outside_the_frozen_enum_is_rejected_not_coerced():
    """把「加仓意向」归到 `add` 是在替产品改口径。枚举来自 PRD 冻结的 TYPE_RULE。"""
    with pytest.raises(SchemaError):
        schemas.parse("post_annotation", good_post(post_type="加仓意向"))
    with pytest.raises(SchemaError):
        schemas.parse("comment_product", good_comment(attitude="bullish"))
    with pytest.raises(SchemaError):
        schemas.parse("comment_product", good_comment(aspects=["expense_ratio"]))


def test_extra_field_is_rejected():
    with pytest.raises(SchemaError):
        schemas.parse("comment_product", good_comment(confidence=0.93))


def test_frozen_enums_match_prd():
    """逐字来自 design/radar-data.js 的 POST_TYPES / DIRECTIONS（PRD §4.3 冻结）。"""
    assert schemas.POST_TYPES == (
        "showcase", "action", "market", "promo", "edu", "event", "qa", "other")
    assert schemas.DIRECTIONS == ("add", "open", "reduce", "close", "hold")


# ── 批 id 集合（最危险的一类失败） ─────────────────────────────────────


def test_batch_aligns_by_item_id_never_by_position():
    """错位是静默的：按顺序对齐会把 A 产品的态度写到 B 产品上，而库里每一行看起来都正常。"""
    ids = ["comment:1|product:3033", "comment:2|product:2822"]
    raw = {"results": [good_comment(item_id=ids[1], attitude="positive"),
                       good_comment(item_id=ids[0], attitude="negative")]}
    by_id = schemas.parse_batch("comment_product", raw, ids)
    assert by_id[ids[0]].attitude == "negative"
    assert by_id[ids[1]].attitude == "positive"


def test_missing_item_fails_the_whole_batch():
    with pytest.raises(IdSetMismatch, match="缺少"):
        schemas.parse_batch("comment_product", {"results": [good_comment()]},
                            ["comment:1|product:3033", "comment:2|product:3033"])


def test_hallucinated_item_fails_the_whole_batch():
    with pytest.raises(IdSetMismatch, match="多出"):
        schemas.parse_batch(
            "comment_product",
            {"results": [good_comment(), good_comment(item_id="comment:999|product:3033")]},
            ["comment:1|product:3033"],
        )


def test_duplicate_item_id_fails():
    with pytest.raises(IdSetMismatch, match="重复"):
        schemas.parse_batch("comment_product",
                            {"results": [good_comment(), good_comment()]},
                            ["comment:1|product:3033"])


def test_one_bad_item_fails_the_batch_rather_than_being_dropped():
    """静默丢掉坏条目 = 那条评论永远不会被标注，而队列显示它已完成。"""
    ids = ["comment:1|product:3033", "comment:2|product:3033"]
    raw = {"results": [good_comment(item_id=ids[0]),
                       good_comment(item_id=ids[1], relevance="irrelevant")]}
    with pytest.raises(SchemaError):
        schemas.parse_batch("comment_product", raw, ids)


# ── 输入指纹（runbook §11.3） ──────────────────────────────────────────


VERSIONS = dict(model="m", prompt_version="p", taxonomy_version="t", schema_version="s")


def test_hash_ignores_dict_key_order():
    """否则每次重排字段都会把全库判成「输入变了」而重刷一遍。"""
    a = schemas.input_hash({"item_id": "x", "comment": "y"}, **VERSIONS)
    b = schemas.input_hash({"comment": "y", "item_id": "x"}, **VERSIONS)
    assert a == b


@pytest.mark.parametrize("field", list(VERSIONS))
def test_hash_changes_when_any_version_changes(field):
    """少任何一项，换了 Prompt 之后重跑会被判成重复而跳过 —— 那正是
    「改了 Prompt 但结果没变」这类查不出原因的 bug。"""
    base = schemas.input_hash({"item_id": "x"}, **VERSIONS)
    bumped = schemas.input_hash({"item_id": "x"}, **{**VERSIONS, field: "changed"})
    assert base != bumped


def test_hash_changes_when_the_text_changes():
    assert (schemas.input_hash({"comment": "点差大"}, **VERSIONS)
            != schemas.input_hash({"comment": "点差小"}, **VERSIONS))
