"""OpenAI-compatible 供应商 —— 走 **Responses API**，不走 Chat Completions。

## 为什么是 /responses 而不是 /chat/completions

2026-09-11 对目标网关（`amao-prd.csopasset.com/llm/v1`，模型 `gpt-5.6-luna`）逐项实测：

| 试的东西 | 结果 |
|---|---|
| `GET /models` | 200，返回 6 个模型，`gpt-5.6-luna` 在列 |
| `POST /chat/completions` + `temperature: 0` | **400** `Unsupported parameter: 'temperature' is not supported with this model` |
| `POST /chat/completions` + `response_format: json_object` | **400** `Response input messages must contain the word 'json'...`（提示里有「JSON」也照报；小写 `json` 也照报） |
| `POST /responses` + `text.format.json_schema` `strict:true` | **200**，返回合法结构化 JSON |

`/models` 的 `supported_endpoint_types` 写的是 `["openai-response", "openai"]` ——
网关内部把 chat/completions 翻译成 Responses API，那条 `json_object` 的校验在翻译后
用错了字段（报错信息里的 `'***.format'` 和 `param: "input"` 都是 Responses API 的形状），
所以 `json_object` 在这个网关上**事实上不可用**。

这不是坏消息：`json_schema` + `strict` 比 `json_object` 强得多 —— 后者只保证「是合法
JSON」，前者保证「字段、枚举、必填全部符合我们的定义」。runbook §6.4 原本写的是
Chat Completions + `temperature=0` + `json_object`，三项里有两项在这个网关上不成立，
已按实测改写（见 runbook §6.4）。

## 这是个推理模型

响应里有 `output[].type == "reasoning"`，`usage.output_tokens_details.reasoning_tokens`
会计费但**不出现在输出文本里**（实测 80 个输出 token 里 23 个是 reasoning）。
所以：

- 解析输出时必须**跳过** reasoning 项去找 `type == "message"`，不能取 `output[0]`；
- 批量分类用 `reasoning.effort = "low"` 省钱省延迟；
- 成本估算要把 reasoning token 算进输出侧。

## 不用官方 openai SDK

这个网关有三处偏离标准（不支持 temperature、`json_object` 坏、模型别名转发），
SDK 会把这些藏在一层封装后面，出问题时看不到线上真正发了什么。直接用 `requests` 拼
请求体，和 `radar_db/` 用 SQLAlchemy Core 而不用 ORM 是同一个理由：**保持可见**。
"""

import json
import logging
import random
import time

import requests

from .. import redact
from .base import Completion, PermanentError, Provider, TransientError, TruncatedOutput, RunStopped, Usage

log = logging.getLogger("worker.ai.provider")

# 值得退避重试的 HTTP 码。408 超时、409 冲突、425 太早、429 限流，以及全部 5xx。
_RETRYABLE = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


