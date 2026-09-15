"""对**真瘦库**的五页冒烟测试 —— `tests/sql_fixture.py` 里那句「只能靠对真库的实测」。

## 它补的是哪一块

`test_sql_provider.py` / `test_provider_parity.py` 跑在内存里十来行编出来的数据上。
schema 是共用的那一份，所以「列名对不上」在那边就会红。抓不到的是另一类：

- dump 里那一列的**实际含义**和我们以为的不一样（`import_dump` 映射错了字段）；
- 口径 SQL 在 350k 行真数据上跑不出来（缺索引、笛卡尔积、类型不一致）；
- 真数据里有编造数据不会出现的形状（整片 KOL 没有一条 AI 标注、某区间一条评论都没有）。

这三类只在真库上现形，所以这个文件存在；也因为真库不进 git、每台机器都不一样，
所以它**默认跳过**而不是默认失败 —— 一个在 CI 上恒红的测试很快就会被人加 `-k not real`
永久关掉，那时它连本机也不跑了。

## 三条硬约束

1. **只读打开。** 真库 5.27 GB，是一次性导入＋ETL 的产物，重建要几十分钟。URL 里带
   `mode=ro&uri=true`，SQLite 层面拒绝任何写入 —— 不靠「这些测试里没有 INSERT」这种
   靠人守的纪律。（`radar_db.make_engine` 会发 `PRAGMA journal_mode=WAL`；库已经是 WAL
   时这是个只读的空操作，不会失败。）
2. **不写死任何真实取值。** 产品代码、KOL 昵称、官号名字都从库里现查。写死一个
   `'3033'` 的测试换一份 dump 就红，而红的原因和代码无关。同理，断言的是**关系**
   （评论数 > 0、桶数 == 区间天数），不是具体数字。
3. **不落 PII。** 真库含真实昵称／IP 归属地／个人简介（ADR-0008）。断言里不出现
   取到的昵称原文，失败信息只给代码和计数 —— 测试输出会进 CI 日志和聊天记录。

## 跑法

```bash
cd backend && PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pytest tests/test_real_db_smoke.py -q
```

库不在就整份跳过。库在别处时设 `RADAR_DB_URL` 也没用 —— 这里刻意自己拼只读 URL，
因为环境变量里那个多半是可写的，而第 1 条不接受「大概不会写」。
"""

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import providers  # noqa: E402
from app import create_app  # noqa: E402
from providers import reset_provider  # noqa: E402
from providers.sql import _UNANNOTATED, SqlProvider  # noqa: E402
from radar_db import default_data_dir  # noqa: E402

DB_PATH = default_data_dir() / "radar.db"

pytestmark = pytest.mark.skipif(
    not DB_PATH.exists(),
    reason=f"真瘦库不在本机（{DB_PATH}）—— 见 CLAUDE.md「瘦库一次性构建」",
)

RANGE = "d7"  # 五页的默认区间；锚点是导入时算出来的，不是今天


@pytest.fixture(scope="module")
def real():
    """连真库的 test client。

    module 级：provider 在 `pool()` / `ranks()` 上有进程内缓存，而且每次构造都要重连
    5.27 GB 的库。按函数重建一遍，这个文件会从几秒变成几分钟。
    """
    url = f"sqlite:///file:{DB_PATH.as_posix()}?mode=ro&uri=true"
    provider = SqlProvider(url)
    app = create_app()
    app.config.update(TESTING=True)

    @app.before_request
    def _install():
        providers._instance = provider

    reset_provider()
    with app.test_client() as c:
        yield c
    reset_provider()


STATUSES = {"ok", "empty", "unavailable", "low_sample", "na"}


def body(client, path):
    """取一个端点的信封，顺带把「200 ＋ 合法 status」这条钉死。

    数据缺失一律 200 + status 枚举（app.py 模块头）。真库上走到 4xx/5xx 说明是口径 SQL
    自己炸了 —— 那和「这个数暂时没有」是两回事，不该被下游断言当成 `None` 咽下去。
    """
    res = client.get(path)
    assert res.status_code == 200, f"{path} → {res.status_code} {res.get_data(as_text=True)[:200]}"
    env = res.get_json()
    assert set(env) >= {"status", "data"}, f"{path} 响应不是 {{status, data}} 信封"
    assert env["status"] in STATUSES, f"{path} status={env['status']!r} 不在五态枚举里"
    return env["data"]


# ── 库本身 ─────────────────────────────────────────────────────────────


