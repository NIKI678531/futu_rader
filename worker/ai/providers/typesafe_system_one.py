"""TypeSafe AI System One provider for Layer-A annotation tasks.

System One is a decision API, not a free-text completion API.  The adapter
therefore translates one redacted annotation payload into independent
Choice/Noul questions and deterministically maps the answers back to the
existing Pydantic wire schemas.  Extractive fields are selected from exact
source spans; this provider never invents summaries or evidence.
"""

import json
import logging
import math
import random
import re
import time

import requests

from .. import redact
from ..schemas import ASPECTS, COMPLIANCE_TAGS, POST_TYPES
from .base import Completion, PermanentError, Provider, RunStopped, TransientError, Usage

log = logging.getLogger("worker.ai.provider.typesafe")

_RETRYABLE = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
_LOW_CONFIDENCE = 0.65
_NOUL_YES = 0.5
_NOUL_AMBIGUOUS_LOW = 0.35
_NOUL_AMBIGUOUS_HIGH = 0.65
_MAX_EXCERPTS = 20

_POST_TYPE_CRITERIA = {
    "showcase": "展示真实持仓、收益、成交记录或晒单截图",
    "action": "明确宣布买入、卖出、加减仓或调仓，但未展示成交凭证",
    "market": "分析市场、指数、产品走势、点位或宏观消息",
    "promo": "介绍或推荐某只 ETF 的特点、费率、派息、上市或规模",
    "edu": "解释产品机制、策略或投资知识",
    "event": "活动、福利、抽奖、直播、报名或有奖问答",
    "qa": "提问、回应评论、澄清指代、投票或普通社交互动",
    "other": "以上类型均不适用",
}

_DIRECTION_CRITERIA = {
    "add": "已有仓位后继续加仓、补仓或溝貨",
    "open": "首次建仓、买入、入貨或上車",
    "reduce": "减仓、部分卖出或部分沽出",
    "close": "清仓、全部卖出、离场或全走",
    "hold": "明确持有不动、揸住、坐貨或继续观望现有仓位",
    "pending": "明确提到操作，但无法判断属于哪一种方向",
    "none": "没有表达任何交易操作",
}

_KOL_ACTION_MAP = {
    "add_position": "加仓",
    "open_position": "建仓",
    "reduce_position": "减仓",
    "close_position": "清仓",
    "switch_product": "转投其他产品",
    "hold": "持有不动",
    "wait": "观望",
    "not_mentioned": "未提及操作",
}

_COMPLIANCE_LABELS = {
    "regulatory_complaint": "监管投诉",
    "serious_allegation": "严重指控",
    "unverified_claim": "未经证实指控",
    "mobilization": "煽动扩散",
    "compliance_concern": "合规质疑",
}


