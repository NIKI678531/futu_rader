"""真实数据 provider —— 本地 SQLite / 生产 MySQL，同一份 SQL（ADR-0001、ADR-0002、ADR-0014）。

取数自 `worker/jobs/import_dump.py` ＋ `worker/jobs/etl.py` 落下的瘦库（`radar_db.schema`
的 `feeds` / `comments` / `mentions` / `users`）。方言由 `RADAR_DB_URL` 决定，两边跑同一份
`MetaData`；这里**只查扁平事实表，不碰 `raw_json`** —— JSON 函数在 MySQL 8 与 SQLite 之间
不通用，拆 JSON 是 worker 的活。

原名 `mysql`，改叫 `sql`：本地开发跑的是 SQLite，叫 mysql 会让人以为本地也得起一个 MySQL。
`DATA_PROVIDER=mysql` 仍然可用（见 `providers/__init__.py`）。

## 哪些字段是真的，哪些是 None

一句话：**能数出来的都是真的；要 AI 标注的随库里有没有标注走；要行情源的都是 None。**

| 类别 | 例子 | 现状 |
|---|---|---|
| 计数 | 提及数、评论量、点赞、转发、互动、热度、活跃账号、排名、环比 | **真实** |
| 主数据 | 产品池、官号名单、KOL 名单 | 真实（客户维护，见下） |
| 日历 | 区间、时间桶、基准区间 | 真实（`core/calendar.py`，锚点来自 `meta_kv`） |
| 内容 | 帖子标题正文、评论正文、作者、链接 | **真实** |
| AI 标注（有写入方） | 帖子类型／摘要／操作方向、评论态度、合规命中、证据引文 | **随标注走**：库里有现行结论就是真值，没有就是 None |
| AI 标注（无写入方） | 主题聚类、负面类别、热议话题、KOL 提及、阶段观点 | **None**（对应的 kind 还没有任何任务在写） |
| 行情 | K 线、日线价格 | **None**（dump 里没有本产品池的价格序列） |

None 由 `core/envelope.py` 判成 `status=unavailable` + HTTP 200，界面渲染「暂不可用」。
**绝不返回 0 或 []** —— 那是在说「查过了，真的是零」（铁律 2）。

## 哪一行标注算数（ADR-0019）

[ADR-0019](../../docs/adr/0019-ai-auto-publish-no-human-gate.md) 取消了人工批准门槛：
**模型写下即发布**，不再等 `review_state` 变成 `approved` / `corrected`。现行结论的定义
只剩两条 —— 链末（没有被任何一行 supersede）且不是 `rejected`；同一链末有多行时取
`created_at` 最新的那条。整个 provider 只在 `_current_annotations()` 里实现一次。

`review_state` 没有消失，它换了岗位：不再决定**能不能**显示，而决定显示哪一枚徽章
（`needs_review` ⇒ 「AI 生成 · 待确认」，见 `frontend/src/lib/view.js`）。`rejected`
是仅剩的下线通道（`worker/jobs/review.py --reject`）。

页面因此必须如实声明这些结论没有经过人工验证：`/meta` 的 `aiValidation` 恒为 `none`
（`core/meta.py`），板块总览与产品监控各有一句相应的文案。

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
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from core import stages as core_stages, themes as core_themes, topics as core_topics  # noqa: E402
from core.attitude import LOW_SAMPLE, sample_sufficient  # noqa: E402
from core.calendar import PRESETS, build, parse_anchor  # noqa: E402
from core.delta import delta  # noqa: E402
from core.heat import heat_of  # noqa: E402
from radar_db import make_engine  # noqa: E402
from radar_db.annotations_read import current_annotations  # noqa: E402
from radar_db.schema import (  # noqa: E402
    NO_SUBJECT,
    annotation_evidence,
    annotations,
    comments,
    feeds,
    mentions,
    meta_kv,
    synthesis_outputs,
)

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
        # 证据卡上的作者身份靠名单认（模块头第 1 点：uid 对不上）。官号两个写法都收，
        # 因为帖子作者名在真实数据里用全称，而契约里的 `account` 是简称。
        self._official_names = {o["full"] for o in self._officials} | {
            o["short"] for o in self._officials
        }
        self._kol_names = {k["name"] for k in self._kols}
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
        data_changed = ({key: value for key, value in meta.items() if key != "own_analysis_progress"}
                        != {key: value for key, value in self._meta.items() if key != "own_analysis_progress"})
        self._meta = meta
        self._anchor = parse_anchor(meta.get("anchor"))
        if data_changed:
            self._cache.clear()
        return data_changed

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
                "attitude": _attitude_block(s["att"]),
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
                # 这只产品在窗口内一条态度标注都没有时是 None（「还没标」），
                # 标过但这一桶没命中时是 0（「数过了，这一桶没有」）。
                "positive": _att(sb, "positive"),
                "negative": _att(sb, "negative"),
                "neutral": _att(sb, "neutral"),
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
        # 自家产品的积极内容数合计（板块总览顶部第二张卡）。有一只没标过 ⇒ 合计未知
        # （`_add_all` 的 None 传染），不是把它当 0 加进去 —— 那个数看着完全正常。
        pos = _add_all(_pos_of(o) for o in own)
        base_pos = _add_all(_att(base[o["code"]], "positive") for o in own)
        risk = self._compliance_counts(rng)
        return {
            "list": items,
            "globalMax": global_max,
            # alerts 与 negMentions 来自负面舆情类别（kind=`neg_category`），当前没有写入方。
            "alerts": {p["code"]: None for p in self._products},
            "negMentions": {p["code"]: None for p in self._products},
            "complianceCount": risk,
            "baseMentions": {c: base[c]["mentions"] for c in self._by_code},
            "baseComments": {c: base[c]["comments"] for c in self._by_code},
            "baseHeat": {c: base[c]["heat"] for c in self._by_code},
            "own": {
                "count": len(own),
                "heat": heat,
                "neg": None,
                "pos": pos,
                # 「没扫过」（None）与「扫了零条」（0）都不能当 0 加进合计，前者让合计未知。
                "risk": _add_all(risk[o["code"]] for o in own),
                "dHeat": delta(heat, base_heat_own),
                "dNeg": delta(None, None),
                "dPos": delta(pos, base_pos),
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
        baseline_range = build(range_key, date.fromisoformat(rng["benchTo"]), days_override=rng["days"])
        base = self._scan(rng["benchFrom"], rng["benchTo"], baseline_range)[code]
        return {
            "mentions": delta(cur["mentions"], base["mentions"]),
            "comments": delta(cur["comments"], base["comments"]),
            "interactions": delta(cur["interactions"], base["interactions"]),
            "likes": delta(cur["likes"], base["likes"]),
            "shares": delta(cur["shares"], base["shares"]),
            "heat": delta(cur["heat"], base["heat"]),
            "positive": delta(_att(cur, "positive"), _att(base, "positive")),
            "negative": delta(_att(cur, "negative"), _att(base, "negative")),
            "neutral": delta(_att(cur, "neutral"), _att(base, "neutral")),
            "accounts": delta(cur["active"], base["active"]),
            # 基准期的**完整观测**，与 pool().list 的元素同形 —— 门面的
            # `observe(code, range, 'bench')` 返回的就是这一份，屏幕拿它当观测用。
            "base": self._observation(self._by_code[code], base, baseline_range),
            # 与 `base.buckets` **逐桶同位**的 delta 束（`fixtures/generate.mjs:169`）。
            # 五条序列与趋势图的图例键一一对应，前端只按图例开关取用 —— 所以这里
            # 不能多发也不能少发：多发的（`mentions`/`likes`/`shares`/`i`）图例里没有
            # 对应开关，少发的（`active`/`positive`/`negative`）会让开关点开一片空白。
            "buckets": [
                {
                    "comments": delta(c["comments"], b["comments"]),
                    "active": delta(c["active"], b["active"]),
                    "interactions": delta(c["interactions"], b["interactions"]),
                    "positive": delta(_att(c, "positive"), _att(b, "positive")),
                    "negative": delta(_att(c, "negative"), _att(b, "negative")),
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
                    "positive": _att(bk, "positive"),
                    "negative": _att(bk, "negative"),
                    "neutral": _att(bk, "neutral"),
                }
            )
        return out

    def daily_for(self, code):
        """KOL 详情与产品监控的逐日轴。价格四项没有数据源 → None，评论与活跃是真的。"""
        if code not in self._by_code:
            return MISSING
        if self._anchor is None:
            return None
        frm = self._anchor - timedelta(days=41)
        per_day = self._by_day(code, frm, self._anchor)
        from radar_db.schema import price_bars
        with self._engine.connect() as conn:
            prices = {row["session_date"]: row for row in conn.execute(select(price_bars).where(
                price_bars.c.code == code, price_bars.c.interval == "1d", price_bars.c.provider == "fmp",
                price_bars.c.adjustment == "split_adjusted", price_bars.c.session_date >= frm.isoformat(),
                price_bars.c.session_date <= self._anchor.isoformat(),
            )).mappings()}
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
                          **{key: float(prices[d.isoformat()][field]) if d.isoformat() in prices else None
                              for key, field in (("px", "close"), ("o", "open"), ("c", "close"), ("h", "high"), ("l", "low"))},
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
        ai = self._post_ai(rng)
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
                    # 标注过的帖子给真值，没标注过的仍是整块缺失态（ADR-0019 §1）。
                    **ai.get(row.feed_id, _UNANNOTATED),
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
        ai = self._post_ai(rng)
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
                    **ai.get(row.feed_id, _UNANNOTATED),
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

    # ── 需要行情源的字段，本期一律 None ──────────────────────────────
    #
    # **先验产品代码**。「这只产品的这个字段要行情源」和「没有这只产品」是两件事：
    # 前者是 200 +「暂不可用」，后者是 404。整段都回 None 的话，URL 里把 3033 敲成
    # 3O33 会得到一屏「暂不可用」—— 看起来像采集掉了数据，于是人去查采集，
    # 而那边一切正常。demo provider 查不到 fixture 键时回的就是 MISSING，
    # 两个 provider 在这件事上必须一致（`tests/test_provider_parity.py`）。

    def _unannotated(self, code):
        """产品在池里但该字段要 AI／行情源 → None；产品不在池里 → MISSING。"""
        return None if code in self._by_code else MISSING

    def candles_for(self, code, range_key):
        if code not in self._by_code:
            return MISSING
        rng = self.build_range(range_key)
        if rng is None:
            return None
        from core.price_bars import aggregate_prices
        from radar_db.schema import price_bars, price_instruments, price_syncs
        interval = "30min" if rng["gran"] == "hour" else "1d"
        with self._engine.connect() as conn:
            instrument = conn.execute(select(price_instruments).where(price_instruments.c.code == code)).mappings().first()
            sync = conn.execute(select(price_syncs).where(
                price_syncs.c.code == code, price_syncs.c.interval == interval,
            ).order_by(price_syncs.c.updated_at.desc()).limit(1)).mappings().first()
            if instrument is None and sync is None:
                return None
            rows = list(conn.execute(select(price_bars).where(
                price_bars.c.code == code, price_bars.c.interval == interval,
                price_bars.c.provider == "fmp", price_bars.c.adjustment == "split_adjusted",
                price_bars.c.session_date >= rng["from"], price_bars.c.session_date <= rng["to"],
            )).mappings())
        return aggregate_prices(rng, rows, instrument["currency"] if instrument else "HKD",
                                sync["reason"] if sync else "not_synced")

    # ── 市场域：Layer B 生成物驱动的叙述组（ADR-0020） ────────────────
    #
    # 数（计数、分桶、环比、生命周期、阶段合并）全部在 `backend/core/`；`synthesis_outputs`
    # 里只有模型写的**字**（名字、句子）与它引用的证据 id。这里把两者拼起来，并且严格分三态：
    #
    # - 这只产品这个区间**一条态度标注都没有** ⇒ 整块 None（暂不可用）；
    # - 标注过但模型还没生成文字 ⇒ 数照给，文字位 None／固定名，`labelStatus='unavailable'`；
    # - 生成过 ⇒ 真值。
    #
    # 「标注过」的判据与 `_scan` 同源（`att is None` 即没标过），不另写一套。

    def _synth(self, code, range_key, kind):
        """现行生成物：`{subkey: {value, evidenceIds, reviewState}}`。链末、非 rejected；同链末取最新。"""
        if self._anchor is None:
            return {}
        if self._meta.get(f"synth_dirty_{code}_{range_key}") == "1":
            return {}
        key = ("synth", code, range_key, kind, self._anchor)
        if key in self._cache:
            return self._cache[key]
        newer = synthesis_outputs.alias("newer")
        chain_end = ~(
            select(newer.c.synthesis_id)
            .where(newer.c.supersedes_id == synthesis_outputs.c.synthesis_id)
            .exists()
        )
        out = {}
        try:
            with self._engine.connect() as conn:
                q = (
                    select(synthesis_outputs)
                    .where(
                        synthesis_outputs.c.code == code,
                        synthesis_outputs.c.range_key == range_key,
                        synthesis_outputs.c.anchor == self._anchor.isoformat(),
                        synthesis_outputs.c.kind == kind,
                        synthesis_outputs.c.review_state != "rejected",
                        chain_end,
                    )
                    .order_by(synthesis_outputs.c.created_at, synthesis_outputs.c.synthesis_id)
                )
                for r in conn.execute(q).mappings():
                    out[r["subkey"]] = {
                        "value": json.loads(r["value_json"]),
                        "evidenceIds": json.loads(r["evidence_ids_json"] or "[]"),
                        "reviewState": r["review_state"],
                    }
        except SQLAlchemyError:
            out = {}
        self._cache[key] = out
        return out

    def _units(self, code, rng):
        """区间内这只产品的判定单元（相关＋有态度），以及市场方向单元。整块 None＝没标过。"""
        key = ("units", code, rng["key"])
        if key in self._cache:
            return self._cache[key]
        lo, hi = _window(rng)
        att = current_annotations(self._engine, "attitude", "comment", window=(lo, hi), subject_code=code)
        if not att:
            self._cache[key] = None
            return None
        rel = current_annotations(self._engine, "relevance", "comment", window=(lo, hi), subject_code=code)
        asp = current_annotations(self._engine, "aspect", "comment", window=(lo, hi), subject_code=code)
        mkt = current_annotations(self._engine, "market_direction", "comment", window=(lo, hi), subject_code=code)
        units = []
        for unit, a in att.items():
            if (rel.get(unit) or {}).get("value") != "relevant":
                continue
            units.append({
                "comment_id": unit[0], "attitude": a["value"],
                "aspects": (asp.get(unit) or {}).get("value") or [], "posted_at": a["posted_at"],
                "annotation_id": (rel.get(unit) or {}).get("annotation_id"),
            })
        market = [
            {"comment_id": unit[0], "market_direction": m["value"], "posted_at": m["posted_at"]}
            for unit, m in mkt.items() if m["value"] in ("bullish", "bearish", "neutral")
        ]
        out = {"units": units, "market": market}
        self._cache[key] = out
        return out

    def _base_units(self, code, rng):
        """基准期的判定单元；基准期一条态度标注都没有 ⇒ None（环比与生命周期暂不可用）。"""
        blo = datetime.combine(date.fromisoformat(rng["benchFrom"]), time.min)
        bhi = datetime.combine(date.fromisoformat(rng["benchTo"]) + timedelta(days=1), time.min)
        att = current_annotations(self._engine, "attitude", "comment", window=(blo, bhi), subject_code=code)
        if not att:
            return None
        rel = current_annotations(self._engine, "relevance", "comment", window=(blo, bhi), subject_code=code)
        asp = current_annotations(self._engine, "aspect", "comment", window=(blo, bhi), subject_code=code)
        return [
            {"attitude": a["value"], "aspects": (asp.get(unit) or {}).get("value") or [], "posted_at": a["posted_at"]}
            for unit, a in att.items() if (rel.get(unit) or {}).get("value") == "relevant"
        ]

    @staticmethod
    def _bucket_index(rng):
        origin = date.fromisoformat(rng["from"])
        gran = rng["gran"]

        def bi(u):
            ts = u.get("posted_at")
            return None if ts is None else _bucket(gran, origin, ts)
        return bi

    def _labels(self, code, range_key, kind, split=False):
        out = {}
        for subkey, row in self._synth(code, range_key, kind).items():
            v = row["value"]
            if not isinstance(v, dict) or "title" not in v:
                continue
            k = tuple(subkey.split("|", 1)) if split else subkey
            out[k] = {"title": v.get("title"), "summary": v.get("summary"),
                      "evidence_ids": row["evidenceIds"], "reviewState": row["reviewState"]}
        return out

    def hot_summaries(self, range_key):
        rng = self.build_range(range_key)
        if rng is None:
            return None
        scan = self._scan(rng["from"], rng["to"], rng)
        out = {}
        for code in self._by_code:
            att = scan[code]["att"]
            if att is None:
                out[code] = {"status": "unavailable", "text": "数据暂不可用", "sample": None, "ok": False}
                continue
            valid = att["positive"] + att["negative"]
            if valid == 0 and att["neutral"] == 0:
                out[code] = {"status": "empty", "text": "暂无相关内容", "sample": 0, "ok": False}
                continue
            if not sample_sufficient(att["positive"], att["negative"]):
                out[code] = {"status": "low_sample", "text": "样本不足，暂无主流观点", "sample": valid, "ok": False}
                continue
            row = self._synth(code, range_key, "hot_summary").get(NO_SUBJECT)
            if row is None or not isinstance(row["value"], dict) or "text" not in row["value"]:
                out[code] = {"status": "unavailable", "text": "数据暂不可用", "sample": valid, "ok": False}
                continue
            net = att["positive"] - att["negative"]
            out[code] = {
                "status": "ok", "text": row["value"]["text"], "sample": valid,
                "tone": "pos" if net > 0 else "neg" if net < 0 else "neu", "ok": True,
                "reviewState": row["reviewState"], "evidenceIds": row["evidenceIds"],
            }
        return out

    def summary_for(self, code, range_key):
        """`{text, sample, low}`。计数句由后端按事实拼，观点句来自模型要点（Layer B）。"""
        if code not in self._by_code:
            return MISSING
        rng = self.build_range(range_key)
        if rng is None:
            return None
        s = self._scan(rng["from"], rng["to"], rng)[code]
        att = s["att"]
        if att is None:
            return None
        valid = att["positive"] + att["negative"]
        name = self._by_code[code]["name"]
        sentences = [
            f"数据显示，{name}（{code}）在 {rng['from']} 至 {rng['to']} 共识别到 {s['mentions']} 条去重提及。"
        ]
        if not s["mentions"] and valid == 0 and att["neutral"] == 0:
            return {"text": "暂无相关内容 — 在所选区间内已完成检查，该产品没有识别到提及内容。",
                    "sample": 0, "low": True, "points": [], "evidenceIds": []}
        if not sample_sufficient(att["positive"], att["negative"]):
            sentences.append(
                f"针对产品本身的有效态度提及为 {valid} 条，低于 {LOW_SAMPLE} 条的判定阈值，本区间不输出整体倾向结论。"
            )
            sentences.append(f"原始数量为积极 {att['positive']} 条、消极 {att['negative']} 条、中性 {att['neutral']} 条。")
            return {"text": "".join(sentences), "sample": valid, "low": True, "points": [], "evidenceIds": []}
        diff = att["positive"] - att["negative"]
        sentences.append(
            f"产品态度分类中积极 {att['positive']} 条、消极 {att['negative']} 条、中性 {att['neutral']} 条，"
            + ("积极与消极条数持平" if diff == 0 else f"积极比消极多 {diff} 条" if diff > 0 else f"消极比积极多 {-diff} 条")
            + "。"
        )
        row = self._synth(code, range_key, "summary").get(NO_SUBJECT)
        points, ev_ids = [], []
        if row and isinstance(row["value"], dict) and row["value"].get("points"):
            for p in row["value"]["points"]:
                t = p["text"].rstrip("。；;") + "。"
                points.append({"text": t, "evidenceIds": p.get("evidence_ids", [])})
                ev_ids.extend(p.get("evidence_ids", []))
            sentences.extend(p["text"] for p in points)
        return {
            "text": "".join(sentences), "sample": valid, "low": False, "points": points,
            "evidenceIds": list(dict.fromkeys(ev_ids)),
            "aiStatus": "ok" if points else "unavailable",
            "reviewState": row["reviewState"] if row else None,
        }

    def themes_for(self, code, range_key):
        if code not in self._by_code:
            return MISSING
        rng = self.build_range(range_key)
        if rng is None:
            return None
        u = self._units(code, rng)
        if u is None:
            return None
        return core_themes.themes(
            code, core_themes.group_by_polarity(u["units"]), self._base_units_grouped(code, rng),
            rng["buckets"], self._bucket_index(rng), self._labels(code, range_key, "theme_label", split=True),
        )

    def _base_units_grouped(self, code, rng):
        base = self._base_units(code, rng)
        return None if base is None else core_themes.group_by_polarity(base)

    def neg_cats_for(self, code, range_key):
        if code not in self._by_code:
            return MISSING
        rng = self.build_range(range_key)
        if rng is None:
            return None
        u = self._units(code, rng)
        if u is None:
            return None
        neg = [x for x in u["units"] if x["attitude"] == "negative"]
        base = self._base_units(code, rng)
        base_neg = None if base is None else [x for x in base if x["attitude"] == "negative"]
        return core_themes.neg_categories(
            code, neg, base_neg, rng["buckets"], self._bucket_index(rng),
            self._labels(code, range_key, "neg_category"),
        )

    def topics_for(self, code, range_key):
        if code not in self._by_code:
            return MISSING
        rng = self.build_range(range_key)
        if rng is None:
            return None
        u = self._units(code, rng)
        if u is None:
            return None
        label = self._labels(code, range_key, "topic_label").get(core_topics.MARKET_SUBKEY)
        baseline = current_annotations(
            self._engine, "market_direction", "comment", subject_code=code,
            window=_window({"from": rng["benchFrom"], "to": rng["benchTo"]}),
        )
        base_units = [{"market_direction": row["value"]} for row in baseline.values()] if baseline else None
        return core_topics.market_topic(
            code, u["market"], rng["buckets"], self._bucket_index(rng), label, base_units,
        )

    def stages_for(self, code, range_key):
        if code not in self._by_code:
            return MISSING
        rng = self.build_range(range_key)
        if rng is None:
            return None
        if self._scan(rng["from"], rng["to"], rng)[code]["att"] is None:
            return None
        series = self.heat_series_for(code, range_key)
        units_rows = self._synth(code, range_key, "stage_unit")
        stage_rows = self._synth(code, range_key, "stage_summary")

        def cat_of(u):
            return (units_rows.get(core_stages.unit_key(u)) or {}).get("value", {}).get("category")

        def digest_of(u):
            return (units_rows.get(core_stages.unit_key(u)) or {}).get("value", {}).get("digest")

        def summary_of(s):
            return (stage_rows.get(core_stages.stage_key(s)) or {}).get("value", {}).get("summary")

        return core_stages.build(code, series, rng["gran"], rng["days"], cat_of, digest_of, summary_of)

    def competitors_for(self, code, range_key):
        """双向：固定对位（CMAP，不经模型）＋ 模型从评论区共现识别的候选（待确认）。"""
        p = self._by_code.get(code)
        if p is None:
            return MISSING
        rng = self.build_range(range_key)
        if rng is None:
            return None
        if self._units(code, rng) is None:
            return None
        fixed = (
            [q["code"] for q in self._products if q.get("ownCode") == code]
            if p["ownership"] == "own" else ([p["ownCode"]] if p.get("ownCode") else [])
        )
        rows = self._synth(code, range_key, "competitor_reason")
        scan = self._scan(rng["from"], rng["to"], rng)
        items = []
        for c in fixed + [k for k in rows if k not in fixed]:
            q = self._by_code.get(c)
            if q is None:
                continue
            r = rows.get(c)
            v = (r or {}).get("value") or {}
            like, dislike = v.get("like_reasons") or [], v.get("dislike_reasons") or []
            n_ev = len((r or {}).get("evidenceIds") or [])
            confirmed = c in fixed
            items.append({
                "code": c, "name": q["name"], "issuer": q["issuer"], "ownership": q["ownership"],
                "relation": "confirmed" if confirmed else "auto_candidate",
                "reason": (f"客户维护的固定对位映射 · {q['issuer']}" if confirmed
                           else "AI 依据本产品评论区的共现识别 · 待确认"),
                "mentions": scan[c]["mentions"], "comments": scan[c]["comments"],
                "delta": delta(scan[c]["comments"], self._scan(rng["benchFrom"], rng["benchTo"])[c]["comments"]),
                "positiveThemes": [{"id": f"{c}-like-{i}", "title": t, "mentions": n_ev} for i, t in enumerate(like)],
                "negativeThemes": [{"id": f"{c}-dislike-{i}", "title": t, "mentions": n_ev} for i, t in enumerate(dislike)],
                "evidencePos": n_ev if like else 0, "evidenceNeg": n_ev if dislike else 0,
                "evidenceIds": (r or {}).get("evidenceIds") or [],
                "reasonStatus": "ok" if r else "unavailable",
                "reviewState": (r or {}).get("reviewState"),
            })
        return {"status": "ok" if items else "empty", "list": items}

    # ── 账号域：产品相关 KOL 与 KOL 其他产品观点 ─────────────────────

    def kol_mentions_for(self, code, range_key):
        """在该产品评论区实际提及它的合作 KOL，按有效提及评论数降序（PRD §4.2 P8）。"""
        if code not in self._by_code:
            return MISSING
        rng = self.build_range(range_key)
        if rng is None:
            return None
        u = self._units(code, rng)
        if u is None:
            return None
        src = self._sources("comment", [x["comment_id"] for x in u["units"]])
        by_kol = defaultdict(list)
        for x in u["units"]:
            r = src.get(x["comment_id"])
            if r and r["authorName"] in self._kol_names:
                by_kol[r["authorName"]].append((x, r))
        quotes = self._evidence_rows([x["annotation_id"] for xs in by_kol.values() for x, _ in xs if x["annotation_id"]])
        kols = {k["name"]: k for k in self._kols}
        items = []
        for name, xs in by_kol.items():
            xs.sort(key=lambda t: (t[1]["postedAt"] or datetime.min), reverse=True)
            counts = defaultdict(int)
            for x, _ in xs:
                counts[x["attitude"]] += 1
            n = len(xs)
            dominant = max(counts, key=counts.get) if n >= 3 else None
            latest_x, latest_r = xs[0]
            quote = (quotes.get(latest_x["annotation_id"]) or [(None, None, None)])[0][2]
            items.append({
                "productCode": code, "kolAccountId": f"kol-{name}", "kolName": name,
                "kolTags": kols[name].get("tags"), "kolType": "partner", "kolTypeLabel": "合作 KOL",
                "mentionCommentCount": n, "lastMentionedAt": _stamp(latest_r["postedAt"]),
                "dominantAttitude": dominant,
                "dominantLabel": {"positive": "积极", "negative": "消极", "neutral": "中性"}.get(dominant) if dominant else None,
                "representativeExcerpt": quote or latest_r["text"],
                "evidenceCount": n,
                "evidence": [
                    {
                        "id": f"{code}-km-{name}-{x['comment_id']}", "productCodes": [code],
                        "publishedAt": _stamp(r["postedAt"]), "authorName": name, "authorType": "合作 KOL",
                        "isKnownKol": True, "kolType": "partner", "excerpt": r["text"], "attitude": x["attitude"],
                        "comments": r["comments"], "interactions": r["interactions"], "sourceUrl": r["url"],
                    }
                    for x, r in xs[:10]
                ],
            })
        items.sort(key=lambda i: (-i["mentionCommentCount"], i["kolName"]))
        return {"status": "ok" if items else "empty", "scope": "合作 KOL 名单", "list": items}

    def kol_opinions(self, kol, range_key):
        """该 KOL 在评论里对其他产品的观点与操作（PRD §4.4 M7）。原料是 `kol_comment_opinion` 任务。"""
        if not any(k["name"] == kol for k in self._kols):
            return MISSING
        rng = self.build_range(range_key)
        if rng is None:
            return None
        lo, hi = _window(rng)
        summ = current_annotations(self._engine, "kol_summary", "comment", window=(lo, hi))
        if not summ:
            return None
        act = current_annotations(self._engine, "kol_action", "comment", window=(lo, hi))
        src = self._sources("comment", [cid for cid, _ in summ])
        quotes = self._evidence_rows([a["annotation_id"] for a in summ.values()])
        tone = {"加仓": "pos", "建仓": "pos", "减仓": "neg", "清仓": "neg", "转投其他产品": "neg",
                "持有不动": "neu", "观望": "neu", "未提及操作": "neu"}
        out = []
        for (cid, code), a in summ.items():
            r = src.get(cid)
            p = self._by_code.get(code)
            if r is None or p is None or r["authorName"] != kol:
                continue
            action = (act.get((cid, code)) or {}).get("value")
            quote = (quotes.get(a["annotation_id"]) or [(None, None, None)])[0][2]
            ts = r["postedAt"]
            out.append({
                "code": code, "name": p["name"], "issuer": p["issuer"], "own": p["ownership"] == "own",
                "sector": p["sector"], "sectorName": p.get("sectorName"),
                "summary": a["value"] if isinstance(a["value"], str) else None,
                "excerpt": quote or r["text"],
                "action": action, "actionTone": tone.get(action), "direction": action,
                "postType": None, "typeLabel": None, "confidence": None,
                "dateText": f"{ts.year}/{ts.month}/{ts.day}" if ts else None,
                "timeText": ts.strftime("%H:%M:%S") if ts else None,
                "engagement": r["interactions"], "url": r["url"], "net": None,
                "reviewState": a["review_state"],
            })
        out.sort(key=lambda o: (o["dateText"] or "", o["timeText"] or ""), reverse=True)
        return out

    # ── 市场域：标注驱动的证据与合规（ADR-0019 §1） ────────────────

    def evidence_for(self, code, ctx_key, polarity, n):
        """某条结论的原文证据：被标成这个极性的那些评论本身，按发布时间倒序。

        ctxKey ／ polarity ／ n 的合法性由 `core/evidence.py` 先验过了，这里只验产品。
        区间取 ctxKey 的头一段（它的形状就是 `<区间>|<面板 id>`）。

        ## 面板 id 不参与选取，这是一处诚实的降级

        演示数据能给五个面板各配一批不同的摘录，因为它是编的。真库里「支撑这条结论的
        原文」目前只到**评论 × 产品 × 极性**这一层：主题、话题、阶段各自的证据要
        `kind=topic_label` / `neg_category` / `market_direction` 那几类标注，而它们还
        没有写入方。所以同一只产品同一极性的几个面板会取到同一批评论 —— 它们确实都是
        这只产品的该极性原文，不是错配，只是比演示态粗。**不能**为了制造差异随机挑几条
        （`core/evidence.py` 模块头说的「不报错、数量对、格式对，只是配错了对象」）。

        整块 None ＝ 这只产品在这个区间内一条态度标注都没有（「暂不可用」）；
        空列表 ＝ 标过了，这个极性一条都没有（「暂无相关内容」）。两者不可互换。
        """
        if code not in self._by_code:
            return MISSING
        rng = self.build_range(str(ctx_key).split("|")[0])
        if rng is None:
            return None
        att = self._current_annotations("attitude", "comment", window=_window(rng))
        by_comment = defaultdict(list)
        for (cid, subject), a in att.items():
            by_comment[cid].append((subject, a))
        hits = [
            cid
            for cid, pairs in by_comment.items()
            if any(s == code and a["value"] == polarity for s, a in pairs)
        ]
        if not any(s == code for pairs in by_comment.values() for s, _a in pairs):
            return None
        src = self._sources("comment", hits)
        items = []
        for cid in hits:
            r = src.get(cid)
            if r is None:  # 标注指向一条已经不在瘦库里的评论（ETL 重跑过）。
                continue
            # 同一条评论可能同时被标了别的产品，证据卡要把它们都带上（契约里
            # `productCodes` 是数组，演示数据也会出现第二个代码）。
            others = sorted(s for s, _a in by_comment[cid] if s != code and s in self._by_code)
            items.append(
                {
                    "id": f"ev-{cid}-{code}",
                    "publishedAt": _stamp(r["postedAt"]),
                    "authorName": r["authorName"],
                    "authorType": self._author_type(r["authorName"]),
                    "excerpt": r["text"],
                    "productCodes": [code] + others,
                    "comments": r["comments"],
                    "interactions": r["interactions"],
                    "sourceUrl": r["url"],
                }
            )
        # 与设计源同序：发布时间倒序。同刻的按 id 兜底，免得两次请求两个顺序。
        items.sort(key=lambda x: (x["publishedAt"] or "", x["id"]), reverse=True)
        return items[:n]

    def compliance_for(self, code, range_key):
        """需合规关注（PRD §4.2 P10）。四态各有各的意思，一个都不能合并：

        - `na`          同业产品不纳入识别 —— 字段不适用，不是「没查到」。
        - `unavailable` 这个区间的合规识别没跑过 —— 该有值但取不到。
        - `empty`       扫过了，这只产品这段时间确实没有命中。
        - `ok`          有命中，每条带 `rationale` 与可回到原文的引文。
        """
        p = self._by_code.get(code)
        if p is None:
            return MISSING
        rng = self.build_range(range_key)
        if rng is None:
            return None
        if p["ownership"] != "own":
            return {"status": "na", "list": []}
        scan = self._compliance_scan(rng)
        if code not in scan["scannedCodes"]:
            return {"status": "unavailable", "list": []}
        hits = scan["byCode"].get(code, [])
        return {"status": "ok" if hits else "empty", "list": hits}

    def _compliance_counts(self, rng):
        """`pool().complianceCount`：每只产品的命中条数。

        与 `fixtures/generate.mjs` 逐字同口径 —— `ok` 给条数、`empty` 给 0、
        `na` 与 `unavailable` 都给 **None**。设计源那句 `|| 0` 把「没扫过」读成
        「零条」，板块总览顶部就会多报一个它并不知道的数（这也是逐字比对白名单里
        唯一那条有意偏差的由来）。
        """
        scan = self._compliance_scan(rng)
        out = {}
        for c, p in self._by_code.items():
            if p["ownership"] != "own" or c not in scan["scannedCodes"]:
                out[c] = None
            else:
                out[c] = len(scan["byCode"].get(c, []))
        return out

    def _compliance_scan(self, rng):
        """按产品读取现行合规结论。v2 写 `tags`，兼容旧行的 `risk_tags`。

        空标签是「已分析但未命中」，不是风险条目；只有该产品自己的标注能证明它已分析。
        没有标注的产品仍是 unavailable，不因其他产品有结果就显示零条。
        """
        key = ("compliance", rng["key"])
        if key in self._cache:
            return self._cache[key]

        window = _window(rng)
        rows = {
            tt: self._current_annotations("compliance", tt, window=window)
            for tt in ("comment", "feed")
        }
        scanned_codes = set()
        ev = self._evidence_rows(
            [a["annotation_id"] for table in rows.values() for a in table.values()]
        )
        by_code = defaultdict(list)
        for target_type, source_kind in (("comment", "评论"), ("feed", "帖子")):
            src = self._sources(target_type, [tid for tid, _s in rows[target_type]])
            for (target_id, code), a in rows[target_type].items():
                r = src.get(target_id)
                if r is None or code not in self._by_code:
                    continue
                value = a["value"]
                if not isinstance(value, dict) or not isinstance(value.get("tags", value.get("risk_tags")), list):
                    continue
                scanned_codes.add(code)
                tags = list(value.get("tags", value.get("risk_tags")) or [])
                if not tags:
                    continue
                quotes = ev.get(a["annotation_id"], [])
                author = r["authorName"]
                author_type = self._author_type(author)
                by_code[code].append(
                    {
                        "id": f"cr-{a['annotation_id']}",
                        "productCodes": [code],
                        "publishedAt": _stamp(r["postedAt"]),
                        "authorName": author,
                        "authorType": author_type,
                        "isKnownKol": author_type == "合作 KOL",
                        # 有定位到的引文就用引文（读的人要能在原文里逐字找到它），
                        # 没有就退回整段原文 —— 不截断，截断出来的「引文」定位不到。
                        "excerpt": quotes[0][2] if quotes else r["text"],
                        "riskTags": tags,
                        "riskLabels": [_RISK_LABEL.get(t, t) for t in tags],
                        "detectionRationale": value.get("rationale"),
                        # PRD §4.2 P10 逐字，**恒定**，不随 review_state 变
                        # （ADR-0019 §2 表末行）。
                        "reviewState": "ai_pending",
                        "reviewLabel": "AI 识别 · 待人工确认",
                        "sourceKind": source_kind,
                        "sourceUrl": r["url"],
                        "comments": r["comments"],
                        "interactions": r["interactions"],
                    }
                )
        for items in by_code.values():
            items.sort(key=lambda x: (x["publishedAt"] or "", x["id"]), reverse=True)
        out = {"scannedCodes": scanned_codes, "byCode": dict(by_code)}
        self._cache[key] = out
        return out

    # ── 内部：AI 标注读取（ADR-0019 §1） ────────────────────────────

    def _current_annotations(self, kind, target_type, ids=None, window=None):
        """某个 kind 的**现行结论**，键为判定单元的后两半 `(target_id, subject_code)`。

        ADR-0019 §1 的规则（链末、非 rejected、同链末取最新、rejected 链末＝无结论）
        **只实现一次**，在 `radar_db/annotations_read.current_annotations` —— worker 的
        Layer B（`jobs/synthesize.py`）按同一条规则取评论标注组事实，两侧共用那一份。
        这里只是把 provider 的引擎递过去。

        `window=(lo, hi)` 按**帖子的** `posted_at` 收半开窗口，与 `_scan` / `_posts` 用的
        是同一个区间；评论的 `posted_at` 在瘦库里大量为 NULL。
        """
        return current_annotations(self._engine, kind, target_type, ids=ids, window=window)

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

        # 态度：按判定单元（评论 × 产品）数正／负／中（ADR-0019 §1 的现行结论规则）。
        #
        # 归属看 `annotations.subject_code`，**不按 `mentions` 摊开**。模型判的是「这条
        # 评论对 3033 是负面的」；同一条评论正文里顺带提到的另一只产品它没判过，摊过去
        # 就是替它表过一次没表过的态。于是会出现「这只产品有 40 条评论、态度却是
        # 暂不可用」——那正是当前的实情，不是漏算。
        #
        # 分母也不是评论总数：只有被标注过的评论进这三个计数。「有效态度提及」本来
        # 就是标注出来的子集（PRD §3.5），这个口径在 fixture 与真库下同名同义。
        for (_comment_id, code), a in self._current_annotations(
            "attitude", "comment", window=(lo, hi)
        ).items():
            s = out.get(code)
            if s is None:  # 标注里的产品不在当前池 —— 同 in_pool 那条，跳过。
                continue
            _bump_att(s, _bucket(gran, origin, a["posted_at"]) if nb else None, a["value"])

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

    # ── 内部：标注周边取数 ───────────────────────────────────────────

    def _post_ai(self, rng):
        """区间内帖子的 AI 标注块，键为 `feed_id`。没标注过的帖子不在返回值里。

        ## 为什么以 `post_type` 为准判「标没标过」

        前端对整块的判据是 `postType == null ⇒ 这篇还没标`（`view.js` 的空值适配），
        而 `worker/jobs/annotate.py` 的 post_annotation 任务永远先写 `post_type` 这一行
        （`_write` 里三个 kind 的顺序固定）。所以「有没有 `post_type` 现行结论」与
        「这篇帖子标没标过」同义，不用再去查 `annotation_runs`。

        `direction` 与 `summary` 各自缺失时**不**跟着整块走：它们留 None（不知道），
        不是 False（一个确切的「没有」）。模型明确写下的 `summary=false` 是一条结论
        （「这篇没有可摘的内容」），和「这一行压根没写」是两回事。
        """
        key = ("post_ai", rng["key"])
        if key in self._cache:
            return self._cache[key]
        window = _window(rng)
        types = _by_target(self._current_annotations("post_type", "feed", window=window))
        if not types:
            self._cache[key] = {}
            return {}
        summaries = _by_target(self._current_annotations("summary", "feed", window=window))
        directions = _by_target(self._current_annotations("direction", "feed", window=window))
        src = self._sources("feed", list(types))
        # 证据行挂在这篇帖子第一个写下的 kind 上，也就是 `post_type`（annotate.py 的
        # `first_id`）。换句话说「类型判定依据」的偏移量就在这一行下面。
        ev = self._evidence_rows([a["annotation_id"] for a in types.values()])

        out = {}
        for feed_id, a in types.items():
            spans = _sentences((src.get(feed_id) or {}).get("text") or "")
            full_text = [t for _s, _e, t in spans]
            quotes = ev.get(a["annotation_id"], [])
            idx = _sentence_index(spans, quotes[0][0]) if quotes else -1
            ptype = a["value"]
            out[feed_id] = {
                "postType": ptype,
                # 枚举外的值 ⇒ KeyError。库里写进了一个我们没有的类型，不能悄悄当成
                # 「其他」显示 —— 那是替模型作了一个它没作的判断（同 `_bump_att`）。
                "typeLabel": _TYPE_LABEL[ptype],
                # ADR-0017 §4／ADR-0019 §2：这一列当前恒为 NULL，照发，不伪造一个数。
                "confidence": a["confidence"],
                # ADR-0019 §2：徽章文案由它决定，不再由它决定能不能显示。
                "reviewState": a["review_state"],
                "fullText": full_text,
                "evidenceIdx": idx,
                # 设计源 `radar-data.js:1244` 逐字：定位不到就是空串，不是整段原文。
                "typeEvidence": full_text[idx] if idx >= 0 else "",
                **_summary_block(summaries.get(feed_id)),
                **_direction_block(directions.get(feed_id)),
            }
        self._cache[key] = out
        return out

    def _sources(self, target_type, ids):
        """标注指向的原文行，键为 `target_id`，值是两种来源共用的一个形状。

        `text` 对帖子是 `title` 与 `content` 用换行接起来的那**一整串** —— 必须和
        `worker/jobs/annotate.py:542` 送进模型、`annotation_evidence.start_offset`
        所索引的那一串逐字节相同。差一个字符，偏移量就落到别处，「判定依据」会高亮到
        半句话上，而且不会报错。
        """
        ids = list(dict.fromkeys(ids))
        if not ids:
            return {}
        out = {}
        with self._engine.connect() as conn:
            for chunk in _chunked(ids, 900):
                if target_type == "comment":
                    q = (
                        select(
                            comments.c.comment_id,
                            comments.c.content,
                            comments.c.author_name,
                            comments.c.posted_at.label("own_posted"),
                            feeds.c.feed_id,
                            feeds.c.posted_at.label("feed_posted"),
                            feeds.c.comment_count,
                            feeds.c.like_count,
                            feeds.c.share_count,
                        )
                        .select_from(
                            comments.join(feeds, feeds.c.feed_id == comments.c.feed_id)
                        )
                        .where(comments.c.comment_id.in_(chunk))
                    )
                    for r in conn.execute(q):
                        out[r.comment_id] = {
                            "text": r.content,
                            "authorName": r.author_name,
                            # 评论自己的时间在瘦库里大量为 NULL，退回所在帖子的发布
                            # 时间 —— 和 `_scan` 给评论归桶用的是同一个口径。
                            "postedAt": r.own_posted or r.feed_posted,
                            "comments": r.comment_count,
                            "interactions": _add_all([r.like_count, r.share_count]),
                            "url": f"https://www.futunn.com/post/{r.feed_id}",
                        }
                else:
                    q = select(
                        feeds.c.feed_id,
                        feeds.c.title,
                        feeds.c.content,
                        feeds.c.author_name,
                        feeds.c.posted_at,
                        feeds.c.comment_count,
                        feeds.c.like_count,
                        feeds.c.share_count,
                    ).where(feeds.c.feed_id.in_(chunk))
                    for r in conn.execute(q):
                        out[r.feed_id] = {
                            "text": "\n".join(x for x in (r.title, r.content) if x),
                            "authorName": r.author_name,
                            "postedAt": r.posted_at,
                            "comments": r.comment_count,
                            "interactions": _add_all([r.like_count, r.share_count]),
                            "url": f"https://www.futunn.com/post/{r.feed_id}",
                        }
        return out

    def _evidence_rows(self, annotation_ids):
        """证据行，按 `annotation_id` 分组，组内按起始偏移升序。

        值是 `(start_offset, end_offset, quote_text)`。两样都要：`quote_text` 是写入时
        从原文里切下来的那一段，用来显示；偏移量用来算它落在第几句。
        """
        ids = list(dict.fromkeys(annotation_ids))
        if not ids:
            return {}
        out = defaultdict(list)
        try:
            with self._engine.connect() as conn:
                for chunk in _chunked(ids, 900):
                    q = select(
                        annotation_evidence.c.annotation_id,
                        annotation_evidence.c.start_offset,
                        annotation_evidence.c.end_offset,
                        annotation_evidence.c.quote_text,
                    ).where(annotation_evidence.c.annotation_id.in_(chunk))
                    for r in conn.execute(q):
                        out[r.annotation_id].append(
                            (r.start_offset, r.end_offset, r.quote_text)
                        )
        except SQLAlchemyError:
            # 同 `_current_annotations`：没跑过 Alembic 的库没有这张表 ⇒ 「没有证据」，
            # 页面退成「AI 生成」而不是 500。
            return {}
        for v in out.values():
            v.sort(key=lambda x: (-1 if x[0] is None else x[0], x[1] or 0))
        return dict(out)

    def _author_type(self, name):
        """证据卡上的作者身份，与设计源 `radar-data.js:805` 同三档。

        那边按随机数掷，这边按主数据名单认：官号按全称／简称、KOL 按名字（模块头
        第 1 点）。认不出来的是「普通散户」—— 这是**默认档**不是猜测：名单之外的
        富途用户就是散户，没有第四种身份。
        """
        if not name:
            return None
        if name in self._official_names:
            return "官方账号"
        if name in self._kol_names:
            return "合作 KOL"
        return "普通散户"


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
    # ADR-0019 §2：徽章由 `review_state` 驱动。没标注过的帖子这里是 None ⇒ 前端不出
    # 任何 AI 徽章（`null` 不是 `pending`）。
    "reviewState": None,
}


def _chunked(items, n):
    """按 n 切批。SQLite 的绑定变量上限是 999（ADR-0016），`IN (...)` 一次塞不下整池。"""
    return [items[i : i + n] for i in range(0, len(items), n)]


def _zero_att():
    return {"positive": 0, "negative": 0, "neutral": 0}


def _blank(nb):
    return {
        "mentions": 0,
        "comments": 0,
        "likes": 0,
        "shares": 0,
        "sharesUnknown": False,
        "authors": set(),
        # 先按 0 数，`_finish` 再决定这三个 0 是「数出来的零」还是「还没标注」。
        "att": _zero_att(),
        "attSeen": False,
        "buckets": [
            {"i": i, "mentions": 0, "comments": 0, "likes": 0, "shares": 0,
             "sharesUnknown": False, "authors": set(), "att": _zero_att()}
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


def _bump_att(s, bi, val):
    """一条现行态度结论计入产品与它所在的桶。`val` 不在三态里 ⇒ KeyError：
    那是库里写进了一个我们没有的枚举值，不能悄悄跳过当成「这条没标」。"""
    s["attSeen"] = True
    targets = [s] if bi is None or not s["buckets"] else [s, s["buckets"][bi]]
    for t in targets:
        t["att"][val] += 1


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
    # 这只产品在窗口内一条态度标注都没有 ⇒ 整块（含每一桶）是 None，不是三个 0。
    # 判据在**产品**这一层，不在桶上：标注过的产品，空桶里那个 0 是数出来的结论
    # （这一桶确实没有被标过态度的评论），与「这只产品还没标过」不是一回事。
    if not s.pop("attSeen"):
        s["att"] = None
        for b in s["buckets"]:
            b["att"] = None
    s["activeByBucket"] = [b["active"] for b in s["buckets"]]
    s["maxBucket"] = max((b["mentions"] for b in s["buckets"]), default=0)


def _bucket(gran, origin, posted):
    off = (posted.date() - origin).days
    if gran == "hour":
        return off * 24 + posted.hour
    if gran == "day":
        return off
    return off // 7


def _att(t, key):
    """扫描结果里的一项态度计数。整块 None（还没标注）时逐项也是 None，不是 0。"""
    a = t["att"]
    return None if a is None else a[key]


def _attitude_block(att):
    """观测里的 `attitude` 块。`sampleSufficient` 的口径在 `core/attitude.py`（铁律 1）。"""
    if att is None:
        return None
    return {**att, "sampleSufficient": sample_sufficient(att["positive"], att["negative"])}


def _pos_of(observation):
    a = observation["attitude"]
    return None if a is None else a["positive"]


def _add_all(vals):
    """求和，`None` 传染。有一项不知道，合计就是不知道（fixtures/generate.mjs 的 addN）。"""
    total = 0
    for v in vals:
        if v is None:
            return None
        total += v
    return total


def _window(rng):
    """区间的半开时间窗 `[lo, hi)`，与 `_scan` / `_posts` 用的是同一个口径。"""
    lo = datetime.combine(date.fromisoformat(rng["from"]), time.min)
    hi = datetime.combine(date.fromisoformat(rng["to"]) + timedelta(days=1), time.min)
    return lo, hi


def _stamp(dt):
    """契约里的 `publishedAt`：`YYYY-MM-DD HH:MM`（设计源 `radar-data.js:812` 同形）。"""
    return None if dt is None else dt.strftime("%Y-%m-%d %H:%M")


def _by_target(current):
    """把 `_current_annotations` 的 `(target_id, subject_code)` 键压成 `target_id`。

    帖子级标注的 `subject_code` 恒为 `NO_SUBJECT`（空串，ADR-0017 §2），所以这一步
    只是去掉一个恒定的半边。万一真出现同一帖子多个 subject，取 `created_at` 最新的
    ——和 ADR-0019 §1 ③ 同一条规则，不让库的返回顺序决定结果。
    """
    out = {}
    for (target_id, _subject), a in current.items():
        prev = out.get(target_id)
        if prev is not None and (prev["created_at"], prev["annotation_id"]) >= (
            a["created_at"],
            a["annotation_id"],
        ):
            continue
        out[target_id] = a
    return out


# 八类内容形式，逐字来自 `design/radar-data.js:993` 的 `POST_TYPES`。这里只要 label，
# 配色（`TYPE_GROUP`）是前端的事。
_TYPE_LABEL = {
    "showcase": "晒单",
    "action": "操作宣言",
    "market": "行情解读",
    "promo": "产品推介",
    "edu": "教学科普",
    "event": "活动/福利",
    "qa": "问答/互动",
    "other": "其他",
}

# 五个操作方向及其色调，逐字来自 `design/radar-data.js:1017` 的 `DIRECTIONS` / `DIR_TONE`。
_DIR_TONE = {
    "pos": {"bg": "var(--positive-100)", "fg": "var(--positive-700)"},
    "neg": {"bg": "var(--negative-100)", "fg": "var(--negative-700)"},
    "neu": {"bg": "var(--ink-100)", "fg": "var(--ink-600)"},
    "pending": {"bg": "var(--warning-100)", "fg": "var(--warning-700)"},
}
_DIRECTIONS = {
    "add": ("加仓", "pos"),
    "open": ("建仓", "pos"),
    "reduce": ("减仓", "neg"),
    "close": ("清仓", "neg"),
    "hold": ("持有观望", "neu"),
}

# 五类风险标签，逐字来自 `design/radar-data.js:1439` 的 `RISK_TAG`（＝ PRD §4.2 P10）。
_RISK_LABEL = {
    "regulatory_complaint": "监管举报",
    "serious_allegation": "严重指控",
    "unverified_claim": "疑似未经证实指控",
    "mobilization": "煽动扩散",
    "compliance_concern": "合规质疑",
}


def _dir_style(k, pending):
    """方向徽章的文案与取色，与 `design/radar-data.js:1029` 的 `dirStyle()` 逐字同。

    这是**展示层规则**不是口径（同 `_short_name` 那段的理由）：fixture 里 `dir` 这个
    对象就是它生成的，后端不照着做，demo 与 sql 会在同一个字段上给出两个形状。
    """
    if pending or k not in _DIRECTIONS:
        return {"k": "pending", "label": "方向待确认", **_DIR_TONE["pending"], "tone": "pending"}
    label, tone = _DIRECTIONS[k]
    return {"k": k, "label": label, **_DIR_TONE[tone], "tone": tone}


def _summary_block(a):
    """`hasSummary` / `summary`（设计源 `annotate()` 的对应两键）。

    `summary=false` 是模型给出的**占位结论**（`annotate.py` 允许写这个值）：这篇没有
    可摘的内容。它渲染成「无摘要」，与「摘要暂不可用」是两句不同的话，不能合并。
    没有这一行 ⇒ 两个键都是 None（还没标）。
    """
    if a is None:
        return {"hasSummary": None, "summary": None}
    v = a["value"]
    if not v:
        # 与设计源同：`hasSummary` 为假时 `summary` 是空串，不是 None——它不是缺失。
        return {"hasSummary": False, "summary": ""}
    return {"hasSummary": True, "summary": v}


def _direction_block(a):
    """`direction` / `directionLabel` / `directionPending` / `hasDir` / `dir`。

    三种写入值三种意思（`annotate.py` 的 direction kind）：

    - `"pending"`   模型看了，判不出方向 ⇒ 「待确认」，`directionPending=True`。
    - `false`       模型看了，这篇不是操作类帖子 ⇒ 四个键都是确切的「没有」。
    - 五个枚举之一   正常方向。

    没有这一行 ⇒ 全 None（还没标）。`dir` 在没有方向时是 **None** 而不是一个
    pending 样式对象 —— 设计源 `radar-data.js:1242` 的 `(dirK || dirPending) ? … : null`
    就是这么写的，前端据此决定画不画那枚徽章。
    """
    if a is None:
        return {
            "direction": None,
            "directionLabel": None,
            "directionPending": None,
            "hasDir": None,
            "dir": None,
        }
    v = a["value"]
    if v == "pending":
        return {
            "direction": None,
            "directionLabel": "待确认",
            "directionPending": True,
            "hasDir": True,
            "dir": _dir_style(None, True),
        }
    if v in _DIRECTIONS:
        return {
            "direction": v,
            "directionLabel": _DIRECTIONS[v][0],
            "directionPending": False,
            "hasDir": True,
            "dir": _dir_style(v, False),
        }
    # `false`（非操作类）落在这里。枚举外的字符串也落在这里而不是抛 —— 与 post_type
    # 不同：方向本来就有「没有方向」这个合法结局，多一个没见过的值退成它不会造出一个
    # 我们没作的判断。
    return {
        "direction": None,
        "directionLabel": "",
        "directionPending": False,
        "hasDir": False,
        "dir": None,
    }


# 句末标点（中英文两套）。`\n` 也算一处断点：`annotate.py` 用换行把标题和正文接起来，
# 标题本身常常不带标点，不断开的话整篇会变成一「句」。
_SENTENCE_END = re.compile(r"[。！？!?；;\n]+|\.(?=\s|$)")


def _sentences(text):
    """把原文切成句子，同时给出每句在原文里的 `[start, end)`。

    偏移量是这个函数存在的理由：`annotation_evidence.start_offset` 是字符位置，而契约
    里的 `evidenceIdx` 是**第几句**。没有区间就只能靠子串查找把引文对回去，而同一句话
    在一篇帖子里出现两次时，子串查找会挑错一处（还不报错）。

    切完丢掉纯空白段，所以 `fullText[i]` 拿到的是有内容的一句；空正文给空列表。
    """
    spans, pos = [], 0
    for m in _SENTENCE_END.finditer(text):
        spans.append((pos, m.end()))
        pos = m.end()
    if pos < len(text):
        spans.append((pos, len(text)))
    out = []
    for start, end in spans:
        s = text[start:end].strip()
        if s:
            out.append((start, end, s))
    return out


def _sentence_index(spans, offset):
    """字符偏移落在第几句。落不进任何一句 ⇒ `-1`（设计源里「没有判定依据」的值）。

    先按包含判（`start <= offset < end`）；引文起点正好压在上一句的句末标点或空白上
    时，退成「最后一个起点不超过它的句子」。两条都不成立（偏移越界、是 None、或者
    原文已经被 ETL 重跑改过）才给 -1 —— 这时候页面显示「AI 生成」而不是高亮一句
    可能根本不相干的话。
    """
    if offset is None:
        return -1
    for i, (start, end, _t) in enumerate(spans):
        if start <= offset < end:
            return i
    best = -1
    for i, (start, _end, _t) in enumerate(spans):
        if start <= offset:
            best = i
    return best


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
