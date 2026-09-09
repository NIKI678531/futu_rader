"""v1 蓝图。URL 前缀在 app.py 注册时给出（要拼上反代子路径 ROOT_PATH）。

端点模块靠导入注册到蓝图。PRD 第 5 章 24 个契约函数 → 23 个端点 ＋ /meta
（`delta()` 不是端点，是环比字段的内嵌形状），映射表见 docs/specs/phase-1-api-integration.md。

已落地：meta、ranges、officials。其余仍在设计源 design/radar-data.js 里由前端直接读，
按 .scratch/phase-1-api-integration/issues/ 的工单顺序逐组迁移。
"""

from flask import Blueprint

v1_bp = Blueprint("v1", __name__)

# 导入即注册路由，必须在蓝图创建之后
from . import meta  # noqa: E402,F401
from . import ranges  # noqa: E402,F401
from . import officials  # noqa: E402,F401