class TypeSafeSystemOneProvider(Provider):
    """Adapter for ``POST /v1/systemone``.

    A System One request has one shared state, so annotation batching is
    intentionally capped at one item.  Multiple independent questions for
    that item are still evaluated in the same HTTP request.
    """

    name = "typesafe_system_one"
    max_batch_size = 1
    supports_generation = False

    def __init__(self, config, session=None, sleep=time.sleep, control=None):
        self._cfg = config
        self.control = control
        self._session = session or requests.Session()
        self._sleep = sleep

    def complete_json(self, system, user, schema, schema_name):
        del system, user, schema, schema_name
        raise PermanentError(
            "TypeSafe System One 只支持 Choice/Noul 等判断，不支持自由文本生成；"
            "请保留已有 Layer-B 结果或使用支持生成的独立 provider"
        )

    def complete_annotations(self, task, payloads, schema_version="v2"):
        if len(payloads) != 1:
            raise PermanentError("TypeSafe System One 每次标注必须恰好包含 1 条 state")
        payload = payloads[0]
        questions, context = _questions_for(task, payload, schema_version)
        body = {"state": payload, "model": self._cfg.model, "questions": questions}
        redact.assert_clean(body)

        response = self._post_with_retry("/v1/systemone", body)
        answers = response.get("answers")
        if not isinstance(answers, dict):
            raise TransientError("TypeSafe 响应缺少 answers 对象")
        data = {"results": [_map_answers(task, payload, questions, answers, context, schema_version)]}
        usage = _extract_usage(response)
        if self.control is not None:
            self.control.record(usage)
            if self.control.before_request is not None:
                self.control.before_request()
            if self.control.reason in ("source_changed", "lease_lost"):
                raise RunStopped(self.control.reason)
        return Completion(
            data=data,
            model=response.get("model") or self._cfg.model,
            usage=usage,
            raw_text=json.dumps(response, ensure_ascii=False),
            response_id=response.get("id"),
        )

    def _post_with_retry(self, path, body):
        url = f"{self._cfg.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._cfg.api_key}",
            "Content-Type": "application/json",
        }
        last = None
        for attempt in range(self._cfg.max_retries + 1):
            if self.control is not None:
                self.control.reserve(retry=attempt > 0)
            started = time.monotonic()
            try:
                response = self._session.post(
                    url, headers=headers, json=body, timeout=self._cfg.timeout_seconds
                )
            except requests.Timeout as exc:
                last = TransientError(f"请求超时（{self._cfg.timeout_seconds}s）")
                last.__cause__ = exc
            except requests.RequestException as exc:
                last = TransientError(f"连接失败：{exc}")
                last.__cause__ = exc
            else:
                if self.control is not None:
                    self.control.latency(time.monotonic() - started)
                if response.status_code == 200:
                    try:
                        return response.json()
                    except ValueError as exc:
                        last = TransientError("TypeSafe 返回了非 JSON 响应")
                        last.__cause__ = exc
                else:
                    last = self._classify(response)
                    if isinstance(last, PermanentError):
                        if self.control is not None:
                            self.control.stop("configuration_error")
                        raise last

            if attempt < self._cfg.max_retries:
                delay = _backoff(attempt, getattr(last, "retry_after", None))
                log.warning("TypeSafe 第 %d/%d 次失败（%s），%.1fs 后重试",
                            attempt + 1, self._cfg.max_retries + 1, last, delay)
                if self.control is None:
                    self._sleep(delay)
                else:
                    self.control.cooldown(
                        delay, rate_limited=getattr(last, "rate_limited", False)
                    )
        raise last

    @staticmethod
    def _classify(response):
        detail = _error_detail(response)
        if response.status_code in _RETRYABLE:
            error = TransientError(f"HTTP {response.status_code}: {detail}")
            error.retry_after = _retry_after(response)
            error.rate_limited = response.status_code == 429
            return error
        return PermanentError(f"HTTP {response.status_code}: {detail}")


def _choice(instructions, criteria):
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def _noul(instructions, yes, no):
    return {
        "type": "noul",
        "instructions": instructions,
        "criteria": {"true": yes, "false": no},
    }


