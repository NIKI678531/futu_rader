"""GET /api/v1/meta 的契约测试。

这里的断言不是「测试代码写对了」，而是护栏：六态文案与热度公式一旦被改动，
CLAUDE.md 的铁律 1、2 就已经破了，测试必须先红。逐字来源 PRD §3.3、§3.6。
"""

from datetime import date, datetime, timedelta

from sqlalchemy import delete, update

from radar_db.schema import comments, feeds, ingestion_runs

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


def test_meta_exposes_comment_ai_routing_generation(client, sql_client):
    demo = client.get("/api/v1/meta").get_json()["data"]["aiCommentRouting"]
    real = sql_client.get("/api/v1/meta").get_json()["data"]["aiCommentRouting"]
    for value in (demo, real):
        assert value["ready"] is False
        assert value["ruleVersion"] == "content-cashtag-v1"
        assert len(value["productPoolDigest"]) == 64


def test_demo_collection_metadata_is_explicitly_unknown(client):
    """演示 fixture 不是在线采集证据，不能把生成器行数冒充生产计数。"""
    assert client.get("/api/v1/meta").get_json()["data"]["dataCollection"] == {
        "freshness": "unavailable",
        "commentCoverage": "unknown",
        "sourceCompleteThrough": None,
        "lastSuccessfulSyncAt": None,
        "platformCommentCount": None,
        "parsedCommentCount": None,
    }


def test_sql_collection_metadata_keeps_known_counts_when_run_state_is_unknown(sql_client):
    """迁移来的历史事实可计数，但没有在线运行记录时不能宣称采集新鲜。"""
    collection = sql_client.get("/api/v1/meta").get_json()["data"]["dataCollection"]
    assert collection == {
        "freshness": "unavailable",
        "commentCoverage": "unknown",
        "sourceCompleteThrough": None,
        "lastSuccessfulSyncAt": None,
        "platformCommentCount": 17,
        "parsedCommentCount": 4,
    }


def test_empty_uncollected_sql_database_reports_null_instead_of_zero(sql_provider):
    """空表在首次成功全量采集前是未知，不是经过检查后的零。"""
    with sql_provider._engine.begin() as conn:
        conn.execute(delete(comments))
        conn.execute(delete(feeds))

    collection = sql_provider.collection_metadata()
    assert collection["commentCoverage"] == "unknown"
    assert collection["platformCommentCount"] is None
    assert collection["parsedCommentCount"] is None


def test_successful_collection_run_exposes_coverage_counts_and_utc_timestamp(sql_provider):
    finished = datetime(2026, 8, 26, 0, 5)
    with sql_provider._engine.begin() as conn:
        conn.execute(update(feeds).values(comment_coverage_status="complete"))
        conn.execute(
            update(feeds)
            .where(feeds.c.feed_id == 2)
            .values(comment_coverage_status="partial")
        )
        conn.execute(
            ingestion_runs.insert().values(
                run_id="sync-ok",
                source="market_insight",
                source_run_id="airflow-all-1",
                source_kind="all",
                status="succeeded",
                started_at=finished - timedelta(minutes=5),
                finished_at=finished,
                complete_through=date(2026, 8, 25),
            )
        )

    collection = sql_provider.collection_metadata()
    assert collection == {
        "freshness": "fresh",
        "commentCoverage": "partial",
        "sourceCompleteThrough": "2026-08-25",
        "lastSuccessfulSyncAt": "2026-08-26T00:05:00Z",
        "platformCommentCount": 17,
        "parsedCommentCount": 4,
    }


def test_latest_failed_collection_marks_previous_success_stale(sql_provider):
    finished = datetime(2026, 8, 26, 0, 5)
    with sql_provider._engine.begin() as conn:
        conn.execute(
            ingestion_runs.insert(),
            [
                {
                    "run_id": "sync-ok",
                    "source": "market_insight",
                    "source_run_id": "airflow-all-1",
                    "source_kind": "all",
                    "status": "succeeded",
                    "started_at": finished - timedelta(minutes=5),
                    "finished_at": finished,
                    "complete_through": date(2026, 8, 25),
                },
                {
                    "run_id": "sync-failed",
                    "source": "market_insight",
                    "source_run_id": "airflow-important-2",
                    "source_kind": "important",
                    "status": "failed",
                    "started_at": finished + timedelta(hours=1),
                    "finished_at": finished + timedelta(hours=1, minutes=2),
                    "complete_through": None,
                    "error_summary": "source unavailable",
                },
            ],
        )

    collection = sql_provider.collection_metadata()
    assert collection["freshness"] == "stale"
    assert collection["sourceCompleteThrough"] == "2026-08-25"
    assert collection["lastSuccessfulSyncAt"] == "2026-08-26T00:05:00Z"


