"""GET /api/v1/meta 的载荷组装（PRD 第 5 章表末「常量」行）。

`/meta` 里其实是两类东西，来源不同，混在一个端点里只是因为前端要的是「启动时一次拿全」：

| 类别 | 例子 | 来源 | 变更方式 |
|---|---|---|---|
| **口径常量** | 预设区间、板块、阈值、六态图例、热度公式、口径说明文字 | `fixtures/meta.json` 手写 | 改 PRD → 改这份文件 → 守卫测试跟着改 |
| **主数据** | 产品池（61 自家 + 59 竞品）、官号名单、数据截至时间 | provider | 接真实库后来自库表 |

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
"""

import json
from pathlib import Path

from providers import get_provider

CONSTANTS = Path(__file__).resolve().parents[1] / "fixtures" / "meta.json"


def meta_payload():
    # 每次请求重读口径常量：改 fixture 不用重启，骨架阶段的调试成本比这点开销值钱。
    payload = json.loads(CONSTANTS.read_text(encoding="utf-8"))

    master = get_provider().master()
    if master:
        # 只并入 provider 确实给出的键。缺的键不补空值——见模块头最后一段。
        payload.update(master)

    return payload
