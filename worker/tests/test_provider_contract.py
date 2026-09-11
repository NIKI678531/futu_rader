"""`ai/providers/openai_compatible.py` —— 与网关的契约。

这份测试把 Gate 0 的**实测结论**钉住，免得后来的人「顺手」把它们改回教科书写法：

| 实测（2026-09-11，amao-prd.csopasset.com，gpt-5.6-luna） | 后果 |
|---|---|
| `temperature` 不被支持 | 带上就是 400，整批失败 |
| `response_format: json_object` 坏 | 400，且报错信息指向 Responses API 的字段 |
| 响应里 `output[0]` 是 `reasoning`，不是 `message` | 取 `output[0]` 会得到空串 |

还有一条不是实测而是纪律：**脱敏断言必须在发出之前**。测试用「构造一个带身份字段的
请求体，断言 HTTP 层一次都没被调用」来证明这一点 —— 只断言抛异常是不够的，
抛异常之前可能已经发出去了。
"""

import json
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai import config  # noqa: E402
from ai.providers import build  # noqa: E402
from ai.providers.base import PermanentError, TransientError  # noqa: E402
from ai.redact import RedactionError  # noqa: E402

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}},
          "required": ["ok"], "additionalProperties": False}


class FakeResponse:
    def __init__(self, status_code, body, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


class FakeSession:
    """按脚本回答的 HTTP 层。脚本项可以是 `FakeResponse` 或要抛出的异常。"""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "body": json,
                           "timeout": timeout})
        step = self.script.pop(0) if self.script else self.script_default()
        if isinstance(step, Exception):
            raise step
        return step

    def script_default(self):
        return ok_response({"ok": True})


def ok_response(data, model="gpt-5.6-luna", usage=None):
    """一条真实形状的 Responses API 响应 —— 注意 `output[0]` 是 reasoning。"""
    return FakeResponse(200, {
        "id": "resp_abc",
        "model": model,
        "status": "completed",
        "output": [
            {"type": "reasoning", "id": "rs_1", "summary": [], "content": []},
            {"type": "message", "id": "msg_1", "role": "assistant",
             "content": [{"type": "output_text",
                          "text": json.dumps(data, ensure_ascii=False)}]},
        ],
        "usage": usage if usage is not None else {
            "input_tokens": 1776,
            "input_tokens_details": {"cached_tokens": 128},
            "output_tokens": 327,
            "output_tokens_details": {"reasoning_tokens": 64},
        },
    })


@pytest.fixture()
def cfg():
    return config.load(base_url="https://gw.invalid/llm/v1", model="gpt-5.6-luna",
                       max_retries=3, timeout_seconds=90)


def provider(cfg, session):
    # sleep 注入成 no-op：退避是真的（有单独一条测它），但测试不该真等 30 秒。
    return build(cfg, session=session, sleep=lambda _s: None)


# ── 请求体：Gate 0 的三条实测 ──────────────────────────────────────────


def test_temperature_is_never_sent(cfg):
    """实测：`Unsupported parameter: 'temperature' is not supported with this model`。
    推理模型的确定性靠 effort 与 prompt，不靠采样温度。"""
    s = FakeSession(ok_response({"ok": True}))
    provider(cfg, s).complete_json("sys", "usr", SCHEMA, "t")
    assert "temperature" not in s.calls[0]["body"]
    assert "top_p" not in s.calls[0]["body"]


def test_it_posts_to_responses_not_chat_completions(cfg):
    """实测：这个网关的 `response_format: json_object` 坏掉了（报错指向
    Responses API 的字段）。`json_schema` + strict 比它强得多，顺势换过去。"""
    s = FakeSession(ok_response({"ok": True}))
    provider(cfg, s).complete_json("sys", "usr", SCHEMA, "t")
    assert s.calls[0]["url"] == "https://gw.invalid/llm/v1/responses"
    assert "response_format" not in s.calls[0]["body"]


