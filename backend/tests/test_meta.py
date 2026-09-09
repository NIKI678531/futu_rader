"""GET /api/v1/meta 的契约测试。

这里的断言不是「测试代码写对了」，而是护栏：六态文案与热度公式一旦被改动，
CLAUDE.md 的铁律 1、2 就已经破了，测试必须先红。逐字来源 PRD §3.3、§3.6。
"""

# PRD §3.6 状态体系（六态，STATUS_LEGEND 逐字）
STATUS_LEGEND = [
    ("0", "已取得数据，且统计值确实为零。"),
    ("—", "字段不适用于该产品或该口径。"),
    ("暂不可用", "字段应有值，但当前数据源未提供或尚未核验。"),
    ("暂无内容", "范围内已完成检查，没有符合条件的内容。"),
    ("样本不足", "有效产品态度评论少于 10 条，不输出倾向结论。"),
    (
        "待确认",
        "AI 自动识别的竞品关系、动态分类或重点舆情（需合规关注）信号，尚未人工确认。",
    ),
]

# PRD §3.3 讨论热度（全站唯一公式）
HEAT_FORMULA = "讨论热度 ＝ 评论量 ＋ 0.3 × 点赞 ＋ 1 × 转发"
HEAT_NOTE = "点赞含帖子获赞与评论获赞，转发为相关帖子的转发数。"


def test_health(client):
    assert client.get("/health").get_json() == {"status": "ok"}


def test_meta_returns_ok_envelope(client):
    r = client.get("/api/v1/meta")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_presets_are_the_five_ranges_with_d7_default(client):
    data = client.get("/api/v1/meta").get_json()["data"]
    assert [p["key"] for p in data["presets"]] == ["d1", "d2", "d7", "d14", "d30"]
    assert data["defaultRange"] == "d7"


def test_bucket_granularity_follows_prd_3_1(client):
    """≤2 天按小时；≤14 天按自然日；≥15 天按自然周。"""
    by_key = {p["key"]: p for p in client.get("/api/v1/meta").get_json()["data"]["presets"]}
    assert [by_key[k]["gran"] for k in ("d1", "d2", "d7", "d14", "d30")] == [
        "hour",
        "hour",
        "day",
        "day",
        "week",
    ]
    assert [by_key[k]["bucketCount"] for k in ("d1", "d2", "d7", "d14", "d30")] == [
        24,
        48,
        7,
        14,
        5,
    ]


def test_status_legend_is_verbatim(client):
    legend = client.get("/api/v1/meta").get_json()["data"]["statusLegend"]
    assert [(x["key"], x["text"]) for x in legend] == STATUS_LEGEND


def test_heat_formula_is_verbatim(client):
    heat = client.get("/api/v1/meta").get_json()["data"]["heat"]
    assert heat["formula"] == HEAT_FORMULA
    assert heat["note"] == HEAT_NOTE
    assert heat["weights"] == {"like": 0.3, "share": 1}


def test_sectors_are_the_eight_of_prd_3_7(client):
    sectors = client.get("/api/v1/meta").get_json()["data"]["sectors"]
    assert [s["key"] for s in sectors] == ["hk", "a", "us", "sgl", "apac", "fi", "cm", "va"]


def test_thresholds_match_prd_3_5(client):
    t = client.get("/api/v1/meta").get_json()["data"]["thresholds"]
    assert t["lowSample"] == 10
    assert t["newDays"] == 30
    assert t["lowConfidence"] == 0.7


def test_chinese_is_not_escaped_on_the_wire(client):
    """逐字文案要能直接 grep，不能是 \\uXXXX。"""
    assert HEAT_FORMULA in client.get("/api/v1/meta").get_data(as_text=True)


def test_unknown_route_is_json_404_not_html(client):
    r = client.get("/api/v1/nope")
    assert r.status_code == 404
    assert r.is_json and "error" in r.get_json()
