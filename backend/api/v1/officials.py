"""官号域端点（PRD §5）。

- GET /api/v1/officials/posts?range=d7                 → `officialPosts(range)`
- GET /api/v1/officials/<account>/etf-mentions?range=  → `etfMentionsFor(account, range)`

两者的 data 形状都与设计源同名函数的返回一致。
"""

from flask import abort, request

from core.envelope import respond
from core.officials import etf_mentions_for, official_posts
from providers.demo import MISSING

from . import v1_bp

DEFAULT_RANGE = "d7"


def _range_key():
    # 不传 range 用默认值 d7（与设计源 DEFAULT_KEY 一致）；传了但不认识的值不做兜底，
    # 直接 404 —— 静默回落会让前端拿到另一段时间的数据却浑然不觉。
    return request.args.get("range", DEFAULT_RANGE)


@v1_bp.get("/officials/posts")
def officials_posts():
    key = _range_key()
    data = official_posts(key)
    if data is MISSING:
        abort(404, description=f"未知的区间预设 {key!r}")
    return respond(data)


@v1_bp.get("/officials/<account>/etf-mentions")
def officials_etf_mentions(account):
    key = _range_key()
    data = etf_mentions_for(account, key)
    if data is MISSING:
        abort(404, description=f"未知的区间预设 {key!r} 或官号 {account!r}")
    return respond(data)