def test_structured_output_is_strict(cfg):
    s = FakeSession(ok_response({"ok": True}))
    provider(cfg, s).complete_json("sys", "usr", SCHEMA, "comment_batch")
    fmt = s.calls[0]["body"]["text"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["strict"] is True
    assert fmt["name"] == "comment_batch"
    assert fmt["schema"] is SCHEMA


def test_store_and_reasoning_effort_are_sent(cfg):
    """`store: false` —— 库里是真实用户评论，没理由多留一份在我们不控制的日志里。"""
    s = FakeSession(ok_response({"ok": True}))
    provider(cfg, s).complete_json("sys", "usr", SCHEMA, "t")
    body = s.calls[0]["body"]
    assert body["store"] is False
    assert body["reasoning"] == {"effort": "low"}


def test_system_and_user_are_separate_input_items(cfg):
    s = FakeSession(ok_response({"ok": True}))
    provider(cfg, s).complete_json("SYS", "USR", SCHEMA, "t")
    assert s.calls[0]["body"]["input"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]


def test_auth_and_timeout_come_from_config(cfg):
    s = FakeSession(ok_response({"ok": True}))
    provider(cfg, s).complete_json("sys", "usr", SCHEMA, "t")
    assert s.calls[0]["headers"]["Authorization"] == "Bearer test-key"
    assert s.calls[0]["timeout"] == 90


def test_structured_output_can_be_turned_off(cfg):
    off = config.load(base_url=cfg.base_url, structured_output=False)
    s = FakeSession(ok_response({"ok": True}))
    provider(off, s).complete_json("sys", "usr", SCHEMA, "t")
    assert "text" not in s.calls[0]["body"]


# ── 脱敏必须早于发送 ───────────────────────────────────────────────────


def test_pii_aborts_before_any_byte_leaves(cfg, monkeypatch):
    """只断言抛异常是不够的 —— 抛异常之前可能已经发出去了。所以断言 HTTP 层
    一次都没被调用。"""
    s = FakeSession(ok_response({"ok": True}))
    p = provider(cfg, s)
    # 让请求体里混进一个身份字段（模拟某个调用方绕过了 redact 的白名单构造器）。
    monkeypatch.setattr(
        p, "_build_body",
        lambda *a, **k: {"model": "m", "input": [{"author_name": "老王"}]},
    )
    with pytest.raises(RedactionError):
        p.complete_json("sys", "usr", SCHEMA, "t")
    assert s.calls == [], "脱敏失败时不该有任何请求发出"


# ── 响应解析：推理模型的形状 ───────────────────────────────────────────


def test_reasoning_item_is_skipped(cfg):
    """`output[0]` 是 reasoning，它的 content 是空数组。取 output[0] 会得到空串，
    然后 json.loads 报一个与真实原因毫不相干的错。"""
    s = FakeSession(ok_response({"ok": True, "n": 3}))
    got = provider(cfg, s).complete_json("sys", "usr", SCHEMA, "t")
    assert got.data == {"ok": True, "n": 3}


def test_model_returned_by_the_gateway_wins(cfg):
    """网关会做别名转发：请求的名字和真正跑的快照可能不是一个。"""
    s = FakeSession(ok_response({"ok": True}, model="gpt-5.6-luna-2026-08-01"))
    assert provider(cfg, s).complete_json("sys", "usr", SCHEMA, "t").model == (
        "gpt-5.6-luna-2026-08-01")


def test_usage_includes_reasoning_and_cached_tokens(cfg):
    """reasoning token 计费但不出现在输出文本里。成本估算要把它算进输出侧。"""
    u = provider(cfg, FakeSession(ok_response({"ok": True}))).complete_json(
        "sys", "usr", SCHEMA, "t").usage
    assert (u.input_tokens, u.output_tokens) == (1776, 327)
    assert u.reasoning_tokens == 64 and u.cached_tokens == 128


def test_absent_usage_is_none_not_zero(cfg):
    """铁律 2：0 是「已取得数据且确实为零」的专用值。"""
    s = FakeSession(ok_response({"ok": True}, usage={}))
    u = provider(cfg, s).complete_json("sys", "usr", SCHEMA, "t").usage
    assert u.input_tokens is None and u.output_tokens is None
    assert u.reasoning_tokens is None and u.cached_tokens is None


def test_truncated_response_is_transient(cfg):
    """输出被截断 ⇒ JSON 一定不完整。当瞬时错误重试，而不是把半个 JSON 往下传。"""
    body = {"id": "r", "model": "m", "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"}, "output": []}
    s = FakeSession(*[FakeResponse(200, body)] * 4)
    with pytest.raises(TransientError, match="不完整"):
        provider(cfg, s).complete_json("sys", "usr", SCHEMA, "t")


