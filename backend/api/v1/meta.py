"""GET /api/v1/meta —— 全局常量与主数据（PRD 第 5 章表末「常量」行）。

载荷怎么拼、口径常量与主数据为什么分两处来，见 core/meta.py 的模块说明。
本模块只负责把它套上信封（CLAUDE.md 铁律 1：端点不算口径，只取数）。
"""

from core.envelope import respond
from core.meta import meta_payload

from . import v1_bp


@v1_bp.get("/meta")
def meta():
    return respond(meta_payload())