class OpenAiCompatibleProvider(Provider):
    name = "openai_compatible"

    def __init__(self, config, session=None, sleep=time.sleep, control=None):
        self._cfg = config
        self.control = control
        # session 可注入，测试用 fake 顶掉，不必起 HTTP 服务。
        self._session = session or requests.Session()
        self._sleep = sleep

    # ── 对外 ────────────────────────────────────────────────────────────

    def complete_json(self, system, user, schema, schema_name):
        body = self._build_body(system, user, schema, schema_name)
        # 最后一道闸：请求体里不许有身份字段。抛异常＝一个字节都没发出去（§11.4）。
        redact.assert_clean(body)

        payload = self._post_with_retry("/responses", body)
        if self.control is not None:
            self.control.record(_extract_usage(payload))
            if self.control.before_request is not None:
                self.control.before_request()
            if self.control.reason in ("source_changed", "lease_lost"):
                raise RunStopped(self.control.reason)
        text = _extract_output_text(payload)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            # strict 模式下这几乎不该发生；真发生了是供应商侧的问题，值得重试一次。
            raise TransientError(
                f"结构化输出不是合法 JSON（前 200 字符）：{text[:200]!r}"
            ) from exc

        return Completion(
            data=data,
            model=payload.get("model") or self._cfg.model,
            usage=_extract_usage(payload),
            raw_text=text,
            response_id=payload.get("id"),
        )

    # ── 请求体 ──────────────────────────────────────────────────────────

    def _build_body(self, system, user, schema, schema_name):
        body = {
            "model": self._cfg.model,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # 实测：这个模型**不接受** temperature，带上直接 400。推理模型的确定性
            # 靠 effort 与 prompt，不靠采样温度。
            "store": self._cfg.store,
        }
        if self._cfg.structured_output:
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            }
        if self._cfg.reasoning_effort:
            body["reasoning"] = {"effort": self._cfg.reasoning_effort}
        if getattr(self._cfg, "service_tier", ""):
            body["service_tier"] = self._cfg.service_tier
        if self._cfg.grouped_batches:
            body["max_output_tokens"] = self._cfg.max_output_tokens
        return body

    # ── 传输层重试（runbook §11.3） ─────────────────────────────────────

    def _post_with_retry(self, path, body):
        url = f"{self._cfg.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._cfg.api_key}",
            "Content-Type": "application/json",
        }
        last = None
        # max_retries 是**重试**次数，所以总共尝试 max_retries + 1 次。
        for attempt in range(self._cfg.max_retries + 1):
            if self.control is not None:
                self.control.reserve(retry=attempt > 0)
            started = time.monotonic()
            try:
                resp = self._session.post(
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
                if resp.status_code == 200:
                    return resp.json()
                last = self._classify(resp)
                if isinstance(last, PermanentError):
                    if self.control is not None:
                        self.control.stop("configuration_error")
                    raise last

            if attempt < self._cfg.max_retries:
                delay = self._backoff(attempt, getattr(last, "retry_after", None))
                log.warning(
                    "第 %d/%d 次失败（%s），%.1fs 后重试",
                    attempt + 1, self._cfg.max_retries + 1, last, delay,
                )
                if self.control is None:
                    self._sleep(delay)
                else:
                    self.control.cooldown(delay, rate_limited=getattr(last, "rate_limited", False))

        raise last

    def _classify(self, resp):
        detail = _error_detail(resp)
        if resp.status_code in _RETRYABLE:
            err = TransientError(f"HTTP {resp.status_code}: {detail}")
            # 供应商给了 Retry-After 就听它的，别用我们自己算的退避把限流窗口撞穿。
            err.retry_after = _retry_after(resp)
            err.rate_limited = resp.status_code == 429
            return err
        return PermanentError(f"HTTP {resp.status_code}: {detail}")

    def _backoff(self, attempt, retry_after=None):
        """指数退避＋抖动（runbook §11.3）。

        抖动是必须的：`AI_CONCURRENCY=4` 的四个 worker 会在同一秒撞上同一个 429，
        不加抖动它们会**同步**退避、同步重试，第二次再一起撞上去。
        """
        if retry_after is not None:
            return retry_after
        base = min(2**attempt, 30)
        return base * (0.5 + random.random())  # noqa: S311  退避抖动，不是密码学用途


# ── 响应解析 ───────────────────────────────────────────────────────────


def _extract_output_text(payload):
    """从 Responses API 的 `output` 数组里取最终文本。

    **必须按 type 找，不能取 output[0]** —— 推理模型的第一项是 `reasoning`，
    它的 `content` 是空数组，取它会得到空串，然后 json.loads 报一个与真实原因
    毫不相干的错。
    """
    if payload.get("status") == "incomplete":
        reason = (payload.get("incomplete_details") or {}).get("reason")
        # 输出被截断 ⇒ JSON 一定不完整。当瞬时错误重试（下一次可能更短）。
        raise TruncatedOutput(f"响应不完整（reason={reason}）——多半是批太大或输出上限太低")

    for item in payload.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") == "output_text":
                return part.get("text") or ""

    raise TransientError(
        f"响应里没有 output_text（status={payload.get('status')!r}，"
        f"output types={[i.get('type') for i in payload.get('output') or []]}）"
    )


def _extract_usage(payload):
    u = payload.get("usage") or {}
    out_details = u.get("output_tokens_details") or {}
    in_details = u.get("input_tokens_details") or {}
    return Usage(
        input_tokens=u.get("input_tokens"),
        output_tokens=u.get("output_tokens"),
        reasoning_tokens=out_details.get("reasoning_tokens"),
        cached_tokens=in_details.get("cached_tokens"),
    )


def _error_detail(resp):
    try:
        body = resp.json()
    except ValueError:
        return (resp.text or "")[:300]
    err = body.get("error")
    if isinstance(err, dict):
        return err.get("message") or json.dumps(err, ensure_ascii=False)[:300]
    return json.dumps(body, ensure_ascii=False)[:300]


def _retry_after(resp):
    raw = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None  # HTTP-date 形式的 Retry-After 少见，退回自己算的退避