def test_missing_message_item_is_transient_and_names_what_it_saw(cfg):
    body = {"id": "r", "model": "m", "status": "completed",
            "output": [{"type": "reasoning", "content": []}]}
    s = FakeSession(*[FakeResponse(200, body)] * 4)
    with pytest.raises(TransientError, match="reasoning"):
        provider(cfg, s).complete_json("sys", "usr", SCHEMA, "t")


def test_non_json_text_is_transient(cfg):
    s = FakeSession(*[ok_response(None)] * 4)
    s.script = [FakeResponse(200, {
        "id": "r", "model": "m", "status": "completed",
        "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "抱歉，我无法完成这个请求。"}]}],
    })] * 4
    with pytest.raises(TransientError, match="不是合法 JSON"):
        provider(cfg, s).complete_json("sys", "usr", SCHEMA, "t")


# ── 重试与退避（runbook §11.3） ────────────────────────────────────────


def test_429_is_retried_then_succeeds(cfg):
    s = FakeSession(FakeResponse(429, {"error": {"message": "rate limited"}}),
                    ok_response({"ok": True}))
    assert provider(cfg, s).complete_json("sys", "usr", SCHEMA, "t").data == {"ok": True}
    assert len(s.calls) == 2


def test_400_is_permanent_and_not_retried(cfg):
    """400 重试三次只是把同一个错误发三遍，还会掩盖「配置写错了」这个真实原因。"""
    s = FakeSession(FakeResponse(400, {"error": {"message": "Unsupported parameter"}}))
    with pytest.raises(PermanentError, match="Unsupported parameter"):
        provider(cfg, s).complete_json("sys", "usr", SCHEMA, "t")
    assert len(s.calls) == 1


def test_401_is_permanent(cfg):
    s = FakeSession(FakeResponse(401, {"error": {"message": "invalid api key"}}))
    with pytest.raises(PermanentError):
        provider(cfg, s).complete_json("sys", "usr", SCHEMA, "t")


def test_attempts_are_max_retries_plus_one(cfg):
    """`max_retries` 是**重试**次数，不是总次数。差一的后果是少试一次就判死。"""
    s = FakeSession(*[FakeResponse(503, {"error": {"message": "down"}})] * 10)
    with pytest.raises(TransientError):
        provider(cfg, s).complete_json("sys", "usr", SCHEMA, "t")
    assert len(s.calls) == cfg.max_retries + 1 == 4


def test_timeout_and_connection_errors_are_retried(cfg):
    s = FakeSession(requests.Timeout(), requests.ConnectionError("dns"),
                    ok_response({"ok": True}))
    assert provider(cfg, s).complete_json("sys", "usr", SCHEMA, "t").data == {"ok": True}
    assert len(s.calls) == 3


def test_retry_after_header_overrides_our_backoff(cfg):
    """别用自己算的退避把限流窗口撞穿。"""
    slept = []
    s = FakeSession(FakeResponse(429, {"error": {"message": "slow down"}},
                                 headers={"Retry-After": "7"}),
                    ok_response({"ok": True}))
    build(cfg, session=s, sleep=slept.append).complete_json("sys", "usr", SCHEMA, "t")
    assert slept == [7.0]


def test_backoff_is_jittered_and_capped(cfg):
    """`AI_CONCURRENCY=4` 的四个 worker 会在同一秒撞上同一个 429。不加抖动它们会
    同步退避、同步重试，第二次再一起撞上去。"""
    p = provider(cfg, FakeSession())
    samples = [p._backoff(3) for _ in range(50)]
    assert len(set(samples)) > 1, "没有抖动"
    assert all(4.0 <= x < 12.0 for x in samples)  # 2**3 * [0.5, 1.5)
    # base 封顶 30，所以再高的 attempt 也不会退避到分钟级 —— 一批卡住不该拖垮整轮。
    assert all(15.0 <= x < 45.0 for x in (p._backoff(20) for _ in range(20)))


def test_non_json_error_body_still_produces_a_readable_message(cfg):
    """网关挂掉时常返回 HTML 网关页。报错里要能看出发生了什么。"""
    s = FakeSession(FakeResponse(502, "<html>502 Bad Gateway</html>"),
                    ok_response({"ok": True}))
    provider(cfg, s).complete_json("sys", "usr", SCHEMA, "t")


def test_unknown_provider_name_raises_instead_of_falling_back(cfg):
    """回退会让一个配置笔误表现为「用了另一个模型」，而标注结果上看不出来。"""
    with pytest.raises(ValueError, match="未知的 AI_PRIMARY_PROVIDER"):
        build(config.load(provider="deepseek"))
