"""KOL 域端点 —— PRD §5 `kolImpact(range)` 与 `kolOpinions(kol, range)`。

`kolProfile` 故意没有端点（ADR-0015、core/kol.py）：PRD §4.3 M5 要求声量排名随前端
筛选重算，画像是「当前可见帖子子集」上的聚合，不是一个能用查询参数表达的东西。
下面 test_leaders_are_the_unfiltered_profiles 钉住的是 leaders 的**全量**语义 ——
它是 KOL 详情页排序用的那份，不是 KOL 影响力页表格里那份。
"""

from urllib.parse import quote

import pytest


def test_impact_envelope_and_status(client):
    r = client.get("/api/v1/kol/impact?range=d7")
    assert r.status_code == 200
    body = r.get_json()
    assert set(body) == {"status", "data"}
    assert body["status"] == "ok"


def test_impact_default_range_is_d7(client):
    assert (
        client.get("/api/v1/kol/impact").get_data()
        == client.get("/api/v1/kol/impact?range=d7").get_data()
    )


def test_impact_unknown_range_is_404(client):
    r = client.get("/api/v1/kol/impact?range=nope")
    assert r.status_code == 404
    assert "error" in r.get_json()


def test_impact_top_level_shape(client):
    data = client.get("/api/v1/kol/impact?range=d7").get_json()["data"]
    assert set(data) >= {"posts", "leaders", "kolActive", "range", "updated"}
    assert data["posts"] and data["leaders"]


@pytest.mark.parametrize(
    "field",
    # 发帖记录表、类型筛选、抽屉、CSV 导出直接消费的字段。少一个页面就渲染不出来。
    ["id", "kol", "tags", "t", "day", "hour", "dateText", "time", "code", "name",
     "sector", "ownership", "issuer", "likes", "comments", "shares", "views",
     "engagement", "url", "postType", "typeLabel", "confidence", "direction",
     "directionLabel", "directionPending", "hasDir", "dir", "hasSummary", "summary",
     "fullText", "evidenceIdx", "typeEvidence", "mentioned", "camp", "campPrimary"],
)
def test_post_carries_every_field_the_table_needs(client, field):
    assert field in client.get("/api/v1/kol/impact?range=d7").get_json()["data"]["posts"][0]


def test_dual_label_direction_is_optional_but_form_is_not(client):
    """双标签（PRD §4.3、`rules.postType` 逐字）：内容形式必有一枚，操作方向可以没有。

    判不出方向的操作类帖子是 directionPending=True 而不是 direction='hold' ——
    把没判出来的说成「持有观望」，是六态里「待确认」存在的全部理由（PRD §3.6）。
    """
    posts = client.get("/api/v1/kol/impact?range=d7").get_json()["data"]["posts"]
    assert all(p["postType"] for p in posts)
    pending = [p for p in posts if p["directionPending"]]
    assert pending, "演示数据里应当存在方向待确认的帖子，否则这条断言什么也没测"
    assert all(p["direction"] is None or p["direction"] == "" for p in pending)


def test_camp_on_kol_posts_is_still_the_four_way_enum(client):
    """帖子上的 camp 枚举与官号页同一套；**页面怎么用它**才是两页不同的地方。

    KOL 页问「这个 KOL 提没提我们」，own 与 both 都算提了；官号页问「这条动态归哪个
    阵营」，一条只能归一类（ADR-0013 O4）。差异在屏幕的筛选谓词里，不在数据里。
    """
    posts = client.get("/api/v1/kol/impact?range=d7").get_json()["data"]["posts"]
    assert {p["camp"] for p in posts} <= {"own", "competitor", "both", "none"}


def test_leaders_are_the_unfiltered_profiles(client):
    """leaders 覆盖 posts 里出现的每一位 KOL，且篇数合计等于总帖数。

    这钉住的是「全量」：它不带任何筛选。KOL 影响力页表格里的画像是前端在可见子集上
    重算的（PRD §4.3 M5），两者数值不同是**对的**，别拿这条断言去卡那个。
    """
    data = client.get("/api/v1/kol/impact?range=d7").get_json()["data"]
    posts, leaders = data["posts"], data["leaders"]
    assert {l["kol"] for l in leaders} == {p["kol"] for p in posts}
    assert sum(l["n"] for l in leaders) == len(posts)


def test_leader_profile_shape(client):
    leader = client.get("/api/v1/kol/impact?range=d7").get_json()["data"]["leaders"][0]
    assert set(leader) >= {
        "kol", "n", "own", "peer", "both", "ownAny", "peerAny",
        "engagement", "comments", "typeCounts", "typeOrder",
        "topType", "topTypeLabel", "styleTag", "top", "posts",
    }
    # ownAny / peerAny 含 both —— 「提没提我们」的口径，两数之和可以大于 n。
    assert leader["ownAny"] == leader["own"] + leader["both"]
    assert leader["peerAny"] == leader["peer"] + leader["both"]