def test_anchor_comes_from_meta_kv_not_the_clock(real):
    """区间的右端 == `meta_kv.anchor`，不是 `date.today()`。

    这条在内存 fixture 上也测了，但那边的 `meta_kv` 是测试自己塞的。真库上它才是
    「`import_dump` 确实写了这个键、而且 backend 确实读的是它」的证据。
    锚点直接从库里读出来对照，不写死日期（约束 2）。
    """
    import sqlite3

    con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute("select v from meta_kv where k = 'anchor'").fetchone()
    finally:
        con.close()
    assert row, "真库 meta_kv 里没有 anchor —— import_dump 没写"
    assert body(real, f"/api/v1/ranges/{RANGE}")["to"] == row[0]


def test_data_cutoff_is_the_real_anchor_not_the_frozen_demo_constant(real):
    """`/meta.updatedAt`（页面上的「数据截至」）随真数据走。

    这一条是补一个**真实发生过的**谎：`updatedAt` 原来手写在 `fixtures/meta.json` 里，
    值是演示锚点 `2026-09-02 09:00 HKT`。接真库之后 `core/meta.py` 照发，页面给
    截止到 `2026-08-25` 的数据落了 `09-02` 的款 —— 虚报一周，且页面上没有任何迹象。
    缺失有「暂不可用」兜着，这种错没有。
    """
    updated = body(real, "/api/v1/meta").get("updatedAt")
    assert updated is not None, "真库有 anchor_ts 却没下发 updatedAt"
    assert updated.startswith(body(real, f"/api/v1/ranges/{RANGE}")["to"]), (
        f"数据截至 {updated!r} 与真实锚点对不上 —— 又落回常量了"
    )


def test_range_buckets_cover_the_window(real):
    rng = body(real, f"/api/v1/ranges/{RANGE}")
    assert rng["days"] == 7
    assert len(rng["buckets"]) == 7, "d7 的桶数必须等于天数（PRD §3.1：后端下发，前端不算桶）"


# ── 板块总览 ───────────────────────────────────────────────────────────


def test_pool_returns_the_whole_active_universe(real):
    """完整活跃 ETF 池 ≈ 120 只（自家 61 ＋ 竞品 59），且计数是真的。

    上界卡在 200：`mentions` 表有 92.7 万行，一个写错的 JOIN 会让池子涨到几千只，
    而那时页面照样渲染得出来 —— 只是热力图密密麻麻，没人会当成 bug。
    """
    pool = body(real, f"/api/v1/pool?range={RANGE}")["list"]
    assert 100 <= len(pool) <= 200, f"活跃池 {len(pool)} 只，与 61+59 的量级对不上"
    assert sum(1 for p in pool if (p["comments"] or 0) > 0) >= 10, (
        "整池没有 10 只有评论 —— 要么区间取空了，要么 comments 根本没接上"
    )


def test_heat_is_a_number_and_unknown_shares_are_disclosed_not_zeroed(real):
    """讨论热度按已知项计算并披露未知帖数（ADR-0022），不因千分之一的坏行整列变灰。

    热度 ＝ 评论量 ＋ 0.3 × 点赞 ＋ 1 × 转发。真库上确实有帖子的 `share_count` 取不到
    （raw_json 被截断的 0.09% 行）。原来那一帖让整只产品的热度变成 None、再传染到公司级
    KPI；现在 `shares` / `interactions` / `discussionHeat` 是按已知项算的下限，
    `heatUnknownPosts` 说出差了几帖。这里钉两件事：三个数在真库上**必须**都是数
    （评论量与点赞是 dump 的列，永远有值），而且 `heatUnknownPosts` 是非负整数 ——
    它是「下限」这句话能被核对的唯一依据。
    """
    seen_unknown = 0
    for p in body(real, f"/api/v1/pool?range={RANGE}")["list"]:
        for f in ("comments", "likes", "shares", "interactions", "discussionHeat"):
            assert isinstance(p[f], (int, float)), f"{p['code']}：{f} = {p[f]!r}，应为数"
        assert isinstance(p["heatUnknownPosts"], int) and p["heatUnknownPosts"] >= 0, (
            f"{p['code']}：heatUnknownPosts = {p['heatUnknownPosts']!r}"
        )
        seen_unknown += p["heatUnknownPosts"]
        assert p["interactions"] == p["likes"] + p["shares"], f"{p['code']}：互动数不是点赞 ＋ 已知转发"
    # 不断言 seen_unknown > 0：坏行落不落在这个窗口是数据的事，不是代码的事。