def _questions_for(task, payload, schema_version):
    if task == "comment_product":
        questions = {
            "relevance_attitude": _choice(
                "只判断 comment 对 state.product 指定 ETF 的态度；上下文仅用于消歧。",
                {
                    "relevant_positive": "明确评价该 ETF 且认可、看好、愿意买入或称赞",
                    "relevant_neutral": "明确评价该 ETF，但只是客观陈述且无褒贬",
                    "relevant_negative": "明确评价该 ETF 且不满、看空、卖出或批评",
                    "irrelevant": "只聊个股、大盘、其他产品、拉群或普通互动，没有评价该 ETF",
                    "needs_context": "结合所给父评论和帖子上下文仍无法判断指代对象",
                },
            ),
            "market_direction": _choice(
                "判断 comment 是否表达对市场、指数、宏观或该 ETF 跟踪标的的方向。",
                {
                    "bullish": "明确看多或预期上涨",
                    "bearish": "明确看空或预期下跌",
                    "neutral": "谈到市场但没有方向",
                    "not_expressed": "完全没有表达市场方向",
                },
            ),
        }
        for aspect in ASPECTS:
            questions[f"aspect__{aspect}"] = _noul(
                f"comment 是否明确评价指定 ETF 的 {aspect} 方面？",
                "明确涉及该方面", "没有涉及该方面",
            )
        if schema_version != "v1":
            descriptions = {
                "regulatory_complaint": "已向或打算向证监会、SFC、消委会、金管局、港交所或警方投诉举报",
                "serious_allegation": "指控发行人、做市商或平台操纵、欺诈、内幕、利益输送等严重违法违规",
                "unverified_claim": "传播未附依据的清盘、停牌、被查、跑路或暴雷等重大事实断言",
                "mobilization": "号召他人集体投诉、留名、刷差评或转发扩散",
                "compliance_concern": "质疑销售宣传、风险披露、KYC 或适当性安排",
            }
            for tag in COMPLIANCE_TAGS:
                questions[f"compliance__{tag}"] = _noul(
                    f"comment 是否出现需要关注的 {tag} 信号？只标信号，不判断事实真伪。",
                    descriptions[tag], "没有这种信号",
                )
        excerpts = _extractive_candidates(payload.get("comment"), 60)
        criteria = {"none": "没有能直接支撑对指定 ETF 评价的原文片段"}
        criteria.update({key: value for key, value in excerpts.items()})
        if excerpts:
            questions["product_evidence"] = _choice(
                "选择最能支撑 comment 对指定 ETF 态度的连续原文；若没有直接评价就选 none。",
                criteria,
            )
        if schema_version != "v1" and excerpts:
            questions["compliance_evidence"] = _choice(
                "选择最能支撑合规关注信号的连续原文；若无信号就选 none。",
                criteria,
            )
        return questions, {"excerpts": excerpts}

    if task == "post_annotation":
        excerpts = _post_candidates(payload, 60)
        criteria = {"none": "帖子没有可读、可概括的文字内容"}
        criteria.update({key: value for key, value in excerpts.items()})
        questions = {
            "post_type": _choice("判断这篇帖子的主要内容形式。", _POST_TYPE_CRITERIA),
            "direction": _choice("判断帖子作者表达的交易操作；只看明确写出的操作。", _DIRECTION_CRITERIA),
        }
        if excerpts:
            questions["summary_excerpt"] = _choice(
                "选择最能概括帖子内容的一段连续原文（不是改写）；只有无可读内容才选 none。",
                criteria,
            )
        return questions, {"excerpts": excerpts}

    if task == "kol_comment_opinion":
        excerpts = _extractive_candidates(payload.get("comment"), 30)
        criteria = {
            "no_opinion": "只是致谢、表情、寒暄、拉群、泛泛互动，或没有对指定 ETF 表达观点"
        }
        criteria.update({key: value for key, value in excerpts.items()})
        questions = {
            "action": _choice(
                "判断 KOL 在 comment 中对指定 ETF 明确表达的操作。",
                {
                    "add_position": "已有仓位后继续加仓、补仓或溝貨",
                    "open_position": "首次建仓、买入、入貨或上車",
                    "reduce_position": "减仓或部分卖出",
                    "close_position": "清仓、全部卖出或离场",
                    "switch_product": "明确改买或建议转投另一只产品",
                    "hold": "明确持有不动、揸住或坐貨",
                    "wait": "明确等待、观望或睇定啲",
                    "not_mentioned": "没有提及任何操作",
                },
            ),
        }
        if excerpts:
            questions["opinion_excerpt"] = _choice(
                "选择 KOL 对 state.product 指定 ETF 的直接观点或操作原文；没有产品观点必须选 no_opinion。",
                criteria,
            )
        if schema_version != "v1":
            questions["post_type"] = _choice(
                "只看这条 KOL 评论本身的主要内容形式，父帖只用于理解指代。",
                _POST_TYPE_CRITERIA,
            )
        return questions, {"excerpts": excerpts}

    raise PermanentError(f"TypeSafe provider 不支持任务 {task!r}")


