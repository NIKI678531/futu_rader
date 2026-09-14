"""发往外部模型前的脱敏 —— runbook §11.4 的白名单实现。

## 为什么是白名单而不是黑名单

黑名单（「把昵称删掉」）的失效方式是**静默的**：`feeds` 表加一列 `author_city`，
黑名单不认识它，于是它被原样发出去，没有任何一处会报错。白名单的失效方式是
**可见的**：新字段不在白名单里，它就到不了请求体，缺了谁一眼看得见。

这份数据是真实富途用户的评论，dump 里带昵称、IP 归属地和个人简介（ADR-0008）。
发出去之后就收不回来了，所以这一层宁可漏发字段，不可漏拦字段。

## 允许发送的字段（§11.4 逐条）

- 匿名 item ID（我们自己造的 `comment:123|product:3033`，不是平台 UID）
- 产品代码／名称／必要别名
- 评论或帖子正文
- 必需的帖子标题／父评论

## 禁止发送

昵称、用户 UID、IP 归属地、个人简介、粉丝／关注／访问数据、数据库连接串、内部 Token。
"""

import re

# 正文里可能混进来的身份信息。正文本身是必须发的，但用户常在评论里 @ 别人。
_AT_MENTION = re.compile(r"@[\w一-鿿㐀-䶿]{1,30}")
# 平台用户主页链接会直接暴露 UID。
_USER_URL = re.compile(r"https?://[^\s]*?/user/\d+", re.I)
# 裸 URL 可能带签名 token（§11.4 末条）。
_ANY_URL = re.compile(r"https?://\S+")

# 允许出现在产品块里的键。多一个键就是多一次泄露机会。
_PRODUCT_KEYS = ("code", "name", "aliases")


class RedactionError(ValueError):
    """脱敏失败。宁可整批不发，也不发一条不确定干净的。"""


def scrub_text(text):
    """正文清洗：去掉 @提及、用户主页链接与任何带签名风险的 URL。

    **不去掉数字**：`3033`、`-2x`、`0.99%` 这些恰恰是判断产品态度要用的（点差、费率、
    杠杆倍数）。把数字一并抹掉会让「这只ETF点差太大」失去可判断性。
    """
    if text is None:
        return None
    out = _USER_URL.sub("[链接]", text)
    out = _ANY_URL.sub("[链接]", out)
    out = _AT_MENTION.sub("@[用户]", out)
    return out


def comment_payload(item_id, product, comment, post_title=None, parent_comment=None,
                    post_context=None):
    """构造一条评论×产品的请求体（runbook §11.1 的输入形状）。

    只有这里构造的 dict 才准进请求。调用方**不要**自己拼一个 dict 传下去 ——
    那就绕过了白名单。
    """
    if not item_id:
        raise RedactionError("item_id 不能为空：输出要靠它对回输入（§11.3）")
    if comment is None:
        raise RedactionError(f"{item_id}: 评论正文为空，这条不该进队列")
    if not product:
        raise RedactionError(f"{item_id}: 产品块缺失，判定单元不成立（§10.1）")

    p = {k: product[k] for k in _PRODUCT_KEYS if product.get(k) is not None}
    if "code" not in p:
        raise RedactionError(f"{item_id}: 产品代码缺失，判定单元不成立（§10.1）")

    payload = {"item_id": item_id, "product": p, "comment": scrub_text(comment)}
    # 标题、父评论、帖子正文开头是**可选**上下文：只在确有值时带上，不要发 null 占位 ——
    # 模型看到 "post_title": null 会以为帖子没有标题，那是一个我们没验证过的事实。
    # 三样都在 §11.4 的许可清单里（「必需的帖子标题／父评论」，正文与标题同源）。
    if post_title:
        payload["post_title"] = scrub_text(post_title)
    if parent_comment:
        payload["parent_comment"] = scrub_text(parent_comment)
    if post_context:
        payload["post_context"] = scrub_text(post_context)
    return payload


def post_payload(item_id, content, title=None, product=None):
    """构造一条帖子标注的请求体（runbook §11.2）。"""
    if not item_id:
        raise RedactionError("item_id 不能为空")
    if not content and not title:
        raise RedactionError(f"{item_id}: 帖子既无标题也无正文，应走 NO_TEXT 规则，不调模型")

    payload = {"item_id": item_id, "content": scrub_text(content)}
    if title:
        payload["title"] = scrub_text(title)
    if product:
        p = {k: product[k] for k in _PRODUCT_KEYS if product.get(k) is not None}
        if p:
            payload["product"] = p
    return payload


# 一旦出现在请求体里就是事故。断言用，不是过滤用 —— 过滤会把事故变成一次静默修正。
FORBIDDEN_KEYS = frozenset(
    {
        "author_uid", "author_name", "nick_name", "user_id", "uid",
        "ip_region", "self_description", "follower_num", "following_num",
        "home_visitor_num", "sns_gender", "raw_json", "url",
    }
)


def assert_clean(payload, _path="payload"):
    """递归确认请求体里没有身份字段。在真正发出前调用（providers 里已接）。"""
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k in FORBIDDEN_KEYS:
                raise RedactionError(
                    f"{_path}.{k} 是禁止外发的身份字段（§11.4）。"
                    f"请求已中止，没有任何数据离开本机。"
                )
            assert_clean(v, f"{_path}.{k}")
    elif isinstance(payload, (list, tuple)):
        for i, v in enumerate(payload):
            assert_clean(v, f"{_path}[{i}]")
    return payload
