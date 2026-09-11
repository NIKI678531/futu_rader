"""真实数据 provider —— 本地 SQLite / 生产 MySQL，同一份 SQL（ADR-0001、ADR-0002、ADR-0014）。

取数自 `worker/jobs/import_dump.py` ＋ `worker/jobs/etl.py` 落下的瘦库（`radar_db.schema`
的 `feeds` / `comments` / `mentions` / `users`）。方言由 `RADAR_DB_URL` 决定，两边跑同一份
`MetaData`；这里**只查扁平事实表，不碰 `raw_json`** —— JSON 函数在 MySQL 8 与 SQLite 之间
不通用，拆 JSON 是 worker 的活。

原名 `mysql`，改叫 `sql`：本地开发跑的是 SQLite，叫 mysql 会让人以为本地也得起一个 MySQL。
`DATA_PROVIDER=mysql` 仍然可用（见 `providers/__init__.py`）。

## 哪些字段是真的，哪些是 None

一句话：**能数出来的都是真的，要靠 AI 标注或行情源的都是 None。**

| 类别 | 例子 | 现状 |
|---|---|---|
| 计数 | 提及数、评论量、点赞、转发、互动、热度、活跃账号、排名、环比 | **真实** |
| 主数据 | 产品池、官号名单、KOL 名单 | 真实（客户维护，见下） |
| 日历 | 区间、时间桶、基准区间 | 真实（`core/calendar.py`，锚点来自 `meta_kv`） |
| 内容 | 帖子标题正文、评论正文、作者、链接 | **真实** |
| AI 标注 | 帖子类型／置信度／摘要／操作方向、态度正负中性、主题、负面类别、合规扫描、阶段观点 | **None**（标注管线已建但尚未驱动页面，ADR-0017） |
| 行情 | K 线、日线价格 | **None**（dump 里没有本产品池的价格序列） |

None 由 `core/envelope.py` 判成 `status=unavailable` + HTTP 200，界面渲染「暂不可用」。
**绝不返回 0 或 []** —— 那是在说「查过了，真的是零」（铁律 2）。

口径公式一个都不在这里实现：热度在 `core/heat.py`，环比在 `core/delta.py`，区间与桶在
`core/calendar.py`。这里只负责把行数出来（铁律 1）。

## 三条实测出来的、影响读数的事

1. **账号靠 `author_name` 认，不靠 uid。** `master.json` 里官号的 `url` 带的 user-id
   （`https://www.futunn.com/user/66362688`）在真实 `feeds.author_uid` 里**一个都不存在**
   ——那是设计源生成的演示 id。按全称匹配官号命中 15/20，按名字匹配 KOL 命中 18/32。
   没命中的账号返回的是空列表（确实没发过帖），不是 None。
2. **评论量用帖子级 `comment_count`，态度用解析出的评论表**（ADR-0011）。近 30 天实测
   两者差 11%（平台计数 109,870 / 解析到 97,730 ＝ 89.0% 覆盖），被上游截断的热帖占
   1.9%。这个差是**可陈述的**，不是隐藏的近似。
3. **`share_count` 只在 raw_json 坏掉的 0.03% 行上是未知**，那些行让所在桶的转发数与
   热度变成 None。`like_count` 与 `comment_count` 是 dump 的列，永远有值。

## 缓存

锚点冻结、底库只读且静态，所以按区间整份算一次就缓存住（和演示 provider 把 fixture
读进内存是同一个道理）。d30 一次全池扫描约 27 万行 × 0.4 秒，缓存后为 0。
"""

import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .sentinel import MISSING

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
# `radar_db/` 在仓库根，本地跑要把根加进 sys.path。镜像里它被 COPY 到 /app/radar_db
# （见 backend/Dockerfile），那时这个条件不成立，也不需要加。
if (REPO_ROOT / "radar_db").is_dir() and str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402

from core.calendar import PRESETS, build, parse_anchor  # noqa: E402
from core.delta import delta  # noqa: E402
from core.heat import heat_of  # noqa: E402
from radar_db import make_engine  # noqa: E402
from radar_db.schema import comments, feeds, mentions, meta_kv  # noqa: E402

# 产品池／官号／KOL 名单是**客户维护的主数据**，当前仓库里唯一一份在这里。
# worker/jobs/import_dump.py 用的是同一个文件（导入按它过滤），换成正式名单时两处一起换。
MASTER = BACKEND_ROOT / "fixtures" / "demo" / "master.json"


