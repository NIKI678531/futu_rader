"""Contract tests for the TypeSafe System One annotation adapter."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai import config  # noqa: E402
from ai.providers import build  # noqa: E402
from ai.providers.base import PermanentError, TransientError  # noqa: E402


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
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "body": json, "timeout": timeout})
        if isinstance(self.responder, list):
            return self.responder.pop(0)
        return self.responder(json)


@pytest.fixture()
def cfg():
    return config.load(
        provider="typesafe_system_one",
        base_url="https://api.typesafe.invalid",
        model="jev-latest",
        micro_batch_size=30,
        max_retries=1,
    )


def _choice(question, selected, confidence=0.95):
    labels = list(question["criteria"])
    rest = (1 - 0.9) / max(1, len(labels) - 1)
    probabilities = {label: (0.9 if label == selected else rest) for label in labels}
    if len(labels) == 1:
        probabilities[selected] = 1.0
    return {
        "type": "choice",
        "choice": selected,
        "confidence": confidence,
        "probabilities": probabilities,
    }


def _noul(value=0.05):
    return {"type": "noul", "noul": value}


def _response(body, choices=None, nouls=None):
    choices = choices or {}
    nouls = nouls or {}
    answers = {}
    for key, question in body["questions"].items():
        if question["type"] == "choice":
            selected = choices.get(key, next(iter(question["criteria"])))
            answers[key] = _choice(question, selected)
        else:
            answers[key] = _noul(nouls.get(key, 0.05))
    return FakeResponse(200, {
        "model": "jev-1.13.0",
        "answers": answers,
        "usage": {"input_tokens": 321, "output_tokens": 45},
    })


def test_kol_annotation_uses_system_one_and_maps_extractively(cfg):
    def respond(body):
        excerpt = next(key for key in body["questions"]["opinion_excerpt"]["criteria"]
                       if key.startswith("excerpt_"))
        return _response(body, {
            "opinion_excerpt": excerpt,
            "action": "open_position",
            "post_type": "action",
        })

    session = FakeSession(respond)
    client = build(cfg, session=session, sleep=lambda _seconds: None)
    payload = {
        "item_id": "comment:7|product:3033",
        "product": {"code": "3033", "name": "恒生科技指数ETF", "aliases": ["恒科"]},
        "comment": "今日开始分批买入3033。",
    }
    completion = client.complete_annotations("kol_comment_opinion", [payload], "v2")

    call = session.calls[0]
    assert call["url"] == "https://api.typesafe.invalid/v1/systemone"
    assert call["headers"]["Authorization"] == "Bearer test-key"
    assert call["body"]["state"] == payload
    assert call["body"]["model"] == "jev-latest"
    assert "input" not in call["body"] and "text" not in call["body"]
    assert completion.model == "jev-1.13.0"
    assert (completion.usage.input_tokens, completion.usage.output_tokens) == (321, 45)
    assert completion.data == {"results": [{
        "item_id": payload["item_id"],
        "summary": "今日开始分批买入3033。",
        "action": "建仓",
        "evidence": "今日开始分批买入3033。",
        "needs_review": False,
        "post_type": "action",
    }]}


def test_kol_no_opinion_stays_explicit_null(cfg):
    session = FakeSession(lambda body: _response(body, {
        "opinion_excerpt": "no_opinion",
        "action": "not_mentioned",
        "post_type": "qa",
    }))
    payload = {
        "item_id": "comment:8|product:3033",
        "product": {"code": "3033"},
        "comment": "多谢支持😊",
    }
    row = build(cfg, session=session).complete_annotations(
        "kol_comment_opinion", [payload], "v2"
    ).data["results"][0]
    assert row["summary"] is None and row["evidence"] is None
    assert row["action"] == "未提及操作" and row["post_type"] == "qa"


def test_comment_v2_combines_relevance_and_attitude(cfg):
    def respond(body):
        excerpt = next(key for key in body["questions"]["product_evidence"]["criteria"]
                       if key.startswith("excerpt_"))
        return _response(
            body,
            {
                "relevance_attitude": "relevant_negative",
                "market_direction": "not_expressed",
                "product_evidence": excerpt,
                "compliance_evidence": "none",
            },
            {"aspect__fee": 0.96},
        )

    session = FakeSession(respond)
    payload = {
        "item_id": "comment:9|product:3033",
        "product": {"code": "3033"},
        "comment": "这只ETF管理费太高。",
    }
    row = build(cfg, session=session).complete_annotations(
        "comment_product", [payload], "v2"
    ).data["results"][0]
    assert row["relevance"] == "relevant" and row["attitude"] == "negative"
    assert row["aspects"] == ["fee"]
    assert row["evidence"] == payload["comment"]
    assert row["market_direction"] is None
    assert row["compliance_tags"] == []


def test_post_annotation_selects_exact_source_excerpt(cfg):
    def respond(body):
        excerpts = body["questions"]["summary_excerpt"]["criteria"]
        selected = next(key for key, value in excerpts.items() if value == "恒科短线仍有支持。")
        return _response(body, {
            "post_type": "market", "direction": "none", "summary_excerpt": selected,
        })

    session = FakeSession(respond)
    payload = {
        "item_id": "feed:10",
        "title": "恒科复盘",
        "content": "恒科短线仍有支持。继续观察成交量。",
        "product": {"code": "3033"},
    }
    row = build(cfg, session=session).complete_annotations(
        "post_annotation", [payload], "v2"
    ).data["results"][0]
    assert row["post_type"] == "market"
    assert row["direction"] is None and row["direction_pending"] is False
    assert row["summary"] == "恒科短线仍有支持。"
    assert row["evidence_spans"] == ["恒科短线仍有支持。"]


def test_invalid_choice_distribution_is_rejected(cfg):
    def respond(body):
        response = _response(body, {
            "opinion_excerpt": "no_opinion",
            "action": "not_mentioned",
            "post_type": "qa",
        })
        response._body["answers"]["action"]["probabilities"] = {"not_mentioned": 1.0}
        return response

    payload = {
        "item_id": "comment:11|product:3033",
        "product": {"code": "3033"},
        "comment": "谢谢",
    }
    with pytest.raises(TransientError, match="选项集合"):
        build(cfg, session=FakeSession(respond)).complete_annotations(
            "kol_comment_opinion", [payload], "v2"
        )


def test_system_one_cannot_be_used_for_free_text_generation(cfg):
    with pytest.raises(PermanentError, match="不支持自由文本生成"):
        build(cfg, session=FakeSession(lambda _body: None)).complete_json(
            "system", "user", {}, "summary"
        )


def test_permanent_and_retryable_http_errors(cfg):
    payload = {
        "item_id": "comment:12|product:3033",
        "product": {"code": "3033"},
        "comment": "谢谢",
    }
    unauthorized = FakeSession([FakeResponse(401, {"error": {"message": "bad key"}})])
    with pytest.raises(PermanentError, match="bad key"):
        build(cfg, session=unauthorized).complete_annotations("kol_comment_opinion", [payload], "v2")
    assert len(unauthorized.calls) == 1

    def ok_after_retry(body):
        return _response(body, {
            "opinion_excerpt": "no_opinion",
            "action": "not_mentioned",
            "post_type": "qa",
        })

    retry = FakeSession([
        FakeResponse(429, {"error": {"message": "slow"}}, {"Retry-After": "0"}),
    ])
    original_post = retry.post

    def post(url, headers=None, json=None, timeout=None):
        if retry.responder:
            return original_post(url, headers=headers, json=json, timeout=timeout)
        retry.calls.append({"url": url, "headers": headers, "body": json, "timeout": timeout})
        return ok_after_retry(json)

    retry.post = post
    build(cfg, session=retry, sleep=lambda _seconds: None).complete_annotations(
        "kol_comment_opinion", [payload], "v2"
    )
    assert len(retry.calls) == 2
