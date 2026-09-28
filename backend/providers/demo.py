"""演示 provider：读 fixtures/demo/ 下由设计源导出的 JSON（生成器见 fixtures/generate.mjs）。

这里**没有任何口径计算**。数值逐字来自 design/radar-data.js，本模块只做「按参数键查表」。
所以它天然满足两条要求：

- 确定性：同参数永远同结果，逐字节相同（Playwright 逐字比对的前提）。
- 演示锚点冻结：ANCHOR=2026-09-01、NOW=2026-09-02 09:00 HKT 烤进 fixture，
  这里不碰系统时间，业务逻辑里没有 datetime.now()（ADR-0012）。

查不到的参数键返回 MISSING（不是 None）—— None 是合法的「字段暂不可用」取值，
用它表示「没这个键」会把两件事混成一件（铁律 2 的同一个坑，换了个位置）。

## 场景（DEMO_SCENARIO）

演示数据是自洽生成的，字段永远齐全 —— 也就是说六态里除了 `0` 和「待确认」，其余几态在
它身上**一次都不会发生**。铁律 2 因此从来没有被真正执行过一次；等真实库接上、大量 AI
派生字段变成 `null` 的那天才发现渲染成了 `0`，就已经在向产品团队撒谎了。

所以留一个场景层：`DEMO_SCENARIO=sixstate` 时先查 `fixtures/sixstate/`，查不到再回落
`fixtures/demo/`。场景 fixture 是手工构造的（生成脚本 fixtures/make_sixstate.py），
每一条对应六态里的一态，确定性、不靠随机。默认 `DEMO_SCENARIO` 不设，行为与从前完全一致。
"""

import json
import os
from pathlib import Path

# 哨兵本体搬去了 providers/sentinel.py —— sql provider 也要用它，而 sql 去 import demo
# 是说不通的（两个平行实现，谁都不该依赖另一个）。这里 re-export 只为了让
# `from providers.demo import MISSING` 这种旧写法不至于突然报错；新代码从 sentinel 取。
from .sentinel import MISSING  # noqa: F401  （re-export，见上）

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"
FIXTURE_DIR = FIXTURE_ROOT / "demo"


