"""GET /api/v1/page-context -- actual comment and publication clocks."""

from flask import abort, request

from core.envelope import respond
from core.page_context import page_context
from providers.sentinel import MISSING

from . import range_key, v1_bp


@v1_bp.get("/page-context")
def page_context_endpoint():
    screen = request.args.get("screen", "")
    scope = "accounts" if screen in {"official", "kol", "kol_detail"} else "market"
    key = range_key(scope)
    data = page_context(
        screen,
        key,
        code=request.args.get("code"),
        account=request.args.get("account"),
        kol=request.args.get("kol"),
    )
    if data is MISSING:
        abort(404, description=f"未知的页面数据时钟：{screen!r} / {key!r}")
    return respond(data)
