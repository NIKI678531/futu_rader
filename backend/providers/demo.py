"""演示 provider：读 fixtures/demo/ 下由设计源导出的 JSON（生成器见 fixtures/generate.mjs）。

主体数值逐字来自 design/radar-data.js，本模块只做「按参数键查表」。官号域是例外：
旧设计源把讨论区挂载标的混进 `mentioned`，因此 fixture 另行固化 v3 显式归属；本模块
只从该归属投影兼容字段与 ETF 汇总，绝不把旧 `mentioned` / `etf_mentions` 当证据。
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
import re
from copy import deepcopy
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from core import topics as core_topics
from core.calendar import build, build_custom
from core.futu_links import canonical_futu_feed_url
from core.range_spec import is_custom_key, parse_custom_key, validate_selectable

# 哨兵本体搬去了 providers/sentinel.py —— sql provider 也要用它，而 sql 去 import demo
# 是说不通的（两个平行实现，谁都不该依赖另一个）。这里 re-export 只为了让
# `from providers.demo import MISSING` 这种旧写法不至于突然报错；新代码从 sentinel 取。
from .sentinel import MISSING  # noqa: F401  （re-export，见上）

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"
FIXTURE_DIR = FIXTURE_ROOT / "demo"

_OFFICIAL_ATTRIBUTION_STATUSES = frozenset({"explicit", "inferred", "unattributed"})
_OFFICIAL_CASHTAG_CODE = re.compile(
    r"\$(?:[^$]*?[（(]\s*)?(0*\d{4,5})\.HK\s*[）)]?\$",
    re.IGNORECASE,
)
_SHORT_PREFIX = re.compile(r"^南方[東东]英")
_SHORT_SUFFIX = re.compile(r"(指數|指数)?ETF$")

# ``topics.json`` predates the product-market-outlook contract and mixes true
# market/index/industry discussion with product mechanics and peer-selection
# cards.  The fixture has no source-kind discriminator, so keep an explicit,
# closed allow-list here: an unknown future legacy title is safer omitted than
# presented as a market-direction conclusion.
_DEMO_MARKET_DIRECTION_TOPIC_TITLES = frozenset({
    "指数与市场方向判断",
    "恒生科技指数反弹持续性",
    "港股通资金流向",
    "美元利率下行路径",
    "长久期国债的久期风险",
    "A股成交量能否放大",
    "恒指关键点位争夺",
    "政策预期与板块轮动",
    "英伟达财报前的仓位安排",
    "纳指财报窗口的波动",
    "海力士 HBM 产能进展",
    "日经与越南的区域轮动",
    "日元汇率对回报的影响",
    "金价关键位争夺",
    "黄金避险与美元流向",
    "美联储议息预期",
    "美股估值分歧",
    "虚拟资产监管进展",
})

def _canonicalize_fixture_links(value):
    """Upgrade frozen fixture post URLs without rewriting multi-megabyte JSON files."""
    if isinstance(value, list):
        return [_canonicalize_fixture_links(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonicalize_fixture_links(item) for key, item in value.items()}
    return canonical_futu_feed_url(value)


def _official_camp(products):
    camps = {
        "own" if product.get("ownership") == "own" else "competitor"
        for product in products
    }
    return "both" if len(camps) > 1 else next(iter(camps)) if camps else "none"


def _fixture_mentioned_etfs(products):
    """Project legacy demo pool mentions onto the new display-only contract."""
    out = []
    for product in products or []:
        if not isinstance(product, dict) or not product.get("code"):
            continue
        code = str(product["code"])
        ticker = product.get("ticker")
        if not ticker:
            ticker = f"{code.zfill(5)}.HK" if code.isdigit() else code
        out.append({
            "code": code,
            "ticker": ticker,
            "name": product.get("name"),
            "issuer": product.get("issuer"),
            "ownership": product.get("ownership"),
            "inPool": product.get("inPool", True),
        })
    return out


def _attach_fixture_mentioned_etfs(value):
    """Copy nested KOL fixture data and add mentionedEtfs to every post shape."""
    value = deepcopy(value)

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if "mentioned" in node and "camp" in node and "mentionedEtfs" not in node:
                node["mentionedEtfs"] = _fixture_mentioned_etfs(node.get("mentioned"))
            for child in node.values():
                walk(child)

    walk(value)
    return value


def _normalise_hk_code(raw):
    code = str(raw).lstrip("0") or "0"
    return code.zfill(4) if len(code) < 4 else code


def _explicit_cashtag_counts(post):
    counts = defaultdict(int)
    for line in post.get("fullText") or []:
        for raw_code in _OFFICIAL_CASHTAG_CODE.findall(str(line)):
            counts[_normalise_hk_code(raw_code)] += 1
    return counts


def _short_name(name, n=10):
    value = _SHORT_SUFFIX.sub("", _SHORT_PREFIX.sub("", str(name or "")))
    if not value:
        value = str(name or "")
    return value if len(value) <= n else value[:n] + "…"


def _demo_market_direction_topics(topics):
    """Copy only frozen topics that are valid P6 market-direction context."""
    if topics is None:
        return None
    return [
        dict(row) for row in topics
        if isinstance(row, dict)
        and row.get("title") in _DEMO_MARKET_DIRECTION_TOPIC_TITLES
    ]


def _demo_market_summary(topics, limit=180):
    """Project the frozen topic cards into one detailed, source-bounded summary.

    The demo fixture predates the single-row market-outlook card and stores up
    to three separately grounded topic labels.  Combining their existing title
    and summary text gives the card the same 2–4 sentence density as current AI
    output without inventing a company, event, or causal link.
    """
    rows = [row for row in (topics or []) if isinstance(row, dict) and row.get("title")]
    rows.sort(
        key=lambda row: (int(row.get("mentions") or 0), str(row.get("id") or "")),
        reverse=True,
    )
    if not rows:
        return None, []

    def clean(value):
        return re.sub(r"\s+", " ", str(value or "")).strip().rstrip("。！？!?；;")

    if len(rows) == 1:
        title = clean(rows[0]["title"])
        detail = clean(rows[0].get("summary"))
        if not detail:
            return None, []
        sentences = [
            (f"评论聚焦「{title}」", rows[0]),
            (f"具体观点为：{detail}", rows[0]),
        ]
    else:
        leads = ("评论首先聚焦", "同时关注", "另一条讨论线索是", "此外还涉及")
        sentences = []
        for index, row in enumerate(rows[:4]):
            title = clean(row["title"])
            detail = clean(row.get("summary"))
            sentence = f"{leads[index]}「{title}」"
            if detail:
                sentence += f"：{detail}"
            sentences.append((sentence, row))

    selected = []
    for sentence, row in sentences:
        candidate = "".join(f"{part}。" for part, _row in [*selected, (sentence, row)])
        if len(candidate) <= limit:
            selected.append((sentence, row))
    # Keep the 2–4 sentence contract without truncating a source sentence.  If
    # detailed prose cannot fit, two source titles are still concrete and fully
    # attributable; one title alone is not enough to manufacture a second fact.
    if len(selected) < 2 and len(rows) >= 2:
        selected = [
            (f"评论聚焦「{clean(rows[0]['title'])}」", rows[0]),
            (f"同时涉及「{clean(rows[1]['title'])}」", rows[1]),
        ]
        if len("".join(f"{sentence}。" for sentence, _row in selected)) > limit:
            return None, []
    elif len(selected) < 2:
        return None, []

    evidence_ids = list(dict.fromkeys(
        evidence_id
        for _sentence, row in selected
        for evidence_id in (row.get("evidenceIds") or [])
    ))
    return "".join(f"{sentence}。" for sentence, _row in selected), evidence_ids


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
                    found.append(_canonicalize_fixture_links(
                        json.loads(path.read_text(encoding="utf-8"))
                    ))
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

    def _range_lookup(self, table, *parts):
        """Return unavailable for the known d0 range when no demo fact exists.

        The frozen design fixture predates d0.  Reusing d1/d7 numbers would
        fabricate current-day facts, while MISSING would incorrectly turn a
        valid range into a 404.  ``None`` preserves the public unavailable
        contract until a dedicated d0 fixture exists.
        """
        value = self._lookup(table, *parts)
        return None if value is MISSING and parts and (
            parts[-1] == "d0" or is_custom_key(parts[-1])
        ) else value

    def _product_range_lookup(self, table, code, range_key):
        if not any(str(item.get("code")) == str(code) for item in self.master().get("products", [])):
            return MISSING
        return self._range_lookup(table, code, range_key)

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
        custom = parse_custom_key(key)
        if custom is not None:
            latest = self._lookup("ranges", "d30")["to"]
            first, last = custom
            validate_selectable(first, last, datetime.fromisoformat(latest).date())
            return build_custom(first, last)
        if key == "d0":
            updated = self.master().get("updatedAt")
            if not updated:
                return None
            try:
                data_through = datetime.strptime(
                    str(updated).removesuffix(" HKT"), "%Y-%m-%d %H:%M"
                )
            except ValueError:
                return None
            return build("d0", data_through.date(), data_through=data_through)
        return self._lookup("ranges", key)

    @staticmethod
    def _with_comment_funnel(observation):
        """把旧 demo 的完整观测映射到父帖筛选后的漏斗契约。"""
        comments = observation["comments"]
        # The bundled demo is a closed fixture.  A zero-comment product is a
        # completed empty result, not missing coverage.
        coverage = 1.0
        return {
            **observation,
            "commentFunnel": {
                "rawPlatformCount": comments,
                "platformCount": comments,
                "qualifyingFeedCount": observation["mentions"],
                "filterExcludedPlatformCount": 0,
                "parsedCount": comments,
                "ruleEligibleCount": comments,
                "ruleExcludedCount": 0,
                "aiCompletedCount": comments,
                "relevantCount": comments,
                "needsContextCount": 0,
                "pendingCount": 0,
                "sourceCoverage": coverage,
                "analysisCoverage": coverage,
            },
        }

    def pool(self, range_key):
        data = self._range_lookup("pool", range_key)
        if data is None:
            return None
        if data is MISSING:
            return MISSING
        own = data["own"]
        own_count = own["count"]
        return {
            **data,
            "own": {
                **own,
                "negLowerBound": own["neg"],
                "posLowerBound": own["pos"],
                "managementCoverage": {
                    "negativeProducts": own_count,
                    "positiveProducts": own_count,
                    "totalProducts": own_count,
                    "complete": True,
                },
            },
            "list": [self._with_comment_funnel(item) for item in data["list"]],
        }

    def ranks(self, range_key):
        return self._range_lookup("ranks", range_key)

    def benchmark(self, code, range_key):
        data = self._product_range_lookup("benchmark", code, range_key)
        if data is None:
            return None
        if data is MISSING:
            return MISSING
        return {**data, "base": self._with_comment_funnel(data["base"])}

    def hot_summaries(self, range_key):
        """整池一份 `{code: 热议总结}`，按区间查表。

        按产品切端点，板块总览那张 120 行的榜单就是 120 次串行往返 ——
        每行调一次 `hotSummaryFor`，而同步 Suspense 一次只能解决一个。
        """
        return self._range_lookup("hot_summaries", range_key)

    def summary_for(self, code, range_key):
        return self._product_range_lookup("summaries", code, range_key)

    def market_outlook(self, code, range_key):
        """只返回当前产品对应的产品态度与市场风向摘要。

        直接投影已有 ``topics`` fixture，不新增一份会与话题数据漂移的
        market-outlook fixture。
        """
        products = self.master().get("products", [])
        by_code = {str(product["code"]): product for product in products}
        if code not in by_code:
            return MISSING

        pool = self.pool(range_key)
        attitudes = {} if pool is None or pool is MISSING else {
            str(product["code"]): product.get("attitude")
            for product in pool.get("list", [])
        }
        value = self.topics_for(code, range_key)
        topics = None if value is MISSING else _demo_market_direction_topics(value)
        attitude_counts = attitudes.get(code)
        market_count = core_topics.market_discussion_count(topics)
        opinion_count = None if attitude_counts is None else sum(
            int(value or 0) for value in attitude_counts.values()
        )
        if not any((count or 0) > 0 for count in (market_count, opinion_count)):
            if any(count is None for count in (market_count, opinion_count)):
                return None
            return {"scope": "product", "list": []}
        projected_topics = topics
        detailed_summary, summary_evidence_ids = _demo_market_summary(projected_topics)
        if projected_topics:
            primary = max(
                projected_topics,
                key=lambda topic: (int(topic.get("mentions") or 0), str(topic.get("id") or "")),
            )
            if detailed_summary:
                primary["summary"] = detailed_summary
                primary["evidenceIds"] = summary_evidence_ids
            else:
                # Never fall back to the legacy one-sentence topic prose when
                # the new 2–4 sentence market-summary contract cannot be met.
                primary["summary"] = None
                primary["labelStatus"] = "unavailable"
        return {
            "scope": "product",
            "list": [core_topics.market_outlook_row(
                by_code[code], projected_topics, attitude_counts,
            )],
        }

    def themes_for(self, code, range_key):
        """一次给两个极性 `{positive, negative}`。调用点从来都是正负各取一次。"""
        value = self._product_range_lookup("themes", code, range_key)
        if value is not None and value is not MISSING:
            for rows in value.values():
                for row in rows:
                    row.setdefault("evidenceIds", [])
                    row.setdefault("keyPoints", [])
        return value

    def issuer_opinions(self, code, range_key):
        product = next((item for item in self.master().get("products", [])
                        if item.get("code") == code), None)
        if product is None:
            return MISSING
        if product.get("ownership") != "own":
            return {"status": "na", "issuer": product.get("issuer"), "counts": None, "list": []}
        return {"status": "unavailable", "issuer": "南方东英", "counts": None, "list": []}

    def neg_cats_for(self, code, range_key):
        return self._product_range_lookup("neg_cats", code, range_key)

    def competitors_for(self, code, range_key):
        return self._product_range_lookup("competitors", code, range_key)

    def compliance_for(self, code, range_key):
        return self._product_range_lookup("compliance", code, range_key)

    def topics_for(self, code, range_key):
        return self._product_range_lookup("topics", code, range_key)

    def kol_mentions_for(self, code, range_key):
        data = self._product_range_lookup("kol_mentions", code, range_key)
        if data is None or data is MISSING:
            return data

        # ``kol_mentions`` is the frozen comment-opinion fixture, while
        # ``kol_impact`` is the frozen KOL-post fixture used by /kol.  Merge the
        # two here so the product page and KOL page really share the same posts
        # without rewriting either historical fixture.
        by_kol = {
            source["kolName"]: {
                **source,
                "evidence": list(source.get("evidence") or []),
            }
            for source in data.get("list", [])
        }
        impact = self.kol_impact(range_key)
        impact_posts = [] if impact is None or impact is MISSING else impact.get("posts", [])
        for post in impact_posts:
            if not any(str(item.get("code")) == str(code) for item in post.get("mentioned", [])):
                continue
            name = post.get("kol")
            if not name:
                continue
            row = by_kol.setdefault(name, {
                "productCode": code,
                "kolAccountId": f"kol-{name}",
                "kolName": name,
                "kolTags": post.get("tags"),
                "kolType": "partner",
                "kolTypeLabel": "合作 KOL",
                "mentionCommentCount": 0,
                "lastMentionedAt": None,
                "dominantAttitude": None,
                "dominantLabel": None,
                "representativeExcerpt": None,
                "evidenceCount": 0,
                "evidence": [],
            })

            time_text = str(post.get("time") or "").strip()
            day = str(post.get("day") or "").strip()
            published_at = (
                f"{day} {time_text.rsplit(' ', 1)[-1]}" if day and time_text
                else time_text or day or None
            )
            original = "\n".join(
                str(part) for part in (post.get("fullText") or []) if part is not None
            ) or str(post.get("typeEvidence") or "")
            source_url = post.get("url")
            post_identity = post.get("id") or f"{name}|{published_at}|{source_url or ''}"
            evidence_id = f"{code}-kp-{post_identity}"
            if any(
                item.get("sourceKind") == "帖子" and item.get("id") == evidence_id
                for item in row["evidence"]
            ):
                continue
            row["evidence"].append({
                "id": evidence_id,
                "productCodes": [str(item["code"]) for item in post.get("mentioned", [])],
                "publishedAt": published_at,
                "authorName": name,
                "authorType": "合作 KOL",
                "isKnownKol": True,
                "kolType": "partner",
                "excerpt": original,
                "attitude": None,
                "attitudeLabel": None,
                "comments": post.get("comments"),
                "interactions": post.get("engagement"),
                "sourceKind": "帖子",
                "sourceUrl": source_url,
                "_summary": post.get("summary") if isinstance(post.get("summary"), str)
                and post["summary"].strip() else None,
            })

        rows = []
        for source in by_kol.values():
            evidence = source["evidence"]
            evidence.sort(
                key=lambda item: (item.get("publishedAt") or "", str(item.get("id") or "")),
                reverse=True,
            )
            posts = [item for item in evidence if item.get("sourceKind") == "帖子"]
            latest_post = posts[0] if posts else None
            representative = latest_post or (evidence[0] if evidence else None)
            rows.append({
                **source,
                "mentionCommentCount": len(evidence),
                "lastMentionedAt": (evidence[0] if evidence else {}).get("publishedAt"),
                "latestPostAt": (latest_post or {}).get("publishedAt"),
                "representativeSummary": (
                    (latest_post or {}).get("_summary")
                    if latest_post else source.get("representativeSummary")
                ),
                "representativeExcerpt": (
                    (representative or {}).get("excerpt")
                    or source.get("representativeExcerpt")
                ),
                "evidenceCount": len(evidence),
                "evidence": [
                    {key: value for key, value in item.items() if key != "_summary"}
                    for item in evidence
                ],
            })

        # Stable sorts keep KOL name ascending when two timestamps tie.
        rows.sort(key=lambda row: row.get("kolName") or "")
        with_posts = [row for row in rows if row.get("latestPostAt") is not None]
        comments_only = [row for row in rows if row.get("latestPostAt") is None]
        with_posts.sort(key=lambda row: row["latestPostAt"], reverse=True)
        comments_only.sort(key=lambda row: row.get("lastMentionedAt") or "", reverse=True)
        return {
            **data,
            "status": "ok" if rows else data.get("status", "empty"),
            "list": [*with_posts, *comments_only],
        }

    def evidence_for(self, code, ctx_key, polarity, n):
        """证据侧栏。参数键 `<code>|<ctxKey>|<polarity>`，其中 ctxKey 自带区间前缀。

        fixture 按区间切了 5 个文件（整份约 23 MB）：侧栏只在点开时取一次，没必要为它
        把五个区间全读进内存。表名里的区间来自 ctxKey，core 那边已经校验过它是预设之一。

        存的是**生成顺序**的 12 条 ＋ n=1..12 各自的次序（fixtures/generate.mjs 有详述）：
        设计源排序用的比较器在时间相同时返回 -1 而不是 0，同分钟两条谁在前取决于 V8 的
        实现，Python 这边重排一次就会和页面对不上。所以这里只按下标取，不排序。
        """
        if not any(str(item.get("code")) == str(code) for item in self.master().get("products", [])):
            return MISSING
        if ctx_key.split("|")[0] == "d0":
            return None
        pack = self._lookup("evidence_" + ctx_key.split("|")[0], code, ctx_key, polarity)
        if pack is MISSING:
            return MISSING
        return [pack["items"][i] for i in pack["orders"][n - 1]]

    def candles_for(self, code, range_key):
        return self._product_range_lookup("candles", code, range_key)

    def heat_series_for(self, code, range_key):
        return self._product_range_lookup("heat_series", code, range_key)

    def stages_for(self, code, range_key):
        return self._product_range_lookup("stages", code, range_key)

    def daily_for(self, code):
        """日度评论量／活跃账号／价格序列。四个契约函数里唯一**不吃区间**的一个 ——
        它固定给 SERIES 那 42 天，所以参数键只有产品代码。`'ALL'` 是它自己的伪代码。"""
        return self._lookup("daily", code)

    def kol_impact(self, range_key):
        value = self._range_lookup("kol_impact", range_key)
        if value is None or value is MISSING:
            return value
        return _attach_fixture_mentioned_etfs(value)

    def kol_opinions(self, kol, range_key):
        if not any(item.get("name") == kol for item in self.master().get("kols", [])):
            return MISSING
        return self._range_lookup("kol_opinions", kol, range_key)

    def official_posts(self, range_key):
        posts = self._range_lookup("official_posts", range_key)
        if posts is None:
            return None
        if posts is MISSING:
            return MISSING
        try:
            by_code = {item["code"]: item for item in self.master().get("products", [])}
        except FileNotFoundError:
            # Tiny contract fixtures used by unit tests need not copy the
            # entire product master.  Their attributed product object still
            # carries the fields required below.
            by_code = {}
        out = []
        for post in posts:
            status = post.get("attributionStatus")
            products = post.get("attributedProducts")
            # Fail closed on pre-v3 fixtures.  ``mentioned``/``camp`` used to
            # include the forum anchor and are therefore not attribution
            # evidence.  They remain response aliases only, populated below
            # from the explicit v3 fields.
            if (
                status not in _OFFICIAL_ATTRIBUTION_STATUSES
                or not isinstance(products, list)
                or (status == "unattributed" and products)
            ):
                status = "unattributed"
                attributed = []
            else:
                attributed = [item for item in products if isinstance(item, dict) and item.get("code")]
                if not attributed:
                    status = "unattributed"
            attributed_camp = _official_camp(attributed)
            primary = attributed[0] if attributed else {}
            primary_details = by_code.get(primary.get("code"), primary)
            out.append({
                **post,
                # ``code`` in old fixtures is still useful as source metadata,
                # but it is never promoted into the attribution set.
                "sourceAnchorCode": post.get("sourceAnchorCode", post.get("code")),
                "attributedProducts": attributed,
                "attributionStatus": status,
                "attributedCamp": attributed_camp,
                # Compatibility fields consumed by older cards must share the
                # same source of truth; otherwise they reintroduce the anchor.
                "code": primary.get("code"),
                "name": primary_details.get("name"),
                "sector": primary_details.get("sector"),
                "ownership": primary_details.get("ownership"),
                "issuer": primary_details.get("issuer"),
                "mentioned": attributed,
                "mentionedEtfs": _fixture_mentioned_etfs(
                    post.get("mentionedEtfs", attributed)
                ),
                "camp": attributed_camp,
                "campPrimary": _official_camp(attributed[:1]),
            })
        return out

    def etf_mentions_for(self, account, range_key):
        officials = self.master().get("officials", [])
        official = next(
            (item for item in officials if account in (item.get("short"), item.get("full"))),
            None,
        )
        if official is None:
            return MISSING
        posts = self.official_posts(range_key)
        if posts is None:
            return None
        if posts is MISSING:
            return MISSING

        rows = {}
        post_ids = set()
        account_names = {official.get("short"), official.get("full")}
        for post in posts:
            if post.get("account") not in account_names and post.get("accountFull") not in account_names:
                continue
            occurrence_counts = _explicit_cashtag_counts(post)
            seen = set()
            for product in post["attributedProducts"]:
                code = str(product["code"])
                if code in seen:
                    continue
                seen.add(code)
                row = rows.setdefault(
                    code,
                    {
                        "code": code,
                        "name": product.get("name"),
                        "short": _short_name(product.get("name")),
                        "issuer": product.get("issuer"),
                        "ownership": product.get("ownership"),
                        "count": 0,
                        "posts": 0,
                    },
                )
                row["count"] += max(1, occurrence_counts.get(code, 0))
                row["posts"] += 1
                post_ids.add(post.get("id"))

        items = sorted(
            rows.values(),
            key=lambda item: (
                0 if item["ownership"] == "own" else 1,
                -item["count"],
                item["code"],
            ),
        )
        return {
            "list": items,
            "own": [item for item in items if item["ownership"] == "own"],
            "peer": [item for item in items if item["ownership"] != "own"],
            "etfCount": len(items),
            "total": sum(item["count"] for item in items),
            "postCount": len(post_ids),
        }