def test_longer_range_has_more_posts(client):
    d1 = client.get("/api/v1/kol/impact?range=d1").get_json()["data"]["posts"]
    d30 = client.get("/api/v1/kol/impact?range=d30").get_json()["data"]["posts"]
    assert len(d30) > len(d1)


# ── kolOpinions(kol, range)：其他产品观点及操作 ──────────────────────────


@pytest.fixture
def a_kol(client):
    """名单里的第一位 KOL。写死名字会让 fixture 一换就红，且中文名进源码没好处。"""
    return client.get("/api/v1/meta").get_json()["data"]["kols"][0]["name"]


def ops(client, kol, qs=""):
    return client.get(f"/api/v1/kol/{quote(kol)}/opinions{qs}").get_json()


def test_opinions_envelope_and_status(client, a_kol):
    r = client.get(f"/api/v1/kol/{quote(a_kol)}/opinions?range=d7")
    assert r.status_code == 200
    body = r.get_json()
    assert set(body) == {"status", "data"}
    assert body["status"] == "ok"


def test_opinions_default_range_is_d7(client, a_kol):
    assert ops(client, a_kol) == ops(client, a_kol, "?range=d7")


def test_opinions_unknown_range_is_404(client, a_kol):
    r = client.get(f"/api/v1/kol/{quote(a_kol)}/opinions?range=nope")
    assert r.status_code == 404
    assert "error" in r.get_json()


def test_opinions_unknown_kol_is_404_not_an_empty_table(client):
    """名单里没这个人 → 404。**不是** 200 + `[]`。

    两者在页面上天差地别：空数组会渲染成「这位 KOL 一条观点也没有」，而事实是
    我们根本不认识这个名字（铁律 2 的同一条边界，换到资源这一层）。
    """
    r = client.get("/api/v1/kol/查无此人/opinions?range=d7")
    assert r.status_code == 404


@pytest.mark.parametrize(
    "field",
    # 表格六列 + 展开原文 + CSV 导出直接消费的字段。
    ["code", "name", "issuer", "own", "sector", "sectorName", "summary", "excerpt",
     "action", "actionTone", "direction", "postType", "typeLabel", "confidence",
     "dateText", "timeText", "engagement", "url", "net"],
)
def test_opinion_row_carries_every_field_the_table_needs(client, a_kol, field):
    assert field in ops(client, a_kol, "?range=d7")["data"][0]


def test_opinions_exclude_products_already_in_the_post_table(client, a_kol):
    """「其他产品」的「其他」：发帖记录里出现过的产品不在这张表里重复列出（PRD §4.4）。"""
    posted = {
        p["code"]
        for p in client.get("/api/v1/kol/impact?range=d7").get_json()["data"]["posts"]
        if p["kol"] == a_kol
    }
    rows = ops(client, a_kol, "?range=d7")["data"]
    assert posted, "这位 KOL 在 d7 内应当有帖子，否则这条断言什么也没排除"
    assert {r["code"] for r in rows}.isdisjoint(posted)


def test_net_is_null_when_the_sample_is_too_thin(client):
    """情绪净值在有效态度评论不足阈值时是 `null`，**不是 0**（PRD §3.5、§3.6 样本不足）。

    `0` 在这一格的含义是「褒贬正好抵消，结论是中性」—— 那是个结论。样本不够时我们
    没有得出任何结论，下发 0 就是替客户编了一个。这条同时要求演示数据里两种情况都
    真实存在，否则断言测不到东西。
    """
    kols = [k["name"] for k in client.get("/api/v1/meta").get_json()["data"]["kols"]]
    rows = [r for k in kols[:6] for r in ops(client, k, "?range=d7")["data"]]
    nets = [r["net"] for r in rows]
    assert any(n is None for n in nets), "演示数据里应当存在样本不足的行"
    assert any(n is not None for n in nets), "也应当存在样本充足的行，否则测不出区别"
    # None 走的是 JSON null 而不是被 Flask 序列化成 0 / "" / 缺键。
    assert all(n is None or isinstance(n, (int, float)) for n in nets)
    assert all("net" in r for r in rows)


def test_opinions_sorted_by_engagement_desc(client, a_kol):
    eng = [r["engagement"] for r in ops(client, a_kol, "?range=d7")["data"]]
    assert eng == sorted(eng, reverse=True)


def test_opinions_are_per_kol(client):
    kols = [k["name"] for k in client.get("/api/v1/meta").get_json()["data"]["kols"]]
    a, b = ops(client, kols[0], "?range=d7")["data"], ops(client, kols[1], "?range=d7")["data"]
    assert a != b
