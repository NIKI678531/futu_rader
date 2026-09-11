"""市场域基础组端点（PRD §5）。

- GET /api/v1/pool?range=d7                       → `pool(range)`
- GET /api/v1/ranks?range=d7                      → `ranks(range)`
- GET /api/v1/products/<code>/benchmark?range=d7  → `benchmark(code, range)`

`observe(code, range)` 没有端点：它是 `pool().list` 的一个元素，前端按 code 取。
理由见 core/market.py 的模块说明。
"""

from flask import abort

from core.envelope import respond
from core.market import benchmark, pool, ranks
from providers.demo import MISSING

from . import range_key, v1_bp


@v1_bp.get("/pool")
def pool_endpoint():
    key = range_key()
    data = pool(key)
    if data is MISSING:
        abort(404, description=f"未知的区间预设 {key!r}")
    return respond(data)


@v1_bp.get("/ranks")
def ranks_endpoint():
    key = range_key()
    data = ranks(key)
    if data is MISSING:
        abort(404, description=f"未知的区间预设 {key!r}")
    return respond(data)


@v1_bp.get("/products/<code>/benchmark")
def benchmark_endpoint(code):
    key = range_key()
    data = benchmark(code, key)
    # 池子里没这只产品，或区间预设不认识 → 404。「有这只产品但基准期取不到数」是另一回事：
    # 那是 200，字段各自为 null，前端渲染「数据暂不可用」。
    if data is MISSING:
        abort(404, description=f"未知的产品 {code!r} 或区间预设 {key!r}")
    return respond(data)
