"""官号域端点（PRD §5）。

- GET /api/v1/officials/posts?range=d7                 → `officialPosts(range)`
- GET /api/v1/officials/<account>/etf-mentions?range=  → `etfMentionsFor(account, range)`

两者的 data 形状都与设计源同名函数的返回一致。
"""

from flask import abort

from core.envelope import respond
from core.officials import etf_mentions_for, official_posts
from providers.demo import MISSING

from . import range_key, v1_bp


@v1_bp.get("/officials/posts")
def officials_posts():
    key = range_key()
    data = official_posts(key)
    if data is MISSING:
        abort(404, description=f"未知的区间预设 {key!r}")
    return respond(data)


@v1_bp.get("/officials/<account>/etf-mentions")
def officials_etf_mentions(account):
    key = range_key()
    data = etf_mentions_for(account, key)
    if data is MISSING:
        abort(404, description=f"未知的区间预设 {key!r} 或官号 {account!r}")
    return respond(data)
