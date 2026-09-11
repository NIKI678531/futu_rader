"""KOL 域端点（PRD §5）。

- GET /api/v1/kol/impact?range=d7            → `kolImpact(range)`
- GET /api/v1/kol/<name>/opinions?range=d7   → `kolOpinions(kol, range)`

`kolProfile` 没有端点：它是已下发帖子之上的纯聚合，且按 PRD §4.3 M5 必须随前端筛选
重算。理由与取舍见 core/kol.py 的模块说明与 ADR-0015。
"""

from flask import abort

from core.envelope import respond
from core.kol import kol_impact, kol_opinions

from providers.sentinel import MISSING

from . import range_key, v1_bp


@v1_bp.get("/kol/impact")
def kol_impact_endpoint():
    key = range_key()
    data = kol_impact(key)
    if data is MISSING:
        abort(404, description=f"未知的区间预设 {key!r}")
    return respond(data)


@v1_bp.get("/kol/<name>/opinions")
def kol_opinions_endpoint(name):
    key = range_key()
    data = kol_opinions(name, key)
    # 区间不认识和 KOL 不在名单里，都是「没这条资源」→ 404。
    # 「在名单里但区间内没观点」是另一回事：那是 200 + 空数组 → 页面渲染「暂无相关内容」。
    if data is MISSING:
        abort(404, description=f"未知的 KOL 或区间预设：{name!r} / {key!r}")
    return respond(data)
