"""市场域叙述组端点（PRD §5）。

- GET /api/v1/hot-summaries?range=d7                        → `hotSummaryFor` 整池一份
- GET /api/v1/products/<code>/summary?range=d7              → `summaryFor(code, range)`
- GET /api/v1/products/<code>/themes?range=d7               → `themesFor(code, range, *)`
- GET /api/v1/products/<code>/negative-categories?range=d7  → `negCatsFor(code, range)`
- GET /api/v1/products/<code>/competitors?range=d7          → `competitorsFor(code, range)`
- GET /api/v1/products/<code>/compliance?range=d7           → `complianceFor(code, range)`

`themes` 不吃 `?polarity=`：一次给 `{positive, negative}`，门面按极性取用。理由与
`hot-summaries` 为什么整池下发一样，都在 core/narrative.py 的模块说明里。

404 只表示**没有这个资源**（产品代码不认识、区间预设不认识）。「有这只产品但这段
时间没有可归类主题」是 200 + 空数组；「字段不适用于同业产品」是 200 + status: 'na'。
"""

from flask import abort

from core.envelope import respond
from core.narrative import (
    compliance_for,
    competitors_for,
    hot_summaries,
    neg_cats_for,
    summary_for,
    themes_for,
)
from providers.demo import MISSING

from . import range_key, v1_bp


def _respond(data, what):
    if data is MISSING:
        abort(404, description=what)
    return respond(data)


@v1_bp.get("/hot-summaries")
def hot_summaries_endpoint():
    key = range_key()
    return _respond(hot_summaries(key), f"未知的区间预设 {key!r}")


@v1_bp.get("/products/<code>/summary")
def summary_endpoint(code):
    key = range_key()
    return _respond(summary_for(code, key), f"未知的产品 {code!r} 或区间预设 {key!r}")


@v1_bp.get("/products/<code>/themes")
def themes_endpoint(code):
    key = range_key()
    return _respond(themes_for(code, key), f"未知的产品 {code!r} 或区间预设 {key!r}")


@v1_bp.get("/products/<code>/negative-categories")
def neg_cats_endpoint(code):
    key = range_key()
    return _respond(neg_cats_for(code, key), f"未知的产品 {code!r} 或区间预设 {key!r}")


@v1_bp.get("/products/<code>/competitors")
def competitors_endpoint(code):
    key = range_key()
    return _respond(competitors_for(code, key), f"未知的产品 {code!r} 或区间预设 {key!r}")


@v1_bp.get("/products/<code>/compliance")
def compliance_endpoint(code):
    key = range_key()
    return _respond(compliance_for(code, key), f"未知的产品 {code!r} 或区间预设 {key!r}")