def _map_answers(task, payload, questions, answers, context, schema_version):
    expected = set(questions)
    got = set(answers)
    if got != expected:
        raise TransientError(
            f"TypeSafe answers 集合不一致：缺少 {sorted(expected - got)}；多出 {sorted(got - expected)}"
        )
    item_id = payload["item_id"]
    reasons = []

    if task == "comment_product":
        combined, confidence = _read_choice(answers, questions, "relevance_attitude")
        if combined.startswith("relevant_"):
            relevance, attitude = "relevant", combined.removeprefix("relevant_")
        else:
            relevance, attitude = combined, None
        if confidence < _LOW_CONFIDENCE:
            reasons.append("产品相关性或态度置信度较低")

        market, market_confidence = _read_choice(answers, questions, "market_direction")
        if market_confidence < _LOW_CONFIDENCE:
            reasons.append("市场方向置信度较低")
        market_direction = None if market == "not_expressed" else market

        aspects = []
        for aspect in ASPECTS:
            probability = _read_noul(answers, f"aspect__{aspect}")
            if probability >= _NOUL_YES:
                aspects.append(aspect)
            if _NOUL_AMBIGUOUS_LOW <= probability <= _NOUL_AMBIGUOUS_HIGH:
                reasons.append(f"{aspect} 维度接近阈值")
        if "other" in aspects and len(aspects) > 1:
            aspects.remove("other")

        if "product_evidence" in questions:
            evidence_key, evidence_confidence = _read_choice(
                answers, questions, "product_evidence"
            )
            evidence = context["excerpts"].get(evidence_key)
        else:
            evidence, evidence_confidence = None, 0.0
        if evidence_confidence < _LOW_CONFIDENCE or (relevance == "relevant" and not evidence):
            reasons.append("产品态度证据不明确")

        result = {
            "item_id": item_id,
            "relevance": relevance,
            "attitude": attitude,
            "aspects": aspects if relevance == "relevant" else [],
            "evidence": evidence if relevance == "relevant" else None,
            "needs_review": bool(reasons),
            "uncertainty_reasons": _dedupe(reasons),
        }
        if schema_version != "v1":
            tags = []
            for tag in COMPLIANCE_TAGS:
                probability = _read_noul(answers, f"compliance__{tag}")
                if probability >= _NOUL_YES:
                    tags.append(tag)
                if _NOUL_AMBIGUOUS_LOW <= probability <= _NOUL_AMBIGUOUS_HIGH:
                    reasons.append(f"{tag} 信号接近阈值")
            if "compliance_evidence" in questions:
                compliance_key, compliance_confidence = _read_choice(
                    answers, questions, "compliance_evidence"
                )
                compliance_evidence = context["excerpts"].get(compliance_key) if tags else None
            else:
                compliance_evidence, compliance_confidence = None, 0.0
            if tags and (not compliance_evidence or compliance_confidence < _LOW_CONFIDENCE):
                reasons.append("合规信号证据不明确")
            result.update({
                "market_direction": market_direction,
                "compliance_tags": tags,
                "compliance_rationale": (
                    "命中" + "、".join(_COMPLIANCE_LABELS[tag] for tag in tags) + "信号"
                    if tags else None
                ),
                "compliance_evidence": compliance_evidence,
            })
            result["needs_review"] = bool(reasons)
            result["uncertainty_reasons"] = _dedupe(reasons)
        return result

    if task == "post_annotation":
        post_type, type_confidence = _read_choice(answers, questions, "post_type")
        direction_value, direction_confidence = _read_choice(answers, questions, "direction")
        if "summary_excerpt" in questions:
            summary_key, summary_confidence = _read_choice(
                answers, questions, "summary_excerpt"
            )
            summary = context["excerpts"].get(summary_key)
        else:
            summary, summary_confidence = None, 0.0
        needs_review = min(type_confidence, direction_confidence, summary_confidence) < _LOW_CONFIDENCE
        return {
            "item_id": item_id,
            "post_type": post_type,
            "direction": direction_value if direction_value in {"add", "open", "reduce", "close", "hold"} else None,
            "direction_pending": direction_value == "pending",
            "summary": summary,
            "evidence_spans": [summary] if summary else [],
            "needs_review": needs_review,
        }

    if task == "kol_comment_opinion":
        if "opinion_excerpt" in questions:
            excerpt_key, excerpt_confidence = _read_choice(
                answers, questions, "opinion_excerpt"
            )
            summary = context["excerpts"].get(excerpt_key)
        else:
            summary, excerpt_confidence = None, 0.0
        action_key, action_confidence = _read_choice(answers, questions, "action")
        confidences = [excerpt_confidence, action_confidence]
        result = {
            "item_id": item_id,
            "summary": summary,
            "action": _KOL_ACTION_MAP[action_key],
            "evidence": summary,
            "needs_review": False,
        }
        if schema_version != "v1":
            post_type, type_confidence = _read_choice(answers, questions, "post_type")
            result["post_type"] = post_type
            confidences.append(type_confidence)
        result["needs_review"] = min(confidences) < _LOW_CONFIDENCE
        return result

    raise PermanentError(f"TypeSafe provider 不支持任务 {task!r}")