def test_important_run_cannot_advance_source_complete_through(sql_provider):
    finished = datetime(2026, 8, 26, 0, 5)
    with sql_provider._engine.begin() as conn:
        conn.execute(
            ingestion_runs.insert(),
            [
                {
                    "run_id": "sync-all",
                    "source": "market_insight",
                    "source_run_id": "airflow-all-1",
                    "source_kind": "all",
                    "status": "succeeded",
                    "started_at": finished - timedelta(minutes=5),
                    "finished_at": finished,
                    "complete_through": date(2026, 8, 25),
                },
                {
                    "run_id": "sync-important",
                    "source": "market_insight",
                    "source_run_id": "airflow-important-2",
                    "source_kind": "important",
                    "status": "succeeded",
                    "started_at": finished + timedelta(hours=1),
                    "finished_at": finished + timedelta(hours=1, minutes=2),
                    # 即使调用方错误地写了更晚日期，读侧也只承认 all 的声明。
                    "complete_through": date(2026, 8, 26),
                },
            ],
        )

    assert sql_provider.collection_metadata()["sourceCompleteThrough"] == "2026-08-25"


def test_sql_data_version_changes_without_restart(sql_provider):
    from radar_db.revisions import bump_revision
    before = dict(sql_provider._meta)
    with sql_provider._engine.begin() as conn:
        bump_revision(conn, "annotation")
    assert sql_provider.refresh()
    assert before != sql_provider._meta


def test_progress_alone_does_not_invalidate_expensive_data(sql_provider):
    from sqlalchemy import insert
    from radar_db.schema import meta_kv
    sql_provider._cache["probe"] = "cached"
    with sql_provider._engine.begin() as conn:
        conn.execute(insert(meta_kv).values(k="own_analysis_progress", v='{"status":"running"}'))
    assert sql_provider.refresh() is False
    assert sql_provider._cache["probe"] == "cached"


def test_presets_are_the_five_ranges_with_d7_default(client):
    data = client.get("/api/v1/meta").get_json()["data"]
    assert [p["key"] for p in data["presets"]] == ["d1", "d2", "d7", "d14", "d30"]
    assert data["defaultRange"] == "d7"


def test_sql_exposes_month_to_date_with_backend_computed_dates(sql_client):
    data = sql_client.get("/api/v1/meta").get_json()["data"]
    preset = next(preset for preset in data["presets"] if preset["key"] == "mtd")
    response = sql_client.get("/api/v1/ranges/mtd")
    assert response.status_code == 200
    month = response.get_json()["data"]
    assert month["from"].endswith("-01")
    assert month["days"] == preset["days"]
    assert len(month["buckets"]) == preset["bucketCount"]
    assert sql_client.get("/api/v1/pool?range=mtd").status_code == 200
    benchmark = sql_client.get("/api/v1/products/3033/benchmark?range=mtd").get_json()["data"]
    assert "07-07" in benchmark["base"]["buckets"][0]["tip"]
    assert "08-01" not in benchmark["base"]["buckets"][0]["tip"]


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


def test_status_legend_carries_its_own_colours(client):
    """图例的底色／字色随口径下发，不由屏幕按 key 现拼。

    六态里只有「暂不可用」和「待确认」是警示色 —— 前者是数据没到，后者是 AI 说了但没人
    确认，都需要读的人停一下；「暂无内容」是蓝，检查过了、确实没有，不该看着像出事。
    把这个映射留在屏里，五个屏迟早各写各的，同一个「暂不可用」在两页上是两种颜色。
    （先例：SECTORS 的 hue／tint 也是随主数据下发的口径常量，ADR-0004。）
    """
    legend = {x["key"]: (x["bg"], x["fg"]) for x in
              client.get("/api/v1/meta").get_json()["data"]["statusLegend"]}
    warn = ("var(--warning-100)", "var(--warning-700)")
    assert legend["暂不可用"] == warn
    assert legend["待确认"] == warn
    assert legend["暂无内容"] == ("var(--csop-blue-50)", "var(--csop-blue-700)")
    assert legend["0"] == legend["—"] == legend["样本不足"] == ("var(--ink-100)", "var(--ink-700)")


# PRD 之外的一个键：ADR-0019 §4 的三档枚举。
AI_VALIDATION_LEVELS = ("none", "spot_check", "gold")


