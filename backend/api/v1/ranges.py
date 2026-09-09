"""GET /api/v1/ranges/<key> —— PRD §5 `buildRange(key)`。

响应 data 的形状与设计源 `buildRange()` 的返回完全一致（PRD §5 逐字：「响应形状与
函数返回一致」），前端直接承接，**不自行算桶**。
"""

from flask import abort

from core.envelope import respond
from core.ranges import build_range
from providers.demo import MISSING

from . import v1_bp


@v1_bp.get("/ranges/<key>")
def ranges(key):
    data = build_range(key)
    # 未知预设 key＝没这条资源，走 404 错误信封；与「有资源但取不到值」（→ 200 +
    # unavailable）严格分开，前端才分得清「路由写错了」和「这个数暂时没有」。
    if data is MISSING:
        abort(404, description=f"未知的区间预设 {key!r}")
    return respond(data)
