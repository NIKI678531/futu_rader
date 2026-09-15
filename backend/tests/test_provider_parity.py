"""demo 与 sql 两个 provider 必须在**同一个契约**上（ADR-0001）。

## 这条测试是为什么存在的

在它之前，22 个端点只在 demo provider 下被测过。demo 读 fixture，fixture 由设计源导出，
字段永远齐全 —— 所以「sql 下这个端点返回的形状不对」这一整类问题，一条测试都碰不到。

结果就是 `etfMentionsFor` 的 `own` / `peer` 在 sql 下发成了**两个计数**，而契约要的是
**两个子列表**。官号动态页那句 `em.own.map(chipEl)` 在 `DATA_PROVIDER=sql` 下当场
TypeError，整屏白；而屏级边界把它报成「后端服务连不上」，于是排查方向从第一秒就是错的。
这个 bug 从写下那天到被发现，中间隔着一次完整的验收 —— 因为没有人手工起过 sql 服务。

## 断言什么、不断言什么

**不比数值。** 两个 provider 的数据源不同，值本来就不一样，比了只会天天红。

比的是**形状**，规则两条：

1. **键集必须相同。** demo 有的键 sql 必须有，反之亦然。少一个键 = 前端读到 undefined；
   多一个键 = 有人在 provider 里加了字段却没进契约。
2. **类型必须相同，`null` 例外。** sql 在任何位置都可以是 `null`（那是合法的
   「暂不可用」，铁律 2 要的就是它）；但 `list` 不能变成 `int`，`dict` 不能变成 `str`。

再加一条与形状无关、但同样只有跑双 provider 才看得见的：

3. **同一个不存在的资源，两边必须给同一个 HTTP 状态码。** 一个 404 一个 200，
   意味着打错代码在 demo 下是「没这个东西」，在 sql 下是「数据暂不可用」——
   后者会把一个拼写错误伪装成采集故障。

## 已知的盲区：demo 那边列表为空的位置

两个 provider 的数据不同，于是会出现「sql 的某个列表有元素、demo 的同名列表是空的」。
空列表里没有元素可下钻，demo 侧就**没有**这个 `[]` 路径的键集可比 —— 例如
`etf-mentions` 的 `own`：内存库里那个官号提了自家 ETF，而演示数据里它一只都没提。

这种位置只能跳过，不能判失败（判失败就是在拿数据差异当契约差异）。跳过的路径会
`print` 出来，`pytest -s` 能看见 —— 它是**覆盖缺口**，不是通过项：那些子对象的键集
这条测试没查过。要补，得让内存库在该位置也有元素（`tests/sql_fixture.py`），
而不是放宽这里的断言。
"""

import pytest

from sql_fixture import KOL_NAME, OFFICIAL_SHORT, OWN_CODE

# 逐个端点（PRD §5 的 23 组契约函数）。参数选的都是 demo fixture 与内存库里**都有**的
# 键，不然比的就是「两边数据不同」而不是「两边契约不同」。
ENDPOINTS = [
    "/meta",
    "/ranges/d1",
    "/pool?range=d1",
    "/ranks?range=d1",
    f"/products/{OWN_CODE}/benchmark?range=d1",
    f"/products/{OWN_CODE}/heat-series?range=d1",
    f"/products/{OWN_CODE}/daily",
    f"/products/{OWN_CODE}/candles?range=d1",
    f"/products/{OWN_CODE}/stages?range=d1",
    f"/products/{OWN_CODE}/summary?range=d1",
    f"/products/{OWN_CODE}/themes?range=d1",
    f"/products/{OWN_CODE}/negative-categories?range=d1",
    f"/products/{OWN_CODE}/competitors?range=d1",
    f"/products/{OWN_CODE}/compliance?range=d1",
    f"/products/{OWN_CODE}/topics?range=d1",
    f"/products/{OWN_CODE}/kol-mentions?range=d1",
    f"/products/{OWN_CODE}/evidence?ctx=d1%7Csum&polarity=neutral&n=3",
    "/hot-summaries?range=d1",
    "/officials/posts?range=d1",
    f"/officials/{OFFICIAL_SHORT}/etf-mentions?range=d1",
    "/kol/impact?range=d1",
    f"/kol/{KOL_NAME}/opinions?range=d1",
]

# 不存在的资源。两边必须同码（第 3 条）。
UNKNOWN = [
    "/ranges/d999",
    "/pool?range=d999",
    "/products/0700/benchmark?range=d1",
    "/products/0700/topics?range=d1",
    "/products/0700/daily",
    "/officials/查无此号/etf-mentions?range=d1",
    "/kol/查无此人/opinions?range=d1",
]