class SqlProvider:
    name = "sql"

    def __init__(self, url=None):
        self._engine = make_engine(url)
        master = json.loads(MASTER.read_text(encoding="utf-8"))
        self._products = master["products"]
        self._by_code = {p["code"]: p for p in self._products}
        self._officials = master["officials"]
        self._kols = master["kols"]
        self._meta = self._read_meta()
        self._anchor = parse_anchor(self._meta.get("anchor"))
        self._cache = {}

    # ── 底层：库 ──────────────────────────────────────────────────────

    def _read_meta(self):
        """`meta_kv` 的全部键值。库不存在或还没导入时返回空 dict —— 不抛异常：
        没接上库的环境应该看到「暂不可用」，而不是 500。"""
        try:
            with self._engine.connect() as conn:
                return {k: v for k, v in conn.execute(select(meta_kv.c.k, meta_kv.c.v))}
        except Exception:
            return {}

    def refresh(self):
        """库在进程活着的时候被重写了，就重新认一遍。每次请求入口调一次。

        这不是优化，是两个会真实发生的失效场景：

        1. **compose 把 backend 和 worker 一起拉起来，库那时是空的**（README 明写了
           这一点）。`__init__` 读到空 meta ⇒ `_anchor` 是 None ⇒ `build_range` 永远
           返回 None ⇒ **每一个端点永久「暂不可用」**，直到有人重启容器。导入跑完
           页面自己好起来，才是对的行为。
        2. **ETL 重跑**会重建 feeds/comments/mentions，而 `_scan` 的结果缓存不知道。
           所以 `jobs/etl.py` 收尾时往 `meta_kv` 写一个 `etl_generation` 计数，
           让这里看得见。

        判据是整份 `meta_kv` 相等，不是某一个键：导入会整表重写，ETL 改 generation，
        两条路径都落在这一个比较里。相等就一行不动 —— 缓存是这个 provider
        唯一的性能来源（一次全池扫描按秒计），不能因为一次探测就白白丢掉。
        """
        meta = self._read_meta()
        if meta == self._meta:
            return False
        self._meta = meta
        self._anchor = parse_anchor(meta.get("anchor"))
        self._cache.clear()
        return True

    @property
    def updated_at(self):
        """页面右上角的「数据截至」。取真实数据的最大 `posted_at`，不是系统时间。"""
        ts = self._meta.get("anchor_ts")
        return ts[:16] if ts else None

    # ── PRD §5 契约函数 ───────────────────────────────────────────────

    def master(self):
        # 主数据整份下发。没接上库也照发：产品池是客户给的，不依赖有没有帖子。
        #
        # `updatedAt` 也在这里，尽管它长得像个口径常量。它是**数据的属性**：这份库里
        # 最后一条帖子发在什么时候。留在 `fixtures/meta.json` 里的后果不是缺失而是
        # 说谎 —— 那份文件里冻着演示锚点 `2026-09-02 09:00 HKT`，而真库的数据到
        # `2026-08-25` 就断了，页面会拿演示期的日期给真数据落款，整整虚报一周。
        # 取不到时这个键**不出现**（`updated_at` 返回 None ⇒ 下面不并入），
        # 前端据此渲染「数据暂不可用」；绝不退回常量。
        out = {
            "products": self._products,
            "officials": self._officials,
            "kols": self._kols,
        }
        if self.updated_at is not None:
            out["updatedAt"] = self.updated_at
        return out

    def build_range(self, key):
        if key not in PRESETS or self._anchor is None:
            # 锚点取不到 ⇒ 整个区间是未知，不用今天兜底（见 core/calendar.parse_anchor）。
            return None
        return build(key, self._anchor)

    # ── 市场域：计数 ─────────────────────────────────────────────────

    # 产品主数据里要随观测一起下发的那几个键。`ownCode`（对位自家产品）**只有竞品有**，
    # 自家产品身上结构性不存在，所以单独处理 —— 给自家产品补一个 `ownCode: None`
    # 会让「这只自家产品的对位产品暂不可用」变成一个可表达的状态，而它并不存在。
    _PRODUCT_KEYS = (
        "code", "name", "sector", "sectorName", "struct",
        "issuer", "ownership", "listingDate", "isNew", "south",
    )

    def _observation(self, p, s, rng):
        """一只产品在某个区间上的**完整观测**。

        `pool().list` 的元素和 `benchmark().base` 必须是同一个形状：门面的
        `observe(code, range, 'bench')` 直接返回 `benchmark().base`，屏幕拿它当观测用
        （`frontend/src/data/radar.js` 的 `observe`）。原来 `base` 只发七个计数字段，
        少掉了 code / name / attitude / discussionHeat / activeAccounts 等等 ——
        屏幕今天只读它的 `buckets`，所以没炸；改天读别的就会拿到 undefined，
        而 undefined 在渲染层**不会**触发「暂不可用」，它会直接显示成空白。
        """
        out = {k: p[k] for k in self._PRODUCT_KEYS}
        if "ownCode" in p:
            out["ownCode"] = p["ownCode"]
        out.update(
            {
                "buckets": self._obs_buckets(rng, s),
                "mentions": s["mentions"],
                "comments": s["comments"],
                "interactions": s["interactions"],
                "likes": s["likes"],
                "shares": s["shares"],
                "discussionHeat": s["heat"],
                "activeAccounts": s["active"],
                "activeByBucket": s["activeByBucket"],
                # 态度分类要 AI 标注（ADR-0017）。整块 None 而不是 {positive: 0, ...}
                # —— 后者是在说「一条积极的都没有」。
                "attitude": None,
                "maxBucket": s["maxBucket"],
                "updatedAt": self.updated_at,
            }
        )
        return out

    @staticmethod
    def _obs_buckets(rng, s):
        """观测里的逐桶序列（设计源 `radar-data.js:351-371` 的 `observe` 内层 map）。

        三件事值得写下来：

        1. **`start`／`label`／`tip` 来自区间，不来自扫描结果。** `_scan` 只会数数，
           桶的日期与悬停文案是日历算出来的（`core/calendar.build`）。少了它们，
           产品监控页的趋势图悬停就没有「09-01 00:00–01:00」可显示。
        2. **基准期观测用的也是当前区间的桶标签。** 设计源里 `observe(code, k, 'bench')`
           map 的就是 `range.buckets` 本身，只换了随机盐 —— 环比是**同位**比较
           （PRD §3.1），标签标的是「第几个桶」，不是「哪一天」。
        3. **没有 `heat`。** 桶级热度另有专门端点（`heat-series`），契约里的观测桶
           只有五条计数序列 ＋ 三条态度。这里多发一个 `heat` 就是契约漂移：
           它会让人以为可以直接拿观测桶画热度曲线，而 demo provider 下没有这个键。
        """
        return [
            {
                "start": rb["day"],
                "label": rb["label"],
                "tip": rb["tip"],
                "i": sb["i"],
                "mentions": sb["mentions"],
                "comments": sb["comments"],
                "interactions": sb["interactions"],
                "likes": sb["likes"],
                "shares": sb["shares"],
                "active": sb["active"],
                # 三态要 AI 标注（ADR-0017）。0 会被读成「这一桶一条积极的都没有」。
                "positive": None,
                "negative": None,
                "neutral": None,
            }
            for rb, sb in zip(rng["buckets"], s["buckets"])
        ]

    def pool(self, range_key):
        rng = self.build_range(range_key)
        if rng is None:
            return None
        cur = self._scan(rng["from"], rng["to"], rng)
        base = self._scan(rng["benchFrom"], rng["benchTo"])

        items, global_max = [], 1
        for p in self._products:
            s = cur[p["code"]]
            global_max = max(global_max, s["maxBucket"])
            items.append(self._observation(p, s, rng))

        own = [o for o in items if o["ownership"] == "own"]
        heat = _add_all(o["discussionHeat"] for o in own)
        base_heat_own = _add_all(base[o["code"]]["heat"] for o in own)
        return {
            "list": items,
            "globalMax": global_max,
            # 三个都要 AI：alerts 与 negMentions 来自负面类别，complianceCount 来自合规扫描。
            "alerts": {p["code"]: None for p in self._products},
            "negMentions": {p["code"]: None for p in self._products},
            "complianceCount": {p["code"]: None for p in self._products},
            "baseMentions": {c: base[c]["mentions"] for c in self._by_code},
            "baseComments": {c: base[c]["comments"] for c in self._by_code},
            "baseHeat": {c: base[c]["heat"] for c in self._by_code},
            "own": {
                "count": len(own),
                "heat": heat,
                "neg": None,
                "pos": None,
                "risk": None,
                "dHeat": delta(heat, base_heat_own),
                "dNeg": delta(None, None),
                "dPos": delta(None, None),
            },
        }

    def ranks(self, range_key):
        """全市场评论量排名（PRD §3.8、铁律 3）。

        底是**完整活跃 ETF 池**（120 只全在内），板块筛选不重算 —— 所以这里不接受任何
        筛选参数，前端只过滤显示。排序与设计源一致：评论量降序，同分按 code 升序。
        """
        rng = self.build_range(range_key)
        if rng is None:
            return None
        cur = self._scan(rng["from"], rng["to"])
        order = sorted(self._by_code, key=lambda c: (-cur[c]["comments"], c))
        return {"map": {c: i + 1 for i, c in enumerate(order)}, "total": len(order)}

    def benchmark(self, code, range_key):
        # 代码不在池里是「没这个资源」→ 404；锚点取不到是「取不到值」→ 200 unavailable。
        # 两者都写成 `return None` 的话，打错一个代码看起来就跟数据源挂了一样。
        if code not in self._by_code:
            return MISSING
        rng = self.build_range(range_key)
        if rng is None:
            return None
        cur = self._scan(rng["from"], rng["to"], rng)[code]
        # 基准区间也切同样多的桶：环比要逐桶对齐同位（PRD §3.1），产品监控页的趋势图
        # 悬停要显示「这一桶较基准同位 +12（+25.0%）」。
        base = self._scan(rng["benchFrom"], rng["benchTo"], rng)[code]
        return {
            "mentions": delta(cur["mentions"], base["mentions"]),
            "comments": delta(cur["comments"], base["comments"]),
            "interactions": delta(cur["interactions"], base["interactions"]),
            "likes": delta(cur["likes"], base["likes"]),
            "shares": delta(cur["shares"], base["shares"]),
            "heat": delta(cur["heat"], base["heat"]),
            # 三项态度与活跃账号：态度要 AI，活跃账号能数。
            "positive": delta(None, None),
            "negative": delta(None, None),
            "neutral": delta(None, None),
            "accounts": delta(cur["active"], base["active"]),
            # 基准期的**完整观测**，与 pool().list 的元素同形 —— 门面的
            # `observe(code, range, 'bench')` 返回的就是这一份，屏幕拿它当观测用。
            "base": self._observation(self._by_code[code], base, rng),
            # 与 `base.buckets` **逐桶同位**的 delta 束（`fixtures/generate.mjs:169`）。
            # 五条序列与趋势图的图例键一一对应，前端只按图例开关取用 —— 所以这里
            # 不能多发也不能少发：多发的（`mentions`/`likes`/`shares`/`i`）图例里没有
            # 对应开关，少发的（`active`/`positive`/`negative`）会让开关点开一片空白。
            "buckets": [
                {
                    "comments": delta(c["comments"], b["comments"]),
                    "active": delta(c["active"], b["active"]),
                    "interactions": delta(c["interactions"], b["interactions"]),
                    # 态度两条要 AI 标注（ADR-0017），环比无从谈起 → delta(None, None)。
                    "positive": delta(None, None),
                    "negative": delta(None, None),
                }
                for c, b in zip(cur["buckets"], base["buckets"])
            ],
        }

    def heat_series_for(self, code, range_key):
        if code not in self._by_code:
            return MISSING
        rng = self.build_range(range_key)
        if rng is None:
            return None
        s = self._scan(rng["from"], rng["to"], rng)[code]
        out = []
        for b, bk in zip(rng["buckets"], s["buckets"]):
            out.append(
                {
                    "i": b["i"],
                    "day": b["day"],
                    "hour": b.get("hour"),
                    "label": b["label"],
                    "tip": b["tip"],
                    "heat": heat_of(bk["comments"], bk["likes"], bk["shares"]),
                    "mentions": bk["mentions"],
                    "comments": bk["comments"],
                    "positive": None,
                    "negative": None,
                    "neutral": None,
                }
            )
        return out

    def daily_for(self, code):
        """KOL 详情与产品监控的逐日轴。价格四项没有数据源 → None，评论与活跃是真的。"""
        if code not in self._by_code:
            return MISSING
        if self._anchor is None:
            return None
        frm = self._anchor - timedelta(days=59)
        per_day = self._by_day(code, frm, self._anchor)
        out = []
        for i in range((self._anchor - frm).days + 1):
            d = frm + timedelta(days=i)
            n_comments, active = per_day.get(d, (0, 0))
            out.append(
                {
                    "date": d.strftime("%m-%d"),
                    "iso": d.isoformat(),
                    "comments": n_comments,
                    "active": active,
                    # 行情序列不在这份 dump 里。写 None 而不是拿收盘价占位。
                    "px": None, "o": None, "c": None, "h": None, "l": None,
                }
            )
        return out

    # ── 账号域：官号 ─────────────────────────────────────────────────

    def official_posts(self, range_key):
        rng = self.build_range(range_key)
        if rng is None:
            return None
        # 官号在真实数据里只能按**全称**认（`full`），uid 对不上，见模块头第 1 点。
        by_name = {o["full"]: o for o in self._officials}
        out = []
        for row, codes in self._posts(rng, list(by_name)):
            o = by_name[row.author_name]
            is_issuer = bool(o.get("comps"))
            common = self._post_common(rng, row, codes)
            # 官号动态的契约里没有 `hour`（KOL 帖子有 —— KOL 详情页的日历轴要用）。
            # 多发一个键不会让页面出错，但它是契约漂移：下一个人会以为这是约定的一部分，
            # 然后在官号页上用它，而 demo provider 下这个键根本不存在。
            common.pop("hour")
            out.append(
                {
                    **common,
                    "id": f"of-{row.feed_id}",
                    "account": o["short"],
                    "accountFull": o["full"],
                    # 有竞品映射的是发行商官号，其余是平台运营（设计源 `o[4] > 0` 同义）。
                    "accountType": "发行商官号" if is_issuer else "平台运营",
                    "isIssuer": is_issuer,
                    **_UNANNOTATED,
                }
            )
        out.sort(key=lambda p: (-p["t"], p["id"]))
        return out

    def etf_mentions_for(self, account, range_key):
        """某官号在区间内提到的 ETF 明细（账号域「提及 ETF」口径：按出现次数累加）。

        注意这**不是**市场域的评论去重口径（PRD §3.2），两者语义相反，别混用。

        `count`（提及次数）当前必然等于 `posts`（帖子数）：`mentions` 表的主键是
        (feed_id, code)，同一篇帖子正文里把同一只 ETF 提三次，在事实层已经并成一行。
        要还原「按出现次数累加」得回到 `raw_json.summary.rich_text` 逐段数——那是 ETL
        的活，不是这里的。两列都发出去，是为了将来 ETL 补上时前端不用改。

        ## 六个键一个都不能少（2026-09-11 修）

        这里原来只发 `list` / `own` / `peer` / `etfCount` 四个键，而且 `own` / `peer`
        发的是**计数**。契约（`design/radar-data.js` 的 `etfMentionsFor`，fixture
        `fixtures/demo/etf_mentions.json` 同形）要的是六个键，其中 `own` / `peer` 是
        **两个子列表**。官号动态页 `OfficialActivity.jsx:155` 那句 `em.own.map(chipEl)`
        在 `DATA_PROVIDER=sql` 下当场 TypeError —— 整屏白，且被屏级边界报成
        「后端服务连不上」，而后端好端端的。

        排序也按契约来：**自家在前**，再按提及次数降序，再按代码升序。原来只有后两级，
        于是同一个官号在 demo 与 sql 下芯片顺序不同 —— 逐字比对抓不到（它比的是 demo），
        只有接了真库才看得见。
        """
        rng = self.build_range(range_key)
        if rng is None:
            return None
        o = next((x for x in self._officials if x["short"] == account or x["full"] == account), None)
        if o is None:
            # 名单里没有这个官号 = 没这个资源 → 404，不是「这个官号的数据暂不可用」。
            return MISSING
        rows = defaultdict(lambda: {"count": 0, "posts": 0})
        post_ids = set()
        for row, codes in self._posts(rng, [o["full"]]):
            for c in codes:
                rows[c]["count"] += 1
                rows[c]["posts"] += 1
                # 只有真提到了 ETF 的帖子才进 postCount（契约里 postSet 也在这一层写）。
                post_ids.add(row.feed_id)
        items = []
        for c, v in rows.items():
            p = self._by_code[c]
            items.append(
                {
                    "code": c,
                    "name": p["name"],
                    "short": _short_name(p["name"]),
                    "issuer": p["issuer"],
                    "ownership": p["ownership"],
                    "count": v["count"],
                    "posts": v["posts"],
                }
            )
        items.sort(key=lambda x: (0 if x["ownership"] == "own" else 1, -x["count"], x["code"]))
        return {
            "list": items,
            # 子列表取自**已排好序的** items，与契约里 `list.filter(...)` 同序。
            "own": [x for x in items if x["ownership"] == "own"],
            "peer": [x for x in items if x["ownership"] != "own"],
            "etfCount": len(items),
            "total": sum(x["count"] for x in items),
            "postCount": len(post_ids),
        }

    # ── 账号域：KOL ──────────────────────────────────────────────────

    def kol_impact(self, range_key):
        rng = self.build_range(range_key)
        if rng is None:
            return None
        by_name = {k["name"]: k for k in self._kols if k.get("active")}
        posts, seen = [], set()
        for row, codes in self._posts(rng, list(by_name)):
            k = by_name[row.author_name]
            seen.add(k["name"])
            posts.append(
                {
                    **self._post_common(rng, row, codes),
                    "id": f"kol-{row.feed_id}",
                    "kol": k["name"],
                    "tags": k["tags"],
                    "views": row.browse_count,
                    **_UNANNOTATED,
                }
            )
        posts.sort(key=lambda p: (-p["t"], p["id"]))
        return {
            "posts": posts,
            # leaders 是**全量**画像（不随筛选变化），KOL 详情页用它定顺序（见 core/kol.py）。
            # 画像里的 typeCounts / topType / styleTag 全部来自 AI 类型标注 → None。
            "leaders": self._leaders(posts),
            "kolActive": len(seen),
            "range": {
                "from": rng["from"], "to": rng["to"],
                "text": rng["text"], "days": rng["days"],
            },
            "updated": self.updated_at,
        }

    def kol_opinions(self, kol, range_key):
        # 「这位 KOL 对发帖记录之外的产品的观点与操作」——观点、操作、情绪净值三项全部
        # 来自 AI 标注（ADR-0017）。一条都算不出来，整份 None 而不是空列表：
        # 空列表是在说「他对别的产品没有观点」。
        if not any(k["name"] == kol for k in self._kols):
            return MISSING
        return None

    # ── 需要 AI 标注或行情源，本期一律 None ─────────────────────────
    #
    # 但**先验产品代码**。「这只产品的这个字段要 AI」和「没有这只产品」是两件事：
    # 前者是 200 +「暂不可用」，后者是 404。整段都回 None 的话，URL 里把 3033 敲成
    # 3O33 会得到一屏「暂不可用」—— 看起来像采集掉了数据，于是人去查采集，
    # 而那边一切正常。demo provider 查不到 fixture 键时回的就是 MISSING，
    # 两个 provider 在这件事上必须一致（`tests/test_provider_parity.py`）。

    def _unannotated(self, code):
        """产品在池里但该字段要 AI／行情源 → None；产品不在池里 → MISSING。"""
        return None if code in self._by_code else MISSING

    def hot_summaries(self, range_key):
        # 整池一份，没有 code 参数，所以没有「产品不存在」这一说。
        return None

    def summary_for(self, code, range_key):
        return self._unannotated(code)

    def themes_for(self, code, range_key):
        return self._unannotated(code)

    def neg_cats_for(self, code, range_key):
        return self._unannotated(code)

    def competitors_for(self, code, range_key):
        return self._unannotated(code)

    def compliance_for(self, code, range_key):
        return self._unannotated(code)

    def topics_for(self, code, range_key):
        return self._unannotated(code)

    def kol_mentions_for(self, code, range_key):
        # 提及本身能数，但每行要 dominantAttitude / representativeExcerpt（AI）才成立。
        return self._unannotated(code)

    def evidence_for(self, code, ctx_key, polarity, n):
        # 摘录是按极性取的，极性来自态度标注。
        # ctx / polarity 的合法性由 core/evidence.py 先验过了，这里只验产品。
        return self._unannotated(code)

    def candles_for(self, code, range_key):
        return self._unannotated(code)

    def stages_for(self, code, range_key):
        return self._unannotated(code)

    # ── 内部：扫描与聚合 ─────────────────────────────────────────────

    def _scan(self, frm, to, rng=None):
        """`[frm, to]`（含两端，自然日）内按产品聚合。`rng` 给出时同时切桶。

        返回的 dict **含产品池全部 120 只**，一条帖子都没有的也在里面（全 0）。
        缺席和零在这里必须分得开：窗口内的底库是全的，所以「没人发」是查出来的结论，
        那个 0 是真的 0；而 `KeyError` 意味着代码不在池里，是另一回事。
        """
        key = (frm, to, rng["key"] if rng else None)
        if key in self._cache:
            return self._cache[key]

        nb = len(rng["buckets"]) if rng else 0
        gran = rng["gran"] if rng else None
        origin = date.fromisoformat(frm)
        lo = datetime.combine(origin, time.min)
        hi = datetime.combine(date.fromisoformat(to) + timedelta(days=1), time.min)

        # 帖子级：评论获赞与评论作者。评论挂在帖子上，所以按帖子的 posted_at 取窗口。
        c_likes, c_authors = defaultdict(int), defaultdict(set)
        with self._engine.connect() as conn:
            q = (
                select(comments.c.feed_id, comments.c.author_uid, comments.c.like_count)
                .select_from(comments.join(feeds, feeds.c.feed_id == comments.c.feed_id))
                .where(feeds.c.posted_at >= lo, feeds.c.posted_at < hi)
            )
            for feed_id, author, like in conn.execute(q):
                if like:
                    c_likes[feed_id] += like
                if author:
                    c_authors[feed_id].add(author)

            out = {c: _blank(nb) for c in self._by_code}
            q = (
                select(
                    mentions.c.code,
                    feeds.c.feed_id,
                    feeds.c.posted_at,
                    feeds.c.author_uid,
                    feeds.c.like_count,
                    feeds.c.comment_count,
                    feeds.c.share_count,
                )
                .select_from(mentions.join(feeds, feeds.c.feed_id == mentions.c.feed_id))
                .where(
                    mentions.c.in_pool.is_(True),
                    feeds.c.posted_at >= lo,
                    feeds.c.posted_at < hi,
                )
            )
            for code, feed_id, posted, author, likes, n_comments, shares in conn.execute(q):
                s = out.get(code)
                if s is None:  # in_pool 与产品池名单不同步 —— 跳过，不要凭空造一只产品。
                    continue
                bi = _bucket(gran, origin, posted) if nb else None
                # 帖子获赞 ＋ 已采集评论获赞（HEAT_NOTE 逐字：「点赞含帖子获赞与评论获赞」）。
                like_total = (likes or 0) + c_likes.get(feed_id, 0)
                who = c_authors.get(feed_id, ())
                _bump(s, bi, 1, n_comments or 0, like_total, shares, author, who)

        for s in out.values():
            _finish(s)
        self._cache[key] = out
        return out

    def _by_day(self, code, frm, to):
        """`daily_for` 用：某产品的逐日（评论量, 活跃账号数）。

        两条查询都在 SQL 里 join 到 `mentions`，**不往 `IN (...)` 里塞 feed_id 列表**：
        SQLite 的绑定变量上限是 999，60 天的帖子随手就破。

        和 `_scan` 一样按结果缓存（约 1 秒／次，热门产品）：锚点冻结、底库只读且静态，
        同一个 code 的 60 天逐日永远是同一份。
        """
        key = ("by_day", code, frm, to)
        if key in self._cache:
            return self._cache[key]

        lo = datetime.combine(frm, time.min)
        hi = datetime.combine(to + timedelta(days=1), time.min)
        window = (mentions.c.code == code, feeds.c.posted_at >= lo, feeds.c.posted_at < hi)
        per = {}
        with self._engine.connect() as conn:
            q = (
                select(feeds.c.posted_at, feeds.c.author_uid, feeds.c.comment_count)
                .select_from(mentions.join(feeds, feeds.c.feed_id == mentions.c.feed_id))
                .where(*window)
            )
            for posted, author, n in conn.execute(q):
                cell = per.setdefault(posted.date(), [0, set()])
                cell[0] += n or 0
                if author:
                    cell[1].add(author)

            q = (
                select(feeds.c.posted_at, comments.c.author_uid)
                .select_from(
                    mentions.join(feeds, feeds.c.feed_id == mentions.c.feed_id).join(
                        comments, comments.c.feed_id == feeds.c.feed_id
                    )
                )
                .where(*window)
            )
            for posted, author in conn.execute(q):
                if author:
                    per.setdefault(posted.date(), [0, set()])[1].add(author)
        out = {d: (v[0], len(v[1])) for d, v in per.items()}
        self._cache[key] = out
        return out

    def _posts(self, rng, names):
        """区间内这批作者的帖子，附带该帖提及的产品池代码（锚点在前）。

        `names` 为空时直接返回空 —— 免得 `IN ()` 在两个方言下行为不同。
        """
        if not names:
            return []
        lo = datetime.combine(date.fromisoformat(rng["from"]), time.min)
        hi = datetime.combine(date.fromisoformat(rng["to"]) + timedelta(days=1), time.min)
        with self._engine.connect() as conn:
            rows = list(
                conn.execute(
                    select(feeds).where(
                        feeds.c.author_name.in_(names),
                        feeds.c.posted_at >= lo,
                        feeds.c.posted_at < hi,
                    )
                )
            )
            if not rows:
                return []
            # 提及同样在 SQL 里 join 回来，不拿 feed_id 列表当绑定变量（SQLite 上限 999）。
            by_feed = defaultdict(list)
            q = (
                select(mentions.c.feed_id, mentions.c.code, mentions.c.source)
                .select_from(mentions.join(feeds, feeds.c.feed_id == mentions.c.feed_id))
                .where(
                    feeds.c.author_name.in_(names),
                    feeds.c.posted_at >= lo,
                    feeds.c.posted_at < hi,
                    mentions.c.in_pool.is_(True),
                )
            )
            for feed_id, code, source in conn.execute(q):
                # 挂载标的（anchor）排前面：它是这篇帖子的主产品，`code` 字段取它。
                by_feed[feed_id].append((0 if source == "anchor" else 1, code))
        out = []
        for r in rows:
            codes = [c for _, c in sorted(by_feed.get(r.feed_id, []))]
            out.append((r, codes))
        return out

    def _post_common(self, rng, row, codes):
        """帖子行里**能数出来**的部分。AI 标注块由调用方并入。"""
        day = row.posted_at.date()
        primary = codes[0] if codes else None
        p = self._by_code.get(primary, {})
        likes, n_comments, shares = row.like_count, row.comment_count, row.share_count
        return {
            # `t` ＝ 距区间起点的小时数，设计源用它排序（`dOff * 24 + hr`）。
            "t": (day - date.fromisoformat(rng["from"])).days * 24 + row.posted_at.hour,
            "day": day.isoformat(),
            "hour": row.posted_at.hour,
            "dateText": day.strftime("%m-%d"),
            "time": row.posted_at.strftime("%m-%d %H:%M"),
            "code": primary,
            "name": p.get("name"),
            "sector": p.get("sector"),
            "ownership": p.get("ownership"),
            "issuer": p.get("issuer"),
            "likes": likes,
            "comments": n_comments,
            "shares": shares,
            "engagement": _add_all([likes, n_comments, shares]),
            "url": f"https://www.futunn.com/post/{row.feed_id}",
            "mentioned": [
                {
                    "code": c,
                    "name": self._by_code[c]["name"],
                    "issuer": self._by_code[c]["issuer"],
                    "ownership": self._by_code[c]["ownership"],
                }
                for c in codes
            ],
            "camp": _camp(self._by_code, codes),
            "campPrimary": _camp(self._by_code, codes[:1]),
        }

    def _leaders(self, posts):
        """按 KOL 汇总的全量画像。能数的数，要 AI 的留 None。"""
        by_kol = defaultdict(list)
        for p in posts:
            by_kol[p["kol"]].append(p)
        out = []
        for name, ps in by_kol.items():
            out.append(
                {
                    "kol": name,
                    "n": len(ps),
                    "own": sum(1 for p in ps if p["camp"] == "own"),
                    "peer": sum(1 for p in ps if p["camp"] == "competitor"),
                    "both": sum(1 for p in ps if p["camp"] == "both"),
                    "ownAny": sum(1 for p in ps if p["camp"] in ("own", "both")),
                    "peerAny": sum(1 for p in ps if p["camp"] in ("competitor", "both")),
                    "engagement": _add_all(p["engagement"] for p in ps),
                    "comments": _add_all(p["comments"] for p in ps),
                    # 类型分布全部来自 AI 类型标注（ADR-0017）。
                    "typeCounts": None,
                    "typeOrder": None,
                    "topType": None,
                    "topTypeLabel": None,
                    "styleTag": None,
                    "top": max(ps, key=lambda p: (p["engagement"] or 0, p["id"])),
                    # 这位 KOL 的全部帖子。契约里有（`frontend/src/lib/profile.js` 算出的
                    # 画像也带它），少发一个键前端就是 undefined —— 而 undefined 不会
                    # 触发「暂不可用」，它会静悄悄地什么都不显示。
                    "posts": ps,
                }
            )
        out.sort(key=lambda x: (-(x["engagement"] or 0), x["kol"]))
        return out


