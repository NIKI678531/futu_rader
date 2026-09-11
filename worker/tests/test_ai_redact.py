"""`ai/redact.py` —— 发往外部模型前的脱敏（runbook §11.4）。

这一层守的是**不可逆**的边界：请求发出去就收不回来了。库里是真实富途用户的评论，
dump 带昵称、IP 归属地和个人简介（ADR-0008）。

所以测试分两类：
- **白名单**：只有 `comment_payload` / `post_payload` 构造出来的 dict 才准进请求体，
  产品块里多一个键都不行。
- **断言层**：`assert_clean` 是断言不是过滤 —— 过滤会把一次事故变成一次静默修正，
  而事故本身（某个调用方绕过了白名单）没有任何人会知道。

另有一条反向约束：**不许脱敏过头**。`3033`、`-2x`、`0.99%` 是判断产品态度必需的，
把数字一并抹掉会让「这只ETF点差太大」失去可判断性。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from ai import redact  # noqa: E402
from ai.redact import RedactionError  # noqa: E402

PRODUCT = {"code": "3033", "name": "南方恒生科技"}


# ── 正文清洗 ───────────────────────────────────────────────────────────


def test_at_mentions_are_replaced():
    """用户常在评论里 @ 别人。正文必须发，但被 @ 的那个人没同意。"""
    out = redact.scrub_text("@老王 你看这只3033还能拿吗")
    assert "老王" not in out
    assert "@[用户]" in out and "3033" in out


def test_user_profile_links_are_replaced():
    """平台用户主页链接直接暴露 UID。"""
    out = redact.scrub_text("看 https://www.futunn.com/user/12345678 的持仓")
    assert "12345678" not in out


def test_any_url_is_replaced_because_it_may_carry_a_signature():
    out = redact.scrub_text("图表在 http://internal.example.com/x?token=abc123")
    assert "token=abc123" not in out and "[链接]" in out


def test_numbers_survive_because_the_judgement_depends_on_them():
    """点差、费率、杠杆倍数是态度判断的依据。抹掉它们等于让模型判不出来。"""
    out = redact.scrub_text("3033 费率 0.99%，-2x 的损耗太大，点差 0.3%")
    for token in ("3033", "0.99%", "-2x", "0.3%"):
        assert token in out


def test_scrub_passes_through_none():
    assert redact.scrub_text(None) is None


def test_scrub_keeps_cantonese_and_traditional_text_intact():
    """社区里粤语与繁体是常态，清洗不该动它们。"""
    text = "點差太大，買賣蝕唔少"
    assert redact.scrub_text(text) == text


# ── 白名单构造 ─────────────────────────────────────────────────────────


def test_payload_contains_only_whitelisted_keys():
    p = redact.comment_payload("comment:1|product:3033", PRODUCT, "点差太大")
    assert set(p) == {"item_id", "product", "comment"}
    assert set(p["product"]) == {"code", "name"}


def test_extra_product_keys_are_dropped_not_forwarded():
    """新字段进不了请求体。黑名单的失效是静默的：加一列 `author_city`，
    黑名单不认识它，于是它被原样发出去，没有任何一处会报错。"""
    p = redact.comment_payload(
        "comment:1|product:3033",
        {**PRODUCT, "issuer_contact": "wang@csop.com", "internal_id": 77},
        "点差太大",
    )
    assert set(p["product"]) == {"code", "name"}


def test_missing_product_code_is_an_error():
    """判定单元是 (comment_id, subject_code)。没有 code 就不成立（§10.1）。"""
    with pytest.raises(RedactionError, match="产品代码"):
        redact.comment_payload("comment:1|product:", {"name": "某ETF"}, "点差太大")


def test_missing_item_id_is_an_error():
    with pytest.raises(RedactionError, match="item_id"):
        redact.comment_payload("", PRODUCT, "点差太大")


def test_none_comment_is_an_error():
    with pytest.raises(RedactionError):
        redact.comment_payload("comment:1|product:3033", PRODUCT, None)


def test_optional_context_is_omitted_rather_than_sent_as_null():
    """发 `"post_title": null` 会让模型以为帖子没有标题 —— 那是一个我们没验证过的事实。"""
    p = redact.comment_payload("comment:1|product:3033", PRODUCT, "点差太大",
                               post_title=None, parent_comment="")
    assert "post_title" not in p and "parent_comment" not in p


def test_context_is_scrubbed_too():
    p = redact.comment_payload("comment:1|product:3033", PRODUCT, "同意",
                               post_title="@小李 谈3033", parent_comment="见 https://x.io/a")
    assert "小李" not in p["post_title"]
    assert "x.io" not in p["parent_comment"]


def test_post_payload_requires_title_or_content():
    with pytest.raises(RedactionError, match="NO_TEXT"):
        redact.post_payload("feed:1", "", title=None)


def test_post_payload_keeps_only_whitelisted_keys():
    p = redact.post_payload("feed:1", "今天加了一手", title="操作记录",
                            product={**PRODUCT, "raw_json": "{...}"})
    assert set(p) == {"item_id", "content", "title", "product"}
    assert "raw_json" not in p["product"]


# ── 断言层 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        {"author_name": "老王"},
        {"item_id": "x", "user": {"nick_name": "老王"}},
        {"results": [{"ok": 1}, {"ip_region": "广东"}]},
        {"a": {"b": [{"self_description": "十年老股民"}]}},
        {"meta": {"raw_json": "{}"}},
    ],
)
def test_forbidden_keys_abort_the_request_at_any_depth(bad):
    """抛异常 ⇒ 一个字节都没发出去。"""
    with pytest.raises(RedactionError):
        redact.assert_clean(bad)


def test_clean_payload_passes_through_unchanged():
    p = redact.comment_payload("comment:1|product:3033", PRODUCT, "点差太大")
    assert redact.assert_clean(p) is p


def test_assert_clean_reports_where_the_leak_is():
    """报错要能直接指到出问题的那个调用方，否则得在 30 条的请求体里肉眼找。"""
    with pytest.raises(RedactionError, match=r"payload\.results\[1\]\.ip_region"):
        redact.assert_clean({"results": [{"ok": 1}, {"ip_region": "广东"}]})


def test_every_pii_column_of_the_users_table_is_forbidden():
    """`users` 表的每一个身份列都必须在黑名单里 —— 这一条会在表加列时提醒我们更新。"""
    from radar_db.schema import users

    pii = {"user_id", "nick_name", "follower_num", "following_num",
           "ip_region", "self_description"}
    assert pii <= redact.FORBIDDEN_KEYS
    assert pii == {c.name for c in users.columns}, (
        "users 表的列变了：请同步 redact.FORBIDDEN_KEYS 并复核 §11.4 的可发送清单"
    )