def _walk(value, path, shape):
    """把一份 JSON 压成 `{路径: 见过的类型}` ＋ `{路径: 见过的键}` ＋ `{列表路径: 元素数}`。

    列表按**元素**下钻并把结果并起来（路径里写成 `[]`）：契约说的是「列表里每一个
    元素长这样」，不是「第 0 个元素长这样」。只看第 0 个的话，一个 120 元素的池里
    有一个元素少了字段就漏过去了。

    `lists` 记的是每个列表路径上**总共**下钻过多少个元素，用来分辨「demo 里没有这条
    路径」的两种原因：列表是空的（数据差异，跳过），还是列表有元素但那些元素里没有
    这个键（契约差异，判失败）。
    """
    types, keys, lists = shape
    if value is None:
        types.setdefault(path, set()).add("null")
        return
    if isinstance(value, dict):
        types.setdefault(path, set()).add("dict")
        keys.setdefault(path, set()).update(value)
        if isinstance(value.get("status"), str):
            _STATUS.setdefault(id(shape), {})[path] = value["status"]
        for k, v in value.items():
            _walk(v, f"{path}.{k}", shape)
        return
    if isinstance(value, list):
        types.setdefault(path, set()).add("list")
        lists[path] = lists.get(path, 0) + len(value)
        for v in value:
            _walk(v, f"{path}[]", shape)
        return
    # bool 必须在 int 之前判：Python 里 True 是 int 的实例，不分开的话
    # 「demo 发 true / sql 发 1」这种差异会被当成同类型放过去。
    if isinstance(value, bool):
        types.setdefault(path, set()).add("bool")
    elif isinstance(value, (int, float)):
        types.setdefault(path, set()).add("number")
    else:
        types.setdefault(path, set()).add("str")


# 每个 dict 路径上的 `status` 值（六态）。形状按契约**随 status 变**：热议总结 `ok` 才有
# `tone`，竞品 `unavailable` 时 list 为空……两边同一路径 status 不同时，比键集比的是数据差异，
# 不是契约差异，所以那棵子树跳过（打印出来，不算通过）。
_STATUS = {}


def _shape(payload):
    shape = ({}, {}, {})
    _walk(payload, "$", shape)
    statuses = _STATUS.pop(id(shape), {})
    return shape + (statuses,)


# sql 侧比设计源契约**多出来**的键（白名单，不是放宽：除此之外的新键仍然判失败）。
# demo fixture 冻结自设计源，不会有它们；前端读不到就是 undefined，与 null 同义，不会炸。
#
# ADR-0020 Layer B 生成物附带的可追溯信息：
# - `reviewState`   徽章要的 review_state（ADR-0019 §2）
# - `evidenceIds`   证据侧栏要的生成物引用 id
# - `labelStatus` / `aiStatus` / `reasonStatus`   「模型还没写这一段」的三个位置
# - `key` / `subkey` / `aspect` / `units` / `points` / `category`   core 分桶的稳定键
#
# ADR-0022 热度下限口径：
# - `heatUnknownPosts`   窗口／桶／自家合计里转发数未知的帖子数；大于零时 shares /
#                        interactions / discussionHeat 是下限。benchmark 上是
#                        `{current, base}` 两侧分说。demo 的转发数永远已知，没有这个键。
#
# Layer B 脏标记（`synth_dirty_*`）：
# - `stale`   现行生成物写下之后底层标注又变了、还没重新汇总。原来这时整块生成物被藏起来
#             （页面上总结突然消失），现在照常下发并标 stale，让前端挂「待更新」。
#
# 事实字段脱离 AI 门控：
# - `evidenceCount`   summary_for 的证据条数。demo 在 topics / themes 上本来就有这个键，
#                     进白名单意味着那两处的键集比对也跳过它 —— 已知的覆盖缺口。
# - `aiValidationDetail`   /meta 上 `meta_kv.ai_validation` 的整份 JSON（demo 下 None）。
EXTENSION_KEYS = frozenset({
    "reviewState", "evidenceIds", "labelStatus", "aiStatus", "reasonStatus",
    "key", "subkey", "aspect", "units", "points", "category",
    "heatUnknownPosts",
    "stale",
    "evidenceCount", "aiValidationDetail",
})


def _under_extension(path):
    return any(seg.rstrip("[]") in EXTENSION_KEYS for seg in path.split(".")[1:])


