"""GET /api/v1/ranges/<key> —— PRD §5 `buildRange(key)`。

响应 data 的形状与设计源 `buildRange()` 的返回完全一致（PRD §5 逐字：「响应形状与
函数返回一致」），前端直接承接，**不自行算桶**。
"""

import logging

from flask import abort, request

from core.envelope import respond
from core.range_spec import CustomRangeError
from core.ranges import build_custom_key, build_range
from providers.sentinel import MISSING

from . import v1_bp


log = logging.getLogger(__name__)


@v1_bp.get("/ranges/<key>")
def ranges(key):
    if key == "custom":
        try:
            key = build_custom_key(request.args.get("from"), request.args.get("to"))
        except CustomRangeError as exc:
            abort(400, description=str(exc))
    log.info(
        "range_request key=%s path=%s remote_addr=%s",
        key,
        request.path,
        request.remote_addr or "unknown",
    )
    data = build_range(key)
    # 未知预设 key＝没这条资源，走 404 错误信封；与「有资源但取不到值」（→ 200 +
    # unavailable）严格分开，前端才分得清「路由写错了」和「这个数暂时没有」。
    if data is MISSING:
        log.warning("range_response_not_found key=%s path=%s", key, request.path)
        abort(404, description=f"未知的区间预设 {key!r}")
    if data is None:
        log.warning(
            "range_response_unavailable key=%s path=%s "
            "diagnostics=see_core.ranges_range_unavailable",
            key,
            request.path,
        )
    else:
        log.info(
            "range_response_ok key=%s path=%s from=%s to=%s bucket_count=%s",
            key,
            request.path,
            data.get("from") if isinstance(data, dict) else "unknown",
            data.get("to") if isinstance(data, dict) else "unknown",
            len(data.get("buckets", [])) if isinstance(data, dict) else "unknown",
        )
    return respond(data)
