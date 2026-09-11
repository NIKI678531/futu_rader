"""产品监控证据组端点（PRD §5）。

- GET /api/v1/products/<code>/topics?range=d7        → `topicsFor(code, range)`
- GET /api/v1/products/<code>/kol-mentions?range=d7  → `kolMentionsFor(code, range)`
- GET /api/v1/products/<code>/evidence?ctx=d7%7Csum&polarity=neutral&n=6
                                                     → `evidenceFor(code, ctxKey, polarity, n)`

证据端点的四个参数**全部参与选取**：code 在路径上，ctx（含区间）、polarity、n 在
查询串上。ctxKey 里有竖线，前端 `encodeURIComponent` 后是 `%7C`。

关联竞品面板的证据取的是**竞品自己**的原文，所以那几次调用的 `code` 是竞品代码而不是
当前产品代码 —— 端点对此无需知情，它只按参数取数（口径在 core/evidence.py）。

400 与 404 分工：`?n=` 不是整数是**请求写错了**（400）；ctx／极性／产品代码认不出来是
**没有这个资源**（404）。两者都不回落到默认值 —— 静默给一份别的证据，页面上不会有任何
迹象说明它配错了对象。
"""

from flask import abort, request

from core.envelope import respond
from core.evidence import DEFAULT_COUNT, evidence_for, kol_mentions_for, topics_for
from providers.sentinel import MISSING

from . import range_key, v1_bp


def _count():
    """?n= 的取值。不传用契约默认值 6；钳位在 core 里做，那是口径。"""
    raw = request.args.get("n")
    if raw is None:
        return DEFAULT_COUNT
    try:
        return int(raw)
    except ValueError:
        abort(400, description=f"参数 n 必须是整数，收到 {raw!r}")


@v1_bp.get("/products/<code>/topics")
def topics_endpoint(code):
    key = range_key()
    data = topics_for(code, key)
    if data is MISSING:
        abort(404, description=f"未知的产品 {code!r} 或区间预设 {key!r}")
    return respond(data)


@v1_bp.get("/products/<code>/kol-mentions")
def kol_mentions_endpoint(code):
    key = range_key()
    data = kol_mentions_for(code, key)
    if data is MISSING:
        abort(404, description=f"未知的产品 {code!r} 或区间预设 {key!r}")
    return respond(data)


@v1_bp.get("/products/<code>/evidence")
def evidence_endpoint(code):
    ctx = request.args.get("ctx", "")
    polarity = request.args.get("polarity", "")
    data = evidence_for(code, ctx, polarity, _count())
    if data is MISSING:
        abort(
            404,
            description=f"未知的证据入口：{code!r} / ctx={ctx!r} / polarity={polarity!r}",
        )
    return respond(data)
