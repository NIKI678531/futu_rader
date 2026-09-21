"""结构化输出的 Schema 与校验 —— 一份定义同时喂给供应商和本地校验。

## 为什么 Schema 不能抄两份

发给供应商的 JSON Schema 和本地校验如果各写一份，它们第一天一致、第三天不一致。
不一致的表现是：供应商按旧 Schema 生成，本地按新 Schema 校验，整批标注全部失败并进
dead-letter —— 或者更糟，本地校验更松，多出来的字段被静默丢弃。这里用 Pydantic 当唯一
定义，wire schema 由 `json_schema()` 从同一个类导出（`test_ai_schemas.py` 有一条断言
专门盯这两者不许漂移）。

## strict 模式的额外要求

供应商的 `strict: true` 要求每个 object 都写 `additionalProperties: false`、且
`required` 必须列出**全部**属性（可选性用 nullable 表达，不用省略字段表达）。Pydantic
默认生成的 schema 不满足这两条，也会带上 strict 模式不支持的 `default`/`maxLength`
等关键字，所以有 `_strictify()` 做一次转换。

## 标签取自 PRD 冻结枚举，不自己造

内容形式 8 类与操作方向 5 类逐字来自 `design/radar-data.js` 的 `POST_TYPES` / `DIRECTIONS`
（PRD §4.3 `TYPE_RULE` 冻结）。模型返回枚举外的值 = 校验失败，不做近似归类 ——
把「加仓意向」归到 `add` 是在替产品改口径。
"""

import copy
import hashlib
import json
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# 帖子级标注不针对某只产品。与 radar_db.schema.NO_SUBJECT 必须一致（那边有 SQL 唯一约束
# 依赖它；NULL 在唯一约束里互不相等，用 NULL 会让幂等失效）。
NO_SUBJECT = ""

# ── PRD 冻结枚举 ─────────────────────────────────────────────────────────

POST_TYPES = ("showcase", "action", "market", "promo", "edu", "event", "qa", "other")
DIRECTIONS = ("add", "open", "reduce", "close", "hold")
RELEVANCE = ("relevant", "irrelevant", "needs_context")
ATTITUDES = ("positive", "neutral", "negative")

# 评论 aspect（runbook §6.5「费用、流动性、跟踪等多标签」、§9 `aspect`）。
ASPECTS = (
    "fee", "liquidity", "spread", "tracking", "dividend",
    "leverage_decay", "performance", "trading_intent", "other",
)


class SchemaError(ValueError):
    """输出不符合约定。**不带默认值兜底** —— runbook §11.3：达到最大次数进 dead-letter，
    不写伪默认值。写一个 `attitude='neutral'` 当兜底，界面上它和真判断长得一模一样。"""


# ── 评论×产品（runbook §11.1） ───────────────────────────────────────────


class CommentAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    relevance: Literal[RELEVANCE]  # type: ignore[valid-type]
    # 无关时必须为 null。`neutral` 表示「评价了，但态度中性」，与「没在评价这只产品」
    # 是两件事（runbook §11.1「`neutral` 不能表示无关」）。
    attitude: Optional[Literal[ATTITUDES]]  # type: ignore[valid-type]
    aspects: list[Literal[ASPECTS]]  # type: ignore[valid-type]
    # 必须是输入原文的连续片段。模型实测会给转述，所以这里只管**形状**，
    # 真伪由 `ai/evidence.py` 在原文里定位来判（§9「GPT 返回 span＋程序验证」）。
    evidence: Optional[str]
    needs_review: bool
    uncertainty_reasons: list[str]

    @model_validator(mode="after")
    def _cross_field_rules(self):
        if self.relevance == "irrelevant" and self.attitude is not None:
            raise ValueError(
                f"{self.item_id}: relevance=irrelevant 时 attitude 必须为 null，"
                f"收到 {self.attitude!r}（runbook §11.1）"
            )
        if self.relevance == "needs_context" and self.attitude is not None:
            raise ValueError(
                f"{self.item_id}: 判不出是否相关就判不出态度，attitude 必须为 null，"
                f"收到 {self.attitude!r}"
            )
        if self.relevance == "relevant" and self.attitude is None:
            raise ValueError(f"{self.item_id}: relevance=relevant 但没有给出 attitude")
        return self


# ── 帖子（runbook §11.2） ────────────────────────────────────────────────


class PostAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    post_type: Literal[POST_TYPES]  # type: ignore[valid-type]
    # 「只在帖子表达了明确操作时出现，判不出方向的操作类帖子标『方向待确认』」
    # （TYPE_RULE 逐字）。null ＝ 没表达操作；`direction_pending=True` ＝ 表达了但判不出。
    # 两者在界面上是不同的东西，不能合并成一个 null。
    direction: Optional[Literal[DIRECTIONS]]  # type: ignore[valid-type]
    direction_pending: bool
    summary: Optional[str]
    evidence_spans: list[str]
    needs_review: bool

    @model_validator(mode="after")
    def _cross_field_rules(self):
        if self.direction is not None and self.direction_pending:
            raise ValueError(
                f"{self.item_id}: direction={self.direction!r} 与 direction_pending=true "
                f"矛盾 —— 判出来了就不是待确认"
            )
        # 「摘要不超过 60 字」（runbook §11.2）。截断会把一句话砍成半句，
        # 宁可判失败重试：半句摘要在界面上看不出是被截的。
        if self.summary is not None and len(self.summary) > 60:
            raise ValueError(
                f"{self.item_id}: 摘要 {len(self.summary)} 字，超过 60 字上限（runbook §11.2）"
            )
        return self


# ── 评论×产品 v2：一次调用堆七个维度（ADR-0020） ─────────────────────────
#
# 在 v1 的相关性／态度／aspect／证据之外加了三样：
#
# - `market_direction`：对大盘／指数／宏观的方向判断，与产品态度**独立**。PRD §3.4 逐字：
#   「单纯预测指数或价格涨跌归入产品话题情绪」—— 这一列就是「产品话题情绪」（P13）的原料。
#   相关性为 irrelevant 的评论照样可以有方向（「大盘要崩」对产品无关，对市场看空）。
# - `compliance_tags` / `compliance_rationale` / `compliance_evidence`：重点舆情五类
#   （runbook §20）。合规不再是一个单独任务 —— 每条评论本来就要发给模型，再为合规发一遍等于
#   两倍请求。空数组＝「查过了，不是重点舆情」，**必须落库**（§20.4）。
#
# 七个维度在文献给出的安全区内（arXiv 2604.03684：≤10 维精度损失 <2pp）。

MARKET_DIRECTIONS = ("bullish", "bearish", "neutral")
COMPLIANCE_TAGS = (
    "regulatory_complaint", "serious_allegation", "unverified_claim",
    "mobilization", "compliance_concern",
)
COMPLIANCE_RATIONALE_MAX = 40


class CommentAnnotationV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    relevance: Literal[RELEVANCE]  # type: ignore[valid-type]
    attitude: Optional[Literal[ATTITUDES]]  # type: ignore[valid-type]
    aspects: list[Literal[ASPECTS]]  # type: ignore[valid-type]
    evidence: Optional[str]
    market_direction: Optional[Literal[MARKET_DIRECTIONS]]  # type: ignore[valid-type]
    compliance_tags: list[Literal[COMPLIANCE_TAGS]]  # type: ignore[valid-type]
    # PRD 的「AI 命中依据」：允许是模型自己的话，≤40 字；`compliance_evidence` 必须是原文。
    compliance_rationale: Optional[str]
    compliance_evidence: Optional[str]
    needs_review: bool
    uncertainty_reasons: list[str]

    @model_validator(mode="after")
    def _cross_field_rules(self):
        if self.relevance == "irrelevant" and self.attitude is not None:
            raise ValueError(
                f"{self.item_id}: relevance=irrelevant 时 attitude 必须为 null，收到 {self.attitude!r}"
            )
        if self.relevance == "needs_context" and self.attitude is not None:
            raise ValueError(
                f"{self.item_id}: 判不出是否相关就判不出态度，attitude 必须为 null，收到 {self.attitude!r}"
            )
        if self.relevance == "relevant" and self.attitude is None:
            raise ValueError(f"{self.item_id}: relevance=relevant 但没有给出 attitude")
        if self.compliance_tags:
            if not (self.compliance_rationale or "").strip():
                raise ValueError(f"{self.item_id}: 标了合规信号却没有命中依据（rationale）")
            if len(self.compliance_rationale) > COMPLIANCE_RATIONALE_MAX:
                raise ValueError(
                    f"{self.item_id}: 命中依据 {len(self.compliance_rationale)} 字，"
                    f"超过 {COMPLIANCE_RATIONALE_MAX} 字上限（runbook §20.4）"
                )
        else:
            if self.compliance_rationale is not None or self.compliance_evidence is not None:
                raise ValueError(
                    f"{self.item_id}: 没有合规信号时 rationale／compliance_evidence 必须为 null"
                )
        return self


