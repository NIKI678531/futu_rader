"""GET /api/v1/progress、GET /api/v1/progress/events —— 漏斗进度与事件流，只读（ADR-0021）。

载荷怎么算见 core/progress.py。本模块只解析查询串、套信封（铁律 1：端点不算口径）。
demo provider 没有库 ⇒ data 为 None ⇒ 信封判 `unavailable`，HTTP 仍是 200。
"""

from flask import request

from core.envelope import respond
from core.progress import events_payload, progress_payload

from . import v1_bp


@v1_bp.get("/progress")
def progress():
    return respond(progress_payload(events_limit=request.args.get("limit")))


@v1_bp.get("/progress/events")
def progress_events():
    return respond(events_payload(after=request.args.get("after"), limit=request.args.get("limit")))