def _read_choice(answers, questions, key):
    answer = answers.get(key)
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise TransientError(f"TypeSafe {key} 不是合法 Choice answer")
    allowed = set(questions[key]["criteria"])
    choice = answer.get("choice")
    probabilities = answer.get("probabilities")
    confidence = answer.get("confidence")
    if choice not in allowed:
        raise TransientError(f"TypeSafe {key} 返回未知 choice={choice!r}")
    if not isinstance(probabilities, dict) or set(probabilities) != allowed:
        raise TransientError(f"TypeSafe {key} probabilities 选项集合不一致")
    values = list(probabilities.values())
    if not values or any(not _probability(value) for value in values):
        raise TransientError(f"TypeSafe {key} probabilities 含非法概率")
    if not math.isclose(sum(values), 1.0, abs_tol=0.02):
        raise TransientError(f"TypeSafe {key} probabilities 合计不为 1")
    if probabilities[choice] + 1e-9 < max(values):
        raise TransientError(f"TypeSafe {key} choice 不是最高概率选项")
    if not _probability(confidence):
        raise TransientError(f"TypeSafe {key} confidence 不是 0..1")
    return choice, float(confidence)


def _read_noul(answers, key):
    answer = answers.get(key)
    if not isinstance(answer, dict) or answer.get("type") != "noul":
        raise TransientError(f"TypeSafe {key} 不是合法 Noul answer")
    probability = answer.get("noul")
    if not _probability(probability):
        raise TransientError(f"TypeSafe {key} noul 不是 0..1")
    return float(probability)


def _probability(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1


def _extractive_candidates(text, max_chars):
    text = (text or "").strip()
    if not text:
        return {}
    pieces = []
    for sentence in re.findall(r"[^\n。！？!?；;]+[。！？!?；;]?", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= max_chars:
            pieces.append(sentence)
            continue
        for fragment in re.findall(r"[^，,、：:]+[，,、：:]?", sentence):
            fragment = fragment.strip()
            if 1 < len(fragment) <= max_chars:
                pieces.append(fragment)
    if len(text) <= max_chars:
        pieces.insert(0, text)
    unique = []
    for piece in pieces:
        if piece not in unique:
            unique.append(piece)
    return {f"excerpt_{index}": value for index, value in enumerate(unique[:_MAX_EXCERPTS])}


def _post_candidates(payload, max_chars):
    values = []
    for field in ("title", "content"):
        for value in _extractive_candidates(payload.get(field), max_chars).values():
            if value not in values:
                values.append(value)
    return {f"excerpt_{index}": value for index, value in enumerate(values[:_MAX_EXCERPTS])}


def _dedupe(values):
    return list(dict.fromkeys(values))


def _extract_usage(payload):
    usage = payload.get("usage") or {}
    return Usage(
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
    )


def _error_detail(response):
    try:
        body = response.json()
    except ValueError:
        return (response.text or "")[:300]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return error.get("message") or json.dumps(error, ensure_ascii=False)[:300]
    return json.dumps(body, ensure_ascii=False)[:300]


def _retry_after(response):
    raw = response.headers.get("Retry-After") if hasattr(response, "headers") else None
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def _backoff(attempt, retry_after=None):
    if retry_after is not None:
        return retry_after
    return min(2**attempt, 30) * (0.5 + random.random())