# ── 模块级小工具 ───────────────────────────────────────────────────────

# 帖子上的 AI 标注块。整块都是 None／空——**不是**「other 类型、置信度 0」，
# 那会在界面上显示成一个我们并没有做出的判断（铁律 2）。
_UNANNOTATED = {
    "postType": None,
    "typeLabel": None,
    "confidence": None,
    "direction": None,
    "directionLabel": None,
    "directionPending": None,
    "hasDir": None,
    "dir": None,
    "hasSummary": None,
    "summary": None,
    "fullText": None,
    "evidenceIdx": None,
    "typeEvidence": None,
}


def _blank(nb):
    return {
        "mentions": 0,
        "comments": 0,
        "likes": 0,
        "shares": 0,
        "sharesUnknown": False,
        "authors": set(),
        "buckets": [
            {"i": i, "mentions": 0, "comments": 0, "likes": 0, "shares": 0,
             "sharesUnknown": False, "authors": set()}
            for i in range(nb)
        ],
    }


def _bump(s, bi, mentions, n_comments, likes, shares, author, comment_authors):
    targets = [s] if bi is None or not s["buckets"] else [s, s["buckets"][bi]]
    for t in targets:
        t["mentions"] += mentions
        t["comments"] += n_comments
        t["likes"] += likes
        if shares is None:
            # raw_json 坏掉 ⇒ 这条帖子的转发数是**未知**。整桶的转发与热度随之未知。
            t["sharesUnknown"] = True
        else:
            t["shares"] += shares
        if author:
            t["authors"].add(author)
        t["authors"].update(comment_authors)