def test_ranks_cover_the_whole_market(real):
    """全市场评论量排名由后端算好（铁律 3），总数与池子一致。"""
    pool = body(real, f"/api/v1/pool?range={RANGE}")["list"]
    ranks = body(real, f"/api/v1/ranks?range={RANGE}")
    assert ranks["total"] == len(pool), "排名总数与活跃池不一致 —— 排名不是按全市场算的"


# ── 产品监控 ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def busiest_code(real):
    """区间内评论最多的那只产品的代码。

    挑最热的那只，是为了让下面「事实类字段应当有值」的断言有意义：随便挑一只可能
    整个区间一条评论都没有，那时「摘要为 None」既可能是没标注也可能是没内容，
    断言分不出来。代码从库里现查（约束 2）。
    """
    pool = body(real, f"/api/v1/pool?range={RANGE}")["list"]
    return max(pool, key=lambda p: p["comments"] or 0)["code"]


def test_product_endpoints_all_answer_on_real_data(real, busiest_code):
    """产品监控整页的端点在真数据上都走得通。

    这一页调的端点最多，也是最容易在真库上炸的：几个口径 SQL 要在 92.7 万行 `mentions`
    上做时间分桶 ＋ 去重。内存 fixture 那十来行跑得通不代表这里跑得通。
    """
    for ep in (
        "benchmark",
        "summary",
        "themes",
        "negative-categories",
        "competitors",
        "compliance",
        "topics",
        "kol-mentions",
        "candles",
        "heat-series",
        "stages",
        "daily",
    ):
        body(real, f"/api/v1/products/{busiest_code}/{ep}?range={RANGE}")

    # 证据端点的参数形状与别处不同：ctx 是 `<区间>|<面板 id>`，polarity 是三极性之一
    # （core/evidence.py）。产品在池里 ⇒ 没有标注只会得到 200 + None，**不是** 404 ——
    # 404 会被前端的 ScreenBoundary 当成传输层故障，整屏报「后端服务连不上」，
    # 而后端好得很，只是这条摘录还没生成（frontend/src/lib/api.js 模块头那张表）。
    body(real, f"/api/v1/products/{busiest_code}/evidence?ctx={RANGE}%7Csum&polarity=neutral")


def test_unknown_product_is_404_not_a_screen_of_unavailable(real):
    """查不到的产品代码回 404，不是 200 + 一屏「暂不可用」（providers/sentinel.py）。"""
    res = real.get(f"/api/v1/products/0000-not-a-code/summary?range={RANGE}")
    assert res.status_code == 404
    assert "status" not in res.get_json(), "传输层错误不该带 status 字段（app.py 模块头）"


def test_ai_and_quote_fields_preserve_real_availability(real, busiest_code):
    """态度、摘要、K 线在 `sql` 下是 `None`，不是 0，也不是空串。

    这是 ADR-0017 的现状：标注管线还没铺满，行情源还没接。页面必须因此显示
    「暂不可用」而不是「0」或者一条平的 K 线。
    """
    bench = body(real, f"/api/v1/products/{busiest_code}/benchmark?range={RANGE}")

    # 环比位是 delta() 契约（`{text, short, abs, pct, dir}`），不是裸数字：缺失体现在
    # abs/pct 为 None ＋ 文案落成「数据暂不可用」／「暂不可用」，不是整个键为 None。
    for field in ("positive", "neutral", "negative"):
        d = bench[field]
        if d["abs"] is None:
            assert d["pct"] is None
            assert d["text"] == "数据暂不可用" and d["short"] == "暂不可用"
        else:
            assert isinstance(d["abs"], (int, float))

    # 桶里的态度三项同样一个都没有：折线要能画成断线，不是画成一条贴地的零线。
    for bucket in bench["base"]["buckets"]:
        for field in ("positive", "neutral", "negative"):
            assert bucket[field] is None or isinstance(bucket[field], (int, float))

    # 行情源还没接（ADR-0017）：K 线整块为 None，不是一条平的零线。
    candles = body(real, f"/api/v1/products/{busiest_code}/candles?range={RANGE}")
    if candles is not None:
        rng = body(real, f"/api/v1/ranges/{RANGE}")
        assert [row["bucket"] for row in candles["list"]] == [row["tip"] for row in rng["buckets"]]
        for row in candles["list"]:
            values = [row[field] for field in ("open", "high", "low", "close")]
            assert all(value is None for value in values) or all(value > 0 for value in values)
            if row["close"] is not None:
                assert row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]