# ── KOL 评论观点（PRD §4.4 M7「其他产品观点及操作」） ─────────────────────
#
# 只跑合作 KOL 的评论。`summary` 是这条评论对该产品观点的一句话（≤30 字），`action` 是设计源
# `ACTIONS` 的 8 个枚举之一（逐字）。它是评论 × 产品的判定单元，与 comment_product 同键。

KOL_ACTIONS = ("加仓", "建仓", "减仓", "清仓", "转投其他产品", "持有不动", "观望", "未提及操作")
KOL_SUMMARY_MAX = 30


class KolOpinionAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    # 评论没有对该产品表达任何观点 ⇒ null（那是结论，写库时落 false 占位，同帖子 summary）。
    summary: Optional[str]
    action: Literal[KOL_ACTIONS]  # type: ignore[valid-type]
    evidence: Optional[str]
    needs_review: bool

    @model_validator(mode="after")
    def _rules(self):
        if self.summary is not None and len(self.summary) > KOL_SUMMARY_MAX:
            raise ValueError(f"{self.item_id}: 观点摘要 {len(self.summary)} 字，超过 {KOL_SUMMARY_MAX} 字上限")
        return self


class KolOpinionAnnotationV2(KolOpinionAnnotation):
    """KOL 评论观点 v2：在观点与操作之外补齐页面使用的 8 类内容形式。"""

    post_type: Literal[POST_TYPES]  # type: ignore[valid-type]


class KolOpinionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[KolOpinionAnnotation]


class KolOpinionBatchV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[KolOpinionAnnotationV2]


TASKS = {
    "comment_product": CommentAnnotation,
    "post_annotation": PostAnnotation,
    "kol_comment_opinion": KolOpinionAnnotation,
}

# 按 schema 版本选模型。`v1` 是 Gate 0–2 的形状，保留给回放与对照实验；生产默认 `v2`
# （`AI_SCHEMA_VERSION`）。post_annotation 两版形状相同。
VERSIONED = {
    "comment_product": {"v1": CommentAnnotation, "v2": CommentAnnotationV2},
    "post_annotation": {"v1": PostAnnotation, "v2": PostAnnotation},
    "kol_comment_opinion": {"v1": KolOpinionAnnotation, "v2": KolOpinionAnnotationV2},
}


def model_for(task, version="v1"):
    try:
        return VERSIONED[task][version]
    except KeyError:
        raise SchemaError(
            f"任务 {task!r} 没有 schema 版本 {version!r}，"
            f"可选：{sorted(VERSIONED.get(task, {}))}"
        ) from None


# ── 批处理信封 ───────────────────────────────────────────────────────────
#
# runbook §11.3：一批 20–50 条（本项目默认 30），且「输出 ID 集合必须与输入完全一致」。
# 顶层必须是 object 而不是裸数组：strict 模式的 root 只接受 object，而且留一个具名字段
# 以后加批级元信息（如整批被拒的原因）不用改 schema 版本。


class CommentBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[CommentAnnotation]


class CommentBatchV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[CommentAnnotationV2]


class PostBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[PostAnnotation]


BATCHES = {
    "comment_product": CommentBatch,
    "post_annotation": PostBatch,
    "kol_comment_opinion": KolOpinionBatch,
}

VERSIONED_BATCHES = {
    "comment_product": {"v1": CommentBatch, "v2": CommentBatchV2},
    "post_annotation": {"v1": PostBatch, "v2": PostBatch},
    "kol_comment_opinion": {"v1": KolOpinionBatch, "v2": KolOpinionBatchV2},
}


def batch_model_for(task, version="v1"):
    try:
        return VERSIONED_BATCHES[task][version]
    except KeyError:
        raise SchemaError(f"任务 {task!r} 没有批 schema 版本 {version!r}") from None


class IdSetMismatch(SchemaError):
    """输入与输出的 item_id 集合不一致。

    这是**最危险的一类失败**，因为它不影响 JSON 合法性：模型漏掉三条、多编两条、
    或者把两条的 id 写反，返回的仍然是一份结构完美的 JSON。如果按顺序对齐（第 i 个
    输出配第 i 个输入），错位会把 A 产品的态度写到 B 产品上，而库里每一行看起来都正常。
    所以对齐**只按 item_id**，集合不等就整批失败。
    """