def _finish(s):
    def close(t):
        shares = None if t["sharesUnknown"] else t["shares"]
        t["shares"] = shares
        t["interactions"] = None if shares is None else t["likes"] + shares
        t["active"] = len(t["authors"])
        t["heat"] = heat_of(t["comments"], t["likes"], shares)
        del t["authors"], t["sharesUnknown"]

    for b in s["buckets"]:
        close(b)
    close(s)
    s["activeByBucket"] = [b["active"] for b in s["buckets"]]
    s["maxBucket"] = max((b["mentions"] for b in s["buckets"]), default=0)


def _bucket(gran, origin, posted):
    off = (posted.date() - origin).days
    if gran == "hour":
        return off * 24 + posted.hour
    if gran == "day":
        return off
    return off // 7


def _add_all(vals):
    """求和，`None` 传染。有一项不知道，合计就是不知道（fixtures/generate.mjs 的 addN）。"""
    total = 0
    for v in vals:
        if v is None:
            return None
        total += v
    return total


def _camp(by_code, codes):
    own = any(by_code.get(c, {}).get("ownership") == "own" for c in codes)
    peer = any(by_code.get(c, {}).get("ownership") not in (None, "own") for c in codes)
    return "both" if own and peer else ("own" if own else ("competitor" if peer else "none"))