def test_ai_validation_says_none_because_nobody_verified_anything(client):
    """ADR-0019 §4「如实声明」的第一处，另外两处是板块总览 S6 与产品监控 P7。

    取消人工批准门槛之后，页面上每一条 AI 结论都是模型自己写的，没人看过。这个键
    是那句话的**唯一来源**：面板文案跟着它走（`lib/view.js` 的 `aiValidationNote`），
    汇报时也引它。三处分头写死，改了一处另外两处就开始撒谎。

    真做了抽检要改成 `spot_check` 时，改的是 `fixtures/meta.json` 这一个值 ——
    这条断言会红，那是对的：改验证等级是件需要有人点头的事。
    """
    data = client.get("/api/v1/meta").get_json()["data"]
    assert data["aiValidation"] == "none"
    assert data["aiValidation"] in AI_VALIDATION_LEVELS


def test_ai_validation_is_the_same_under_the_real_data_provider(sql_client):
    """demo 与 sql 同值。

    演示数据里的 AI 字段更不是验证过的 —— 它们是生成器编出来的。一旦两个 provider
    在这个键上分叉，「这一屏的结论验证到什么程度」就变成了「你用哪个 provider 截的图」。
    """
    assert sql_client.get("/api/v1/meta").get_json()["data"]["aiValidation"] == "none"


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


