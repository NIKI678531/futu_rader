"""GET /api/v1/meta 的载荷组装（PRD 第 5 章表末「常量」行）。

`/meta` 里其实是两类东西，来源不同，混在一个端点里只是因为前端要的是「启动时一次拿全」：

| 类别 | 例子 | 来源 | 变更方式 |
|---|---|---|---|
| **口径常量** | 预设区间、板块、阈值、六态图例、热度公式、口径说明文字 | `fixtures/meta.json` 手写 | 改 PRD → 改这份文件 → 守卫测试跟着改 |
| **主数据** | 当前生产产品池、官号名单、数据截至时间 | provider | 接真实库后来自库表 |

`updatedAt`（页面上的「数据截至」／「最近更新」）曾经手写在 `fixtures/meta.json` 里，
因为它长得像个常量。它不是：它是**这批数据**最后一条帖子的时间。放在常量那边的后果
不是缺失而是说谎 —— `sql` provider 接真库时，页面拿演示锚点 `2026-09-02 09:00 HKT`
给截止到 `2026-08-25` 的真数据落款，虚报一周，而页面上没有任何迹象。
现在它跟着 `master()` 走；provider 取不到就不下发这个键（见下一段）。

分开的理由：口径常量的每个字**逐字受 PRD 约束**，必须能被守卫测试盯住（见
tests/test_meta.py 的逐字断言）；主数据是数据，演示期从设计源导出、正式期从库里查，
两者的生命周期完全不同。把口径常量也塞进 provider，改一个阈值就要重跑生成脚本。

**主数据取不到时不兜底。** mysql provider 现在 `master()` 返回 None，于是 products /
officials 两个键**根本不出现**在响应里，前端据此渲染「暂不可用」。绝不下发 `[]` ——
那是在说「客户一只产品都没维护」（铁律 2）。

## `aiValidation`

第三类东西，只有一个键，来自 [ADR-0019](../../docs/adr/0019-ai-auto-publish-no-human-gate.md) §4：
页面上的 AI 结论**验证到什么程度**。枚举三档，本期恒为 `none`：

| 值 | 含义 |
|---|---|
| `none` | 模型自动生成，没有人工验证，也没有金标 —— 准确率**未知** |
| `spot_check` | 做过抽检，有一个可陈述的抽检准确率 |
| `gold` | 有金标集，有可复算的准确率 |

它和上面两类都不一样：既不是口径也不是数据，是一句**关于数据可信度的声明**。放进
`/meta` 是因为它必须和面板上那句「AI 结论由模型自动生成，未经人工验证」以及任何一次
汇报里的说法**是同一个来源** —— 三处分头写死，改了一处另外两处就开始撒谎。

两个 provider 同值，且 demo 也是 `none`：演示数据里的 AI 字段更不是验证过的。
真做了抽检要改这个值时，改的是这一份 `fixtures/meta.json`，不是前端文案。
"""

import json
import hashlib
from pathlib import Path

from providers import get_provider
from radar_db.comment_routes import (
    COMMENT_ROUTE_VERSION,
    is_ready as comment_routes_ready,
    product_pool_digest,
)
from radar_db.revisions import ai_source_version

CONSTANTS = Path(__file__).resolve().parents[1] / "fixtures" / "meta.json"


def meta_payload():
    # 每次请求重读口径常量：改 fixture 不用重启，骨架阶段的调试成本比这点开销值钱。
    payload = json.loads(CONSTANTS.read_text(encoding="utf-8"))

    provider = get_provider()
    payload.update(version_payload())
    # 在线采集状态是数据属性，不是 fixture 口径常量。两个 provider 都返回同一形状；
    # demo 明确下发 unavailable/null，不能用演示生成器里的行数冒充生产采集状态。
    payload["dataCollection"] = provider.collection_metadata()
    master = provider.master()
    if master:
        # 只并入 provider 确实给出的键。缺的键不补空值——见模块头最后一段。
        payload.update(master)

    if provider.name == "sql":
        month = provider.build_range("mtd")
        if month is not None:
            payload["presets"].append({
                key: month[key] for key in
                ("key", "days", "label", "gran", "granLabel", "benchLabel", "trendTitle")
            } | {"bucketCount": len(month["buckets"])})

    return payload


def version_payload():
    provider = get_provider()
    if provider.name != "sql":
        return {
            "dataProvider": provider.name,
            "dataRevision": "demo",
            "analysisProgress": None,
            "aiCommentRouting": {
                "ready": False,
                "ruleVersion": COMMENT_ROUTE_VERSION,
                "productPoolDigest": product_pool_digest(),
            },
        }
    source = provider._meta
    catalog_state = [
        (product["code"], product["ownership"], product.get("listingDate"), product.get("isNew"))
        for product in provider._products
    ]
    revision = hashlib.sha256(
        json.dumps({"meta": source, "catalog": catalog_state}, sort_keys=True).encode()
    ).hexdigest()
    progress = json.loads(source.get("own_analysis_progress", "null"))
    summary = None
    if progress:
        products = {
            code: dict(row) for code, row in progress.get("products", {}).items()
        }
        scope = progress.get("scope", "own")
        status = progress["status"]
        source_changed = (
            progress.get("sourceVersion", {}) != ai_source_version(source)
        )
        if source_changed:
            status = "source_changed"
            products = {
                code: {**row, "complete": False} for code, row in products.items()
            }
        total = len(products)
        done = sum(bool(row.get("complete")) for row in products.values())
        catalog_gap = set()
        if status == "complete" and scope == "all":
            expected_codes = {product["code"] for product in provider._products}
            catalog_gap = expected_codes - set(products)
            if catalog_gap:
                status = "source_changed"
                total = len(expected_codes)
                done = sum(
                    bool(products.get(code, {}).get("complete")) for code in expected_codes
                )
        state_text = {"configuration_error": "配置错误，已暂停", "lease_lost": "执行锁异常，已暂停",
                  "source_changed": "数据范围已变化，已暂停"}.get(status, "处理中")
        label = "全池分析" if scope == "all" else "自家分析"
        if catalog_gap:
            state_text = f"产品目录已更新，需补跑 {len(catalog_gap)} 只"
        summary = {"completed": done, "total": total, "status": status,
               "text": f"{label} {done}/{total} · " + ("已完成" if done == total else state_text),
                   "anchor": progress["anchor"], "products": products, "scope": scope,
                   "ranges": progress.get("ranges")}
    return {
        "dataProvider": "sql",
        "dataRevision": revision,
        "analysisProgress": summary,
        "aiCommentRouting": {
            "ready": comment_routes_ready(source),
            "ruleVersion": source.get("comment_route_version", COMMENT_ROUTE_VERSION),
            "productPoolDigest": source.get(
                "comment_route_pool_digest",
                product_pool_digest(product["code"] for product in provider._products),
            ),
        },
    }