def parse_batch(task, raw, expected_ids, version="v1"):
    """校验整批输出，并确认 id 集合与输入完全一致。返回 `{item_id: 标注对象}`。"""
    model = batch_model_for(task, version)
    try:
        batch = model.model_validate(raw)
    except ValidationError as exc:
        raise SchemaError(f"{task} 批输出不合 schema: {exc}") from exc

    by_id = {}
    for item in batch.results:
        if item.item_id in by_id:
            raise IdSetMismatch(f"{task}: item_id 重复 {item.item_id!r}")
        by_id[item.item_id] = item

    expected = set(expected_ids)
    got = set(by_id)
    if got != expected:
        raise IdSetMismatch(
            f"{task}: id 集合不一致。缺少 {sorted(expected - got)}；"
            f"多出 {sorted(got - expected)}"
        )
    return by_id


def parse_batch_partial(task, raw, expected_ids, version="v1"):
    if not isinstance(raw, dict) or set(raw) != {"results"} or not isinstance(raw["results"], list):
        raise SchemaError("Expected a results array")
    expected = set(expected_ids)
    seen, valid, invalid = set(), {}, {}
    for row in raw["results"]:
        item_id = row.get("item_id") if isinstance(row, dict) else None
        if not isinstance(item_id, str) or item_id not in expected or item_id in seen:
            raise IdSetMismatch("Unknown or duplicate item_id")
        seen.add(item_id)
        try:
            valid[item_id] = parse(task, row, version)
        except SchemaError as exc:
            invalid[item_id] = str(exc)
    for item_id in expected - seen:
        invalid[item_id] = "Missing item_id"
    return valid, invalid


def batch_json_schema(task, version="v1"):
    """整批发给供应商的 JSON Schema。"""
    return _strictify(copy.deepcopy(batch_model_for(task, version).model_json_schema()))


# ── wire schema ─────────────────────────────────────────────────────────


def _strictify(node):
    """把 Pydantic 生成的 schema 改成供应商 strict 模式接受的形态。

    三件事：每个 object 补 `additionalProperties: false`；`required` 列全部属性；
    删掉 strict 模式不支持的关键字。
    """
    if isinstance(node, list):
        return [_strictify(n) for n in node]
    if not isinstance(node, dict):
        return node

    out = {
        key: ({name: _strictify(schema) for name, schema in value.items()}
              if key in ("properties", "$defs", "definitions") and isinstance(value, dict)
              else _strictify(value))
        for key, value in node.items() if key not in _UNSUPPORTED
    }
    if out.get("type") == "object" or "properties" in out:
        out["additionalProperties"] = False
        out["required"] = list(out.get("properties", {}).keys())
    return out


# strict 模式不支持这些（会 400）。`default` 尤其要删：它会让模型以为字段可以不给。
_UNSUPPORTED = frozenset({"default", "maxLength", "minLength", "pattern", "format",
                          "minimum", "maximum", "minItems", "maxItems", "title"})


def json_schema(task, version="v1"):
    """该任务发给供应商的 JSON Schema。与本地校验同源，不是抄的。"""
    model = model_for(task, version)
    return _strictify(copy.deepcopy(model.model_json_schema()))


def parse(task, raw, version="v1"):
    """校验一条模型输出。失败抛 `SchemaError`，**不返回半个对象**。"""
    model = model_for(task, version)
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise SchemaError(f"{task} 输出不合 schema: {exc}") from exc


# ── 输入指纹（runbook §11.3） ────────────────────────────────────────────


def input_hash(payload, *, model, prompt_version, taxonomy_version, schema_version):
    """缓存键／幂等键。

    runbook §11.3 逐条：「缓存键包括输入、上下文、产品、模型、Prompt、taxonomy 和
    schema 版本」。少任何一项，换了 Prompt 之后重跑会被判成重复而跳过 —— 那正是
    「改了 Prompt 但结果没变」这类查不出原因的 bug。

    `sort_keys` 是必须的：dict 顺序变化不该产生新指纹，否则每次重排字段都会重刷全库。
    """
    material = json.dumps(
        {
            "payload": payload,
            "model": model,
            "prompt_version": prompt_version,
            "taxonomy_version": taxonomy_version,
            "schema_version": schema_version,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
