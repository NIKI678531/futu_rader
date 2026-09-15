"""v1 蓝图。URL 前缀在 app.py 注册时给出（要拼上反代子路径 ROOT_PATH）。

端点模块靠导入注册到蓝图。PRD 第 5 章 24 个契约函数 → 23 个端点 ＋ /meta
（`delta()` 不是端点，是环比字段的内嵌形状），映射表见 docs/specs/phase-1-api-integration.md。

已落地：meta、ranges、officials、kol、market（pool / ranks / benchmark）、
narrative（hot-summaries / summary / themes / negative-categories / competitors / compliance）、
evidence（topics / kol-mentions / evidence）、prices（candles / heat-series / stages / daily）
—— **PRD 第 5 章的契约函数至此全部落地**。

24 个函数对 23 个端点，差的那个是 `delta()`（环比字段的内嵌形状，不是端点）。23 个端点
里实际注册 21 个，另两个有意没有：`kolProfile` 必须随前端筛选重算（ADR-0015），
`observe` 是 `pool().list` 的一个元素（core/market.py）。
"""

from flask import Blueprint, request

v1_bp = Blueprint("v1", __name__)

DEFAULT_RANGE = "d7"


def range_key():
    """?range= 的取值。不传用默认值 d7（与设计源 DEFAULT_KEY 一致）。

    传了但不认识的值**不做兜底**，一路带到 core 那边判成 MISSING → 404。静默回落到 d7
    会让前端拿到另一段时间的数据却浑然不觉 —— 页面上不会有任何迹象说明它看的不是
    自己要的区间。
    """
    return request.args.get("range", DEFAULT_RANGE)


# 导入即注册路由，必须在蓝图创建之后
from . import meta  # noqa: E402,F401
from . import ranges  # noqa: E402,F401
from . import officials  # noqa: E402,F401
from . import kol  # noqa: E402,F401
from . import market  # noqa: E402,F401
from . import narrative  # noqa: E402,F401
from . import evidence  # noqa: E402,F401
from . import prices  # noqa: E402,F401
from . import progress  # noqa: E402,F401