def test_stage_half_day_threshold_is_half_of_low_sample(client):
    """PRD 第 3 章阈值表「阶段观点半日阈值 5」。

    这个 5 不是另一个独立的数，是 `lowSample` 的一半（设计源写作
    `Math.ceil(LOW_SAMPLE / 2)`）：小时粒度下一天切成三段，每段的样本自然只有整日的
    几分之一，沿用 10 会让几乎每一段都判成样本不足、整页阶段观点塌成一片灰。
    改 lowSample 而忘了这个数，症状就是「换了个阈值之后小时视图突然没有阶段了」。
    """
    t = client.get("/api/v1/meta").get_json()["data"]["thresholds"]
    assert t["stageHalfDay"] == 5
    assert t["stageHalfDay"] == -(-t["lowSample"] // 2)


ETF_MENTION_RULE = (
    "提及 ETF 口径：官号正文中的产品代码／Cashtag／唯一产品名称，对照当前 ETF 产品池匹配；"
    "若正文未明确产品，仅在发行商、明确资产类别与唯一产品同时成立时推断，多义内容不归属。"
    "个股代码、个股名称不在词表内，不计入。次数按出现次数，一帖内出现 3 次计 3；"
    "挂载标的仅作来源审计，不参与归属。"
)


POST_TYPE_RULE = (
    "类型为双标签：内容形式（晒单／操作宣言／行情解读／产品推介／教学科普／活动福利／"
    "问答互动／其他）必有一枚；操作方向（加仓／减仓／建仓／清仓／持有观望）只在帖子"
    "表达了明确操作时出现，判不出方向的操作类帖子标「方向待确认」。"
)


def test_post_type_rule_is_verbatim(client):
    """双标签口径（PRD §4.3、O1／ADR-0013）。这段字印在 KOL 影响力页页脚。

    「判不出方向的操作类帖子标『方向待确认』」是这条口径的要害：把没判出来的说成
    「持有观望」就是在编造一个模型没给出的结论。改这句话之前先改 PRD。
    """
    assert client.get("/api/v1/meta").get_json()["data"]["rules"]["postType"] == POST_TYPE_RULE


HOT_SUMMARY_RULE = (
    "热议总结由 AI 归纳当前日期范围内该 ETF 最主流的具体观点，须为观点而非正负面判断；"
    "有效态度样本低于 10 条不输出。"
)


def test_hot_summary_rule_is_verbatim(client):
    """这段字是板块总览「热议总结」表头的 title，也是这一列的验收口径。

    「须为观点而非正负面判断」是要害：一旦有人把它实现成「整体偏正面」，这一列就变成了
    态度分类的复述，而榜单里紧挨着的正面／负面两列已经在说那件事了。
    「低于 10 条不输出」与 thresholds.lowSample 是同一个 10（PRD §3.5），不是巧合。
    """
    data = client.get("/api/v1/meta").get_json()["data"]
    assert data["rules"]["hotSummary"] == HOT_SUMMARY_RULE
    assert str(data["thresholds"]["lowSample"]) in HOT_SUMMARY_RULE


STAGE_RULE = (
    "阶段观点：当日按上午（00:00–12:00）／下午（12:00–17:00）／盘后（17:00–24:00）"
    "三段各归纳一条主流观点；多日先逐日归纳主流观点与情绪，再由 AI 把观点相近的连续"
    "日期合并为同一阶段，14 天及以上视图下单日孤立观点并入相邻阶段。样本不足的时段"
    "不参与合并，只在折线下方以灰点标记。阶段总结描述讨论区观点，不表述与价格的"
    "因果关系。"
)


def test_stage_rule_is_verbatim(client):
    """阶段观点口径（PRD §5 `stagesFor` 的 `rule` 字段、第 4 章 P12）。这段字印在
    产品监控页「热度变化与阶段观点」面板的说明位上。

    最后一句是要害：**「阶段总结描述讨论区观点，不表述与价格的因果关系」**。这一面板
    的热度折线与上方的 K 线共用横轴，所以「9 月 3 日减仓离场」紧挨着一根阴线是常态；
    只要总结里出现一次「因……而下跌」，这块只读舆情看板就变成了在给客户做因果归因。
    删掉这句比改一个数危险得多，而它不会让任何测试变红——除了这一条。
    """
    assert client.get("/api/v1/meta").get_json()["data"]["rules"]["stage"] == STAGE_RULE


def test_etf_mention_rule_is_verbatim(client):
    """账号域「提及 ETF」按**出现次数累加**，与市场域的评论去重（PRD §3.2，同一条评论
    对同一产品只计一次）**语义相反**。这段文字直接印在官号清单表头的 title 上，
    被人顺手「统一」成去重口径，页面上的数字就全错了，而且看不出来。
    """
    assert client.get("/api/v1/meta").get_json()["data"]["rules"]["etfMention"] == ETF_MENTION_RULE


# ── 主数据 ──────────────────────────────────────────────────────────────


def test_products_are_the_120_of_the_pool(client):
    products = client.get("/api/v1/meta").get_json()["data"]["products"]
    own = [p for p in products if p["ownership"] == "own"]
    peer = [p for p in products if p["ownership"] == "peer"]
    assert (len(own), len(peer)) == (61, 59)


def test_products_are_a_list_so_the_order_survives(client):
    """**必须是数组。** 产品代码是 '3033' 这样的纯数字字符串，JS 对象会把它们当整数键
    按数值升序重排，ORDER（自家在前、竞品在后）当场丢失，而前端的产品下拉、热力图
    都按这个顺序渲染。这条断言就是钉住「别顺手改成 {code: {...}} 字典」。
    """
    products = client.get("/api/v1/meta").get_json()["data"]["products"]
    assert isinstance(products, list)
    assert [p["code"] for p in products[:2]] == ["3033", "3037"]
    assert products[0]["ownership"] == "own" and products[-1]["ownership"] == "peer"


def test_officials_carry_their_homepage_url(client):
    """主页地址随主数据下发，不由屏幕现算。

    设计源里这个地址是 `R.hash(全称) % 80000000` 现算的——演示数据生成器泄漏进了
    屏幕代码（ADR-0004）。地址是账号的属性，接真实库后来自库里的账号表。
    """
    officials = client.get("/api/v1/meta").get_json()["data"]["officials"]
    assert len(officials) == 20
    assert all(o["url"].startswith("https://www.futunn.com/user/") for o in officials)
    assert {"short", "full", "comps", "url"} == set(officials[0])


def test_officials_do_not_leak_the_generator_baselines(client):
    """OFFICIAL 主数据里的 7 日发布篇数／互动基准是**演示生成器的输入**，不是业务字段。

    下发它们，前端迟早会有人拿去当「基准值」显示，而真实库里根本没有这个东西。
    """
    officials = client.get("/api/v1/meta").get_json()["data"]["officials"]
    assert not any(k in officials[0] for k in ("postsBase", "interBase", "2", "3"))


def test_kol_roster_is_named_entities_not_tuples(client):
    """合作 KOL 名单：`{name, tags, active}`，标签来自合作名单（PRD §4.4 逐字）。

    设计源里 KOLS 是 `[名字, '标签,标签', 1]` 三元组——那是手写数据表的形状，不是
    接口形状。下发命名字段，KOL 详情页就不用靠下标位置去认字段。
    """
    kols = client.get("/api/v1/meta").get_json()["data"]["kols"]
    assert kols and {"name", "tags", "active"} == set(kols[0])
    assert all(isinstance(k["active"], bool) for k in kols)
    assert all(k["tags"] for k in kols)


def test_chinese_is_not_escaped_on_the_wire(client):
    """逐字文案要能直接 grep，不能是 \\uXXXX。"""
    assert HEAT_FORMULA in client.get("/api/v1/meta").get_data(as_text=True)


def test_unknown_route_is_json_404_not_html(client):
    r = client.get("/api/v1/nope")
    assert r.status_code == 404
    assert r.is_json and "error" in r.get_json()
