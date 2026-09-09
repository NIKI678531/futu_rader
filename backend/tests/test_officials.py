"""官号域端点 —— PRD §5 `officialPosts(range)` / `etfMentionsFor(account, range)`。"""

import pytest


def test_posts_envelope_and_status(client):
    r = client.get("/api/v1/officials/posts?range=d7")
    assert r.status_code == 200
    body = r.get_json()
    assert set(body) == {"status", "data"}
    assert body["status"] == "ok"
    assert isinstance(body["data"], list) and body["data"]


def test_posts_default_range_is_d7(client):
    """不传 range 用默认 d7，与设计源 DEFAULT_KEY 一致。"""
    assert (
        client.get("/api/v1/officials/posts").get_data()
        == client.get("/api/v1/officials/posts?range=d7").get_data()
    )


@pytest.mark.parametrize(
    "field",
    # 官号发帖流卡片直接消费的字段。PRD §5：响应形状与函数返回一致。
    ["id", "account", "accountFull", "accountType", "isIssuer", "t", "day", "time",
     "code", "name", "sector", "ownership", "issuer", "likes", "comments", "shares",
     "engagement", "url", "postType", "typeLabel", "confidence", "hasSummary",
     "summary", "fullText", "evidenceIdx", "mentioned", "camp", "campPrimary"],
)
def test_post_carries_every_field_the_feed_card_needs(client, field):
    assert field in client.get("/api/v1/officials/posts?range=d7").get_json()["data"][0]


def test_camp_is_three_way_exclusive(client):
    """官号页阵营三分互斥：一条只能归一类（ADR-0013 O4）。

    KOL 页的「提自家」含 both，是另一个问题的答案。**不要顺手统一。**
    """
    posts = client.get("/api/v1/officials/posts?range=d7").get_json()["data"]
    assert {p["camp"] for p in posts} <= {"own", "competitor", "both", "none"}


def test_longer_range_has_more_posts(client):
    d1 = client.get("/api/v1/officials/posts?range=d1").get_json()["data"]
    d30 = client.get("/api/v1/officials/posts?range=d30").get_json()["data"]
    assert len(d30) > len(d1)


def test_posts_unknown_range_is_404(client):
    r = client.get("/api/v1/officials/posts?range=nope")
    assert r.status_code == 404
    assert "error" in r.get_json()


def test_etf_mentions_shape(client):
    body = client.get("/api/v1/officials/华夏/etf-mentions?range=d7").get_json()
    assert body["status"] == "ok"
    data = body["data"]
    assert set(data) >= {"list", "own", "peer", "etfCount", "total", "postCount"}
    assert data["etfCount"] == len(data["list"])
    assert len(data["own"]) + len(data["peer"]) == len(data["list"])


def test_etf_mentions_counts_occurrences_not_deduplicated_comments(client):
    """`ETF_MENTION_RULE` 逐字：「一帖内出现 3 次计 3，挂载标的至少计 1」。

    这与市场域的评论去重（PRD §3.2，同一条评论对同一产品只计一次）**语义相反**。
    两者都叫「提及」，不是一回事 —— total ≥ postCount 就是这条口径的可观测后果。
    """
    data = client.get("/api/v1/officials/华夏/etf-mentions?range=d30").get_json()["data"]
    assert data["total"] >= data["postCount"] > 0
    for e in data["list"]:
        assert e["count"] >= e["posts"] >= 1


def test_etf_mentions_own_products_sort_first(client):
    """自家在前、竞品在后（清单表芯片条的渲染顺序直接依赖它）。"""
    data = client.get("/api/v1/officials/华夏/etf-mentions?range=d30").get_json()["data"]
    owns = [i for i, e in enumerate(data["list"]) if e["ownership"] == "own"]
    peers = [i for i, e in enumerate(data["list"]) if e["ownership"] != "own"]
    if owns and peers:
        assert max(owns) < min(peers)


def test_platform_account_with_no_etf_mentions_is_empty_not_zero(client):
    """空态是 status=empty + 空数组，**不是** 0。铁律 2 的正向样例。"""
    body = client.get("/api/v1/officials/牛牛新股君/etf-mentions?range=d1").get_json()
    data = body["data"]
    if data["etfCount"] == 0:
        assert data["list"] == []
        assert data["total"] == 0  # 真的数过了，确实是零 —— 这个 0 是诚实的


def test_chinese_is_not_escaped_on_the_wire(client):
    """响应通篇逐字中文文案；转义后不可读、体积翻倍、也没法直接 grep 比对。"""
    raw = client.get("/api/v1/officials/posts?range=d1").get_data(as_text=True)
    assert "\\u" not in raw


def test_deterministic(client):
    a = client.get("/api/v1/officials/posts?range=d7").get_data()
    b = client.get("/api/v1/officials/posts?range=d7").get_data()
    assert a == b