def _status_differs(path, d_status, s_status):
    """`path` 自己或任一祖先 dict 的 status 两边不同。"""
    p = path
    while p:
        if p in d_status and p in s_status and d_status[p] != s_status[p]:
            return True
        cut = max(p.rfind("."), p.rfind("["))
        if cut <= 0:
            break
        p = p[:cut]
    return False


def _blind(path, d_lists):
    """demo 在 `path` 上无从比较，是因为它上游某个列表是空的吗？

    `$.own[].code` 的上游列表是 `$.own`；`$.list[].mentioned[]` 的是 `$.list` 与
    `$.list[].mentioned`。任意一层是空列表，下面整棵子树在 demo 侧就都不存在 ——
    那是数据差异。反过来，上游列表**有**元素却仍然没有这条路径，就是真差异。
    """
    return any(
        path[i : i + 2] == "[]" and d_lists.get(path[:i]) == 0 for i in range(len(path) - 1)
    )


def _get(client, url):
    res = client.get("/api/v1" + url)
    body = res.get_json() if res.is_json else None
    return res.status_code, body


# ── /meta 是两边唯一同源的端点，先把它单拎出来 ───────────────────────


class TestShape:
    """22 个端点，逐个比形状。"""

    @pytest.mark.parametrize("url", ENDPOINTS)
    def test_same_contract_shape(self, client, sql_client, url):
        demo_code, demo_body = _get(client, url)
        sql_code, sql_body = _get(sql_client, url)

        assert demo_code == 200, f"demo 下 {url} 不是 200：{demo_code}"
        assert sql_code == 200, f"sql 下 {url} 不是 200：{sql_code}"

        d_types, d_keys, d_lists, d_status = _shape(demo_body["data"])
        s_types, s_keys, _, s_status = _shape(sql_body["data"])
        blind = []

        # ① 键集。只比 sql 真的给出了 dict 的那些路径 —— sql 在某处整块 null
        # （AI 标注未建）是合法的，那时它没有键集可比。
        for path, sk in s_keys.items():
            if _under_extension(path):
                continue
            if _status_differs(path, d_status, s_status):
                blind.append(path)
                continue
            dk = d_keys.get(path)
            if dk is None:
                if _blind(path, d_lists):
                    blind.append(path)
                    continue
                pytest.fail(
                    f"{url}：sql 在 {path} 给了一个 demo 里不存在的对象。"
                    f"契约以设计源为准，多出来的字段说明 provider 和契约岔开了。键={sorted(sk)}"
                )
            assert sk - EXTENSION_KEYS == dk - EXTENSION_KEYS, (
                f"{url} 的 {path} 键集不一致。\n"
                f"  只在 demo：{sorted(dk - sk)}\n"
                f"  只在 sql ：{sorted(sk - dk - EXTENSION_KEYS)}\n"
                f"前者会让前端读到 undefined，后者说明有人加了字段没进契约。"
            )

        # ② 类型。sql 可以在任何地方是 null，但不能把 list 换成 number。
        for path, st in s_types.items():
            if _under_extension(path) or _status_differs(path, d_status, s_status):
                continue
            dt = d_types.get(path)
            if dt is None:
                if _blind(path, d_lists):
                    continue
                pytest.fail(f"{url}：sql 多出了路径 {path}（类型 {sorted(st)}），demo 里没有。")
            extra = st - dt - {"null"}
            assert not extra, (
                f"{url} 的 {path} 类型不一致：demo={sorted(dt)}，sql={sorted(st)}。\n"
                f"`null` 是允许的（那是「暂不可用」），换一种类型不是 —— "
                f"前端按契约里的类型写代码，比如 `em.own.map(...)`。"
            )

        # 跳过的不是通过的。打出来，别让缺口变成静默的。
        if blind:
            print(f"\n[parity] {url} 未比对（demo 侧列表为空）：{sorted(blind)}")


class TestUnknownResourceStatusParity:
    """同一个不存在的资源，两边同码。"""

    @pytest.mark.parametrize("url", UNKNOWN)
    def test_same_http_status(self, client, sql_client, url):
        demo_code, _ = _get(client, url)
        sql_code, _ = _get(sql_client, url)
        assert demo_code == sql_code == 404, (
            f"{url}：demo 回 {demo_code}，sql 回 {sql_code}。\n"
            f"两边都该是 404。其中一边回 200 + unavailable 的话，"
            f"URL 里打错一个代码会显示成一屏「暂不可用」—— 看起来像采集掉了数，"
            f"于是人去查采集，而那边一切正常。"
        )
