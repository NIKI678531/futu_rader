"""价格与阶段组端点（PRD §5）。

- GET /api/v1/products/<code>/candles?range=d7      → `candlesFor(code, range)`
- GET /api/v1/products/<code>/heat-series?range=d7  → `heatSeriesFor(code, range)`
- GET /api/v1/products/<code>/stages?range=d7       → `stagesFor(code, range)`
- GET /api/v1/products/<code>/daily                 → `dailyFor(code)`

`daily` 是全组唯一**不吃 ?range=** 的端点：`dailyFor(code)` 只有一个参数，它给的是固定
42 天的完整日历。给它加一个被忽略的 `?range=` 比不加更糟 —— 传了没反应的参数，调用方
只会以为自己筛过了。

`heat-series` 与 `daily` 第一期没有屏幕读（理由见 core/prices.py）。它们仍然是端点，
因为 PRD §5 要求 24 个契约函数 1:1 对应端点；少一个，「全部落地」这句话就不成立。
"""

from flask import abort

from core.envelope import respond
from core.prices import candles_for, daily_for, heat_series_for, stages_for
from providers.sentinel import MISSING

from . import range_key, v1_bp


@v1_bp.get("/products/<code>/candles")
def candles_endpoint(code):
    key = range_key()
    data = candles_for(code, key)
    if data is MISSING:
        abort(404, description=f"未知的产品 {code!r} 或区间预设 {key!r}")
    return respond(data)


@v1_bp.get("/products/<code>/heat-series")
def heat_series_endpoint(code):
    key = range_key()
    data = heat_series_for(code, key)
    if data is MISSING:
        abort(404, description=f"未知的产品 {code!r} 或区间预设 {key!r}")
    return respond(data)


@v1_bp.get("/products/<code>/stages")
def stages_endpoint(code):
    key = range_key()
    data = stages_for(code, key)
    if data is MISSING:
        abort(404, description=f"未知的产品 {code!r} 或区间预设 {key!r}")
    return respond(data)


@v1_bp.get("/products/<code>/daily")
def daily_endpoint(code):
    data = daily_for(code)
    if data is MISSING:
        abort(404, description=f"未知的产品 {code!r}")
    return respond(data)