# 两条正则与 `frontend/src/lib/view.js` 的 `shortName()` 逐字对应，那边又是
# `design/radar-data.js` 的逐字拷贝。三处同源不是重复实现：它是**展示层的缩写规则**，
# 不是口径（铁律 1 管的是公式），而 fixture 里的 `short` 正是它生成的 —— 后端不照着做，
# demo 与 sql 两个 provider 就会在同一个字段上给出两个值。
_SHORT_PREFIX = re.compile(r"^南方[東东]英")
_SHORT_SUFFIX = re.compile(r"(指數|指数)?ETF$")


def _short_name(name, n=10):
    """产品名缩写。与 `shortName()` 同口径：先去发行商前缀与 ETF 后缀，再截 10 字。

    原来这里是一个只截长度的 `_ellipsis`，于是自家产品的 `short` 在 sql 下是
    「南方東英恒生科技指…」，在 demo 下是「恒生科技」—— 同一个字段两个值，而页面上
    那一栏窄得只放得下后者。
    """
    s = _SHORT_SUFFIX.sub("", _SHORT_PREFIX.sub("", str(name or "")))
    # 整个名字都被两条正则吃掉时退回原名（与 `shortName` 的 `if (!s)` 同）。
    if not s:
        s = str(name or "")
    return s if len(s) <= n else s[:n] + "…"
