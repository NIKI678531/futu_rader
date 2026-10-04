"""官号域端点 —— PRD §5 `officialPosts(range)` / `etfMentionsFor(account, range)`。"""

import json

import pytest

from providers.demo import FIXTURE_DIR, DemoProvider


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
     "summary", "fullText", "evidenceIdx", "mentioned", "camp", "campPrimary",
     "sourceAnchorCode", "attributedProducts", "attributionStatus", "attributedCamp"],
)
def test_post_carries_every_field_the_feed_card_needs(client, field):
    assert field in client.get("/api/v1/officials/posts?range=d7").get_json()["data"][0]


def test_demo_post_uses_the_explicit_attribution_fixture(client):
    posts = client.get("/api/v1/officials/posts?range=d7").get_json()["data"]
    post = next(item for item in posts if item["id"] == "华夏@13|d7")

    assert post["sourceAnchorCode"] == "7261"
    assert [item["code"] for item in post["attributedProducts"]] == ["7261", "7266"]
    assert post["attributionStatus"] == "explicit"
    assert post["attributedCamp"] == "both"
    # The compatibility fields are projections of the explicit attribution,
    # not independent legacy inputs.
    assert post["mentioned"] == post["attributedProducts"]
    assert post["camp"] == post["attributedCamp"]


def test_generated_demo_fixture_persists_the_explicit_attribution_contract():
    fixtures = json.loads((FIXTURE_DIR / "official_posts.json").read_text(encoding="utf-8"))

    for posts in fixtures.values():
        for post in posts:
            assert {
                "sourceAnchorCode",
                "attributedProducts",
                "attributionStatus",
                "attributedCamp",
            } <= post.keys()
            assert post["mentioned"] == post["attributedProducts"]
            assert post["camp"] == post["attributedCamp"]


def test_demo_attribution_never_promotes_legacy_fields(tmp_path):
    """旧 mentioned/camp 即使有值，也不得偷偷升级成新归属。"""
    (tmp_path / "official_posts.json").write_text(
        json.dumps(
            {
                "d1": [
                    {
                        "id": "anchor-only",
                        "code": "3068",
                        "mentioned": [
                            {
                                "code": "3042",
                                "name": "华夏比特币ETF",
                                "issuer": "AMC 华夏",
                                "ownership": "peer",
                            }
                        ],
                        # These stale values may have been derived from the
                        # anchor; only attributedProducts/status are evidence.
                        "camp": "own",
                        "campPrimary": "own",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    post = DemoProvider(fixture_dir=tmp_path).official_posts("d1")[0]

    assert post["sourceAnchorCode"] == "3068"
    assert post["attributedProducts"] == []
    assert post["attributionStatus"] == "unattributed"
    assert post["attributedCamp"] == "none"
    assert post["mentioned"] == []
    assert post["camp"] == "none"
    assert post["campPrimary"] == "none"


def test_demo_compatibility_fields_follow_explicit_attribution(tmp_path):
    explicit = {
        "code": "3042",
        "name": "华夏比特币ETF",
        "issuer": "AMC 华夏",
        "ownership": "peer",
    }
    stale = {
        "code": "3068",
        "name": "旧挂载产品",
        "issuer": "CSOP 南方东英",
        "ownership": "own",
    }
    (tmp_path / "official_posts.json").write_text(
        json.dumps(
            {
                "d1": [
                    {
                        "id": "explicit",
                        "code": "3068",
                        "mentioned": [stale],
                        "camp": "own",
                        "campPrimary": "own",
                        "sourceAnchorCode": "3068",
                        "attributedProducts": [explicit],
                        "attributionStatus": "explicit",
                        "attributedCamp": "competitor",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    post = DemoProvider(fixture_dir=tmp_path).official_posts("d1")[0]

    assert post["sourceAnchorCode"] == "3068"
    assert post["code"] == "3042"
    assert post["mentioned"] == [explicit]
    assert post["camp"] == "competitor"
    assert post["campPrimary"] == "competitor"


def test_demo_etf_mentions_are_aggregated_from_explicit_post_attribution(tmp_path):
    product = {
        "code": "3042",
        "name": "华夏比特币ETF",
        "issuer": "AMC 华夏",
        "ownership": "peer",
    }
    (tmp_path / "master.json").write_text(
        json.dumps({"officials": [{"short": "华夏", "full": "华夏"}]}),
        encoding="utf-8",
    )
    (tmp_path / "official_posts.json").write_text(
        json.dumps(
            {
                "d1": [
                    {
                        "id": "explicit",
                        "account": "华夏",
                        "fullText": ["$03042.HK$ 与 $华夏比特币ETF（3042.HK）$"],
                        "sourceAnchorCode": "3068",
                        "attributedProducts": [product],
                        "attributionStatus": "explicit",
                        "attributedCamp": "competitor",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    # Deliberately contradictory legacy aggregate: it must never be read.
    (tmp_path / "etf_mentions.json").write_text(
        json.dumps({"华夏|d1": {"list": [{"code": "3068", "count": 99}]}}),
        encoding="utf-8",
    )

    data = DemoProvider(fixture_dir=tmp_path).etf_mentions_for("华夏", "d1")

    assert [item["code"] for item in data["list"]] == ["3042"]
    assert data["list"][0]["count"] == 2
    assert data["postCount"] == 1


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
    """`ETF_MENTION_RULE` 逐字：「一帖内出现 3 次计 3」；挂载标的不参与归属。

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