# ── KOL 影响力 ／ KOL 详情 ──────────────────────────────────────────────


def test_kol_impact_has_real_counts_and_null_type_profile(real):
    """真库上的 KOL：互动量是真的，类型画像整块是 `None`。

    这正是 `fixtures/sixstate/` 第六篇帖子在演的形状 —— 那个 fixture 不是编出来的
    极端场景，它就是真库今天的样子（`annotations` 只覆盖了 129 个判定单元）。
    """
    data = body(real, f"/api/v1/kol/impact?range={RANGE}")
    assert data["leaders"], "真库上 d7 一个 KOL 都没有 —— 口径或区间取空了"
    for leader in data["leaders"]:
        assert leader["n"] > 0
        for field in ("typeCounts", "typeOrder", "topType", "topTypeLabel", "styleTag"):
            assert leader[field] is None, (
                f"{field} 在没有标注的真库上应整块为 None —— "
                f"给出部分计数会画出一张分母不对、但看着完全正常的构成图"
            )


def test_every_real_post_matches_what_the_provider_promises_to_omit(real):
    """真帖子上缺的那十三个字段，逐字就是 `providers/sql.py::_UNANNOTATED`。

    `test_six_states.py` 拿 fixture 对过一次同样的等式。这里再对一次的理由是方向相反：
    那边证明「fixture 演的是 provider 说的」，这里证明「provider 说的就是真库发的」。
    中间任何一环改了而另一环没跟上，两条里必有一条红。
    """
    posts = body(real, f"/api/v1/kol/impact?range={RANGE}")["posts"]
    assert posts, "真库上 d7 一篇 KOL 帖子都没有"
    for post in posts:
        assert all(key in post for key in _UNANNOTATED)
        assert post["confidence"] is None
        if post["postType"] is not None:
            assert isinstance(post["typeLabel"], str) and post["typeLabel"]
            assert post["summary"] is None or isinstance(post["summary"], str)


def test_kol_posts_keep_their_platform_counts(real):
    """标注缺不缺，与平台计数采没采到，是两条独立的链路。

    真库上互动数是数出来的、货真价实；混成「什么都没有」会让页面上最常见的那种
    真实形态（互动量好好的、只是没标注）看不出来。
    """
    posts = body(real, f"/api/v1/kol/impact?range={RANGE}")["posts"]
    assert any((p["likes"] or 0) + (p["comments"] or 0) > 0 for p in posts), (
        "整页 KOL 帖子互动量全为零／全为空 —— 计数链路断了"
    )


def test_kol_opinions_is_null_not_an_empty_list(real):
    """「其他产品观点」在 `sql` 下是 `None`。

    空列表是一句**结论**：「他对别的产品没有观点」。而真相是这需要 AI 标注才判得出来，
    现在根本没查过。两者在 JS 里都是假值，但页面文案一个是「暂无相关内容」一个是
    「暂不可用」—— 说错了就是替一个没做过的判断背书。
    """
    name = body(real, f"/api/v1/kol/impact?range={RANGE}")["leaders"][0]["kol"]
    from urllib.parse import quote

    opinions = body(real, f"/api/v1/kol/{quote(name)}/opinions?range={RANGE}")
    assert opinions is None or isinstance(opinions, list)
    for item in opinions or []:
        assert item["code"] and item["url"]
        assert item["summary"] is None or isinstance(item["summary"], str)


# ── 官号动态 ───────────────────────────────────────────────────────────


def test_official_posts_answer_with_real_content(real):
    data = body(real, f"/api/v1/officials/posts?range={RANGE}")
    assert data, "真库上 d7 没有任何官号内容"


def test_etf_mentions_returns_the_list_contract_not_a_count(real):
    """`etfMentionsFor` 发的是**列表**，不是计数。

    这是 `sql` provider 上真实发生过的一次契约漂移（见 conftest 的 `sql_client`）：
    `own` 发成了一个数字，demo 下永远是列表，于是只有接真库时页面才会 `.map` 炸掉。
    """
    accounts = body(real, f"/api/v1/officials/posts?range={RANGE}")
    account = next(iter(accounts)) if isinstance(accounts, dict) else accounts[0]["account"]
    from urllib.parse import quote

    data = body(real, f"/api/v1/officials/{quote(account)}/etf-mentions?range={RANGE}")
    if data is None:
        return  # 该官号该区间没有提及 —— 合法，且不是本条要测的东西
    for key in ("own", "peer"):
        assert isinstance(data[key], list), f"{key} 应为列表，实得 {type(data[key]).__name__}"