class DemoProvider:
    name = "demo"

    def __init__(self, fixture_dir=None, scenario=None):
        base = Path(fixture_dir) if fixture_dir else FIXTURE_DIR
        scenario = scenario if scenario is not None else os.getenv("DEMO_SCENARIO", "")
        # 场景目录在前、基准目录在后：场景只覆盖它显式给出的那几个键，其余照常走演示数据，
        # 于是构造一个六态样本不需要把整份 fixture 复制一遍。
        self._dirs = [FIXTURE_ROOT / scenario, base] if scenario else [base]
        self.scenario = scenario
        self._cache = {}

    def _tables(self, name):
        if name not in self._cache:
            found = []
            for d in self._dirs:
                path = d / f"{name}.json"
                if path.exists():
                    found.append(json.loads(path.read_text(encoding="utf-8")))
            if not found:
                raise FileNotFoundError(
                    f"演示数据 {self._dirs[-1] / (name + '.json')} 不存在。"
                    "先跑：node backend/fixtures/generate.mjs"
                )
            self._cache[name] = found
        return self._cache[name]

    def _lookup(self, table, *parts):
        key = "|".join(str(p) for p in parts)
        for t in self._tables(table):
            if key in t:
                return t[key]
        return MISSING

    def _doc(self, name):
        """整份文档，不按参数键查表（主数据用）。场景目录优先。"""
        return self._tables(name)[0]

    def refresh(self):
        """空操作。fixture 是仓库里的静态文件，进程跑着的时候不会自己变。

        这个方法存在只是为了让 `get_provider()` 有一个统一的调用点 —— 在那边写
        `if isinstance(p, SqlProvider)` 会把 provider 的实现细节漏回接缝里（ADR-0001
        的整个意思是两个 provider 对上层没有区别）。
        """
        return False

    def collection_metadata(self):
        """演示数据没有在线采集运行，因此所有运行态字段都必须明确为未知。

        `/meta` 在 demo 与 sql provider 下保持同一契约形状，但这里不能拿 fixture 的
        编造行数冒充生产采集状态。数值用 ``None``，而不是 0：0 的含义是已经完成采集且
        确认一条都没有。
        """
        return {
            "freshness": "unavailable",
            "commentCoverage": "unknown",
            "sourceCompleteThrough": None,
            "lastSuccessfulSyncAt": None,
            "platformCommentCount": None,
            "parsedCommentCount": None,
        }

    # ── PRD §5 契约函数 ────────────────────────────────────────────────

    def master(self):
        """主数据：产品池与官号名单（/meta 的实体部分，口径常量另有来源）。

        products 是**数组**不是字典 —— 产品代码是 '3033' 这样的纯数字字符串，
        JS 对象会把它们当整数键按数值升序重排，ORDER（自家在前、竞品在后）当场丢失。
        """
        return self._doc("master")

    def build_range(self, key):
        return self._lookup("ranges", key)

    def pool(self, range_key):
        return self._lookup("pool", range_key)

    def ranks(self, range_key):
        return self._lookup("ranks", range_key)

    def benchmark(self, code, range_key):
        return self._lookup("benchmark", code, range_key)

    def hot_summaries(self, range_key):
        """整池一份 `{code: 热议总结}`，按区间查表。

        按产品切端点，板块总览那张 120 行的榜单就是 120 次串行往返 ——
        每行调一次 `hotSummaryFor`，而同步 Suspense 一次只能解决一个。
        """
        return self._lookup("hot_summaries", range_key)

    def summary_for(self, code, range_key):
        return self._lookup("summaries", code, range_key)

    def themes_for(self, code, range_key):
        """一次给两个极性 `{positive, negative}`。调用点从来都是正负各取一次。"""
        return self._lookup("themes", code, range_key)

    def neg_cats_for(self, code, range_key):
        return self._lookup("neg_cats", code, range_key)

    def competitors_for(self, code, range_key):
        return self._lookup("competitors", code, range_key)

    def compliance_for(self, code, range_key):
        return self._lookup("compliance", code, range_key)

    def topics_for(self, code, range_key):
        return self._lookup("topics", code, range_key)

    def kol_mentions_for(self, code, range_key):
        return self._lookup("kol_mentions", code, range_key)

    def evidence_for(self, code, ctx_key, polarity, n):
        """证据侧栏。参数键 `<code>|<ctxKey>|<polarity>`，其中 ctxKey 自带区间前缀。

        fixture 按区间切了 5 个文件（整份约 23 MB）：侧栏只在点开时取一次，没必要为它
        把五个区间全读进内存。表名里的区间来自 ctxKey，core 那边已经校验过它是预设之一。

        存的是**生成顺序**的 12 条 ＋ n=1..12 各自的次序（fixtures/generate.mjs 有详述）：
        设计源排序用的比较器在时间相同时返回 -1 而不是 0，同分钟两条谁在前取决于 V8 的
        实现，Python 这边重排一次就会和页面对不上。所以这里只按下标取，不排序。
        """
        pack = self._lookup("evidence_" + ctx_key.split("|")[0], code, ctx_key, polarity)
        if pack is MISSING:
            return MISSING
        return [pack["items"][i] for i in pack["orders"][n - 1]]

    def candles_for(self, code, range_key):
        return self._lookup("candles", code, range_key)

    def heat_series_for(self, code, range_key):
        return self._lookup("heat_series", code, range_key)

    def stages_for(self, code, range_key):
        return self._lookup("stages", code, range_key)

    def daily_for(self, code):
        """日度评论量／活跃账号／价格序列。四个契约函数里唯一**不吃区间**的一个 ——
        它固定给 SERIES 那 42 天，所以参数键只有产品代码。`'ALL'` 是它自己的伪代码。"""
        return self._lookup("daily", code)

    def kol_impact(self, range_key):
        return self._lookup("kol_impact", range_key)

    def kol_opinions(self, kol, range_key):
        return self._lookup("kol_opinions", kol, range_key)

    def official_posts(self, range_key):
        return self._lookup("official_posts", range_key)

    def etf_mentions_for(self, account, range_key):
        return self._lookup("etf_mentions", account, range_key)
