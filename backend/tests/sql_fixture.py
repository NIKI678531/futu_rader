"""内存瘦库 ＋ 一小撮编出来的数据 —— `sql` provider 的测试底座。

从 `test_sql_provider.py` 里搬出来的，因为现在有第二个消费者：
`test_provider_parity.py` 要拿同一份数据把 demo 与 sql 两个 provider 的返回**形状**
对起来。搬出来而不是复制一份，是因为复制的那份会慢慢和这份长得不一样，而两边跑的
数据不同的时候，「形状对不上」这条断言就不知道是真差异还是数据差异了。

**不接真库。** 真库在 `%LOCALAPPDATA%\\futu-radar\\radar.db`，4.9 GB，且含真实用户
昵称／IP 归属地／个人简介（ADR-0008）—— 测试不该依赖一个不进 git、每台机器都不一样、
还带 PII 的文件。这里用 `radar_db.schema` 在内存里建同一套表。schema 是共用的那一份，
所以「列名对不上」这类错误照样抓得到；抓不到的是「dump 里那一列的实际含义和我们
以为的不一样」—— 那种事只能靠对真库的实测（见 `test_real_db_smoke.py`）。
"""

import json
from datetime import datetime, timedelta

from core.calendar import parse_anchor

# 先导入 provider：它会把仓库根塞进 sys.path，下面的 radar_db 才 import 得到
# （见 providers/sql.py 顶部的 sys.path 守卫）。
from providers.sql import SqlProvider
from radar_db import create_all
from radar_db.comment_filter import filter_readiness_values
from radar_db.product_catalog import load_products
from radar_db.schema import (
    annotation_evidence,
    annotation_runs,
    annotations,
    comments,
    feed_mentions,
    feeds,
    mentions,
    meta_kv,
)

ANCHOR = "2026-08-25"  # 真实数据的锚点：最近一个完整自然日
MASTER = {"products": load_products()}
OWN_CODE = "3033"  # 恒生科技指數ETF，CSOP 南方东英
PEER_CODE = "3032"  # 同名竞品，恒生投资
OFFICIAL_FULL = "恒生投資管理有限公司"  # 有 comps ⇒ 发行商官号
OFFICIAL_SHORT = "恒生投资"
KOL_NAME = "孫子的末代傳人"  # master.json 里 active 的 KOL

# 帖子正文与评论正文。原来都是「正文」两个字 —— 计数路径不看内容，写什么都行。
# 标注读路径看：`annotation_evidence.start_offset` 是**字符偏移**，`evidenceIdx` 是
# 「第几句」，一句话的正文里这两者恒等于 0／0，错位一整句也测不出来。所以这里每篇
# 正文都真有两句，测试用 `.index()` 现算偏移（而不是手写一个数字，那个数字会在有人
# 改一个字之后静默错位 —— 正是这条读路径最怕的那种错）。
FEED_TEXT = "这只 ETF 我今天加了一手。费率比同类低，打算长期拿着。"
COMMENT_TEXT = {
    11: "费率比同行高了不少，不太划算。",
    12: "感谢支持，我们会持续跟进。",
    13: "跟踪误差挺小的，拿着放心。",
    14: "前一天的讨论，落在基准区间里。",
}


def _feed(
    feed_id, day, hour, uid, name, likes, n_comments, shares, browse=None, *, code=OWN_CODE
):
    return {
        "feed_id": feed_id,
        "code": code,
        "source_ticker": f"0{code}.HK",
        # Feed timestamps are canonical UTC-naive; test labels below describe
        # their HKT wall time, so store the corresponding UTC value.
        "posted_at": datetime(2026, 8, day, hour, 0) - timedelta(hours=8),
        "feed_type": 1,
        "author_uid": uid,
        "author_name": name,
        "title": None,
        "content": FEED_TEXT,
        "like_count": likes,
        "comment_count": n_comments,
        "image_count": 0,
        "share_count": shares,
        "browse_count": browse,
        "raw_json_broken": shares is None,
    }


def make_sql_provider(broken_share=True, anchor=ANCHOR):
    """内存库 ＋ 一小撮数据。

    `broken_share=True` 时 f2 的 `share_count` 是 NULL —— 模拟源库 raw_json 被 TEXT 列
    截断的那 0.03% 行。它是好几条断言的起因，不是随手写的。
    """
    p = SqlProvider("sqlite://")
    create_all(p._engine)

    rows_feeds = [
        # 08-25 09:00 官号发帖，正文同时提到自家与竞品
        _feed(1, 25, 9, "u1", OFFICIAL_FULL, 10, 4, 2),
        # 08-25 09:30 → 同一个小时桶。转发数未知
        _feed(2, 25, 9, "u2", KOL_NAME, 0, 1, None if broken_share else 0, browse=888),
        # 08-25 14:00 竞品讨论区
        _feed(3, 25, 14, "u3", "路人甲", 5, 2, 0, code=PEER_CODE),
        # 08-24：基准区间那天
        _feed(4, 24, 11, "u4", "路人乙", 100, 10, 1),
    ]
    rows_comments = [
        # `author_name` 有值是为了证据卡：作者身份按主数据名单认（官号／合作 KOL／
        # 普通散户），名字为空时那一栏是 None。11 与 13 是散户，12 是官号自己回的帖。
        {"comment_id": 11, "feed_id": 1, "author_uid": "u9", "author_name": "路人丙",
         "content": COMMENT_TEXT[11], "like_count": 3},
        {"comment_id": 12, "feed_id": 1, "author_uid": "u1", "author_name": OFFICIAL_FULL,
         "content": COMMENT_TEXT[12], "like_count": 0},
        {"comment_id": 13, "feed_id": 3, "author_uid": "u9", "author_name": "路人丙",
         "content": COMMENT_TEXT[13], "like_count": 1},
        # 基准区间（08-24）里唯一的一条评论 —— 环比要能比两头，一头没有可标注的目标，
        # 「上个区间的态度」就永远是缺失态，`benchmark()` 的态度环比测不出真值。
        # 作者与 f4 的发帖人同一个、点赞 0：活跃账号数与热度都不变，前面手算的那几条
        # 计数断言一个字都不用改。
        {"comment_id": 14, "feed_id": 4, "author_uid": "u4", "author_name": "路人乙",
         "content": COMMENT_TEXT[14], "like_count": 0},
    ]
    rows_mentions = [
        {"feed_id": 1, "code": OWN_CODE, "source": "anchor", "in_pool": True},
        {"feed_id": 1, "code": PEER_CODE, "source": "body", "in_pool": True},
        # 池外标的：读路径必须滤掉，否则 KeyError 或凭空多出一只产品
        {"feed_id": 1, "code": "0700", "source": "body", "in_pool": False},
        {"feed_id": 2, "code": OWN_CODE, "source": "anchor", "in_pool": True},
        {"feed_id": 3, "code": PEER_CODE, "source": "anchor", "in_pool": True},
        {"feed_id": 4, "code": OWN_CODE, "source": "anchor", "in_pool": True},
    ]
    rows_feed_mentions = [
        {"feed_id": 1, "raw_ticker": "03033.HK", "market": "HK", "occurrences": 1},
        {"feed_id": 1, "raw_ticker": "03032.HK", "market": "HK", "occurrences": 1},
        {"feed_id": 2, "raw_ticker": "03033.HK", "market": "HK", "occurrences": 1},
        {"feed_id": 3, "raw_ticker": "03032.HK", "market": "HK", "occurrences": 1},
        {"feed_id": 4, "raw_ticker": "03033.HK", "market": "HK", "occurrences": 1},
    ]
    with p._engine.begin() as conn:
        conn.execute(feeds.insert(), rows_feeds)
        conn.execute(comments.insert(), rows_comments)
        conn.execute(mentions.insert(), rows_mentions)
        conn.execute(feed_mentions.insert(), rows_feed_mentions)
        # ``add_annotations`` defaults to this auditable current-policy run.
        # Product read paths deliberately reject untraceable or old-prompt
        # rows, so the shared fixture must model the production lineage too.
        conn.execute(
            annotation_runs.insert(),
            {
                "run_id": "run-test",
                "task": "comment_product",
                "provider": "openai_compatible",
                "model_id": "test-model",
                "prompt_version": "comment-product-v3",
                "taxonomy_version": "test-v1",
                "schema_version": "test-v1",
                "started_at": datetime(2026, 8, 25, 12, 0),
                "finished_at": datetime(2026, 8, 25, 12, 1),
                "status": "done",
                "input_count": 0,
                "success_count": 0,
                "error_count": 0,
            },
        )
        conn.execute(
            annotation_runs.insert(),
            {
                "run_id": "kol-run-test",
                "task": "kol_comment_opinion",
                "provider": "openai_compatible",
                "model_id": "test-model",
                "prompt_version": "kol-opinion-v2",
                "taxonomy_version": "test-v1",
                "schema_version": "test-v1",
                "started_at": datetime(2026, 8, 25, 12, 0),
                "finished_at": datetime(2026, 8, 25, 12, 1),
                "status": "done",
                "input_count": 0,
                "success_count": 0,
                "error_count": 0,
            },
        )
        metadata = [
            {"k": key, "v": value}
            for key, value in filter_readiness_values(p._comment_filter_config).items()
        ]
        if anchor:
            metadata.extend(
                [
                    {"k": "anchor", "v": anchor},
                    {"k": "anchor_ts", "v": f"{anchor} 23:59:59"},
                ]
            )
        conn.execute(meta_kv.insert(), metadata)

    # provider 在 __init__ 里就把 meta 读进来了（生产环境是先导库后起服务）。内存库只能
    # 由 provider 自己那个 engine 建，顺序反了，所以这里重放 __init__ 的最后两行。
    p._meta = p._read_meta()
    p._anchor = parse_anchor(p._meta.get("anchor"))
    return p


# ── 标注（ADR-0017 五张表里的两张） ────────────────────────────────────
#
# 默认值集中在这里而不是每条断言里各写一遍：断言只写**与它有关**的那几个键，读的人
# 一眼就知道这条测的是什么。`make_sql_provider()` 本身**不塞任何标注** —— 「库里一行
# 标注都没有」是接真库第一天的样子，也是绝大多数断言要的前提。
#
# 不必先造 `annotation_runs` 的父行：瘦库没有外键（radar_db/schema.py），`run_id`
# 在这里只是一个字符串标签。


def add_annotations(provider, rows):
    """往内存库里塞 `annotations` 行，返回同一个 provider（便于串写）。

    `input_hash` 默认按序号编：它和 `(target_type, target_id, subject_code, kind, run_id)`
    一起是唯一键，两行「同一单元、同一次运行」会撞约束 —— 而重跑链恰恰要写多行。
    `created_at` 默认逐行加一分钟，让「链末取最新」有个确定的先后，不靠插入顺序。
    """
    base = datetime(2026, 8, 25, 12, 0)
    payload = [
        {
            "annotation_id": r["annotation_id"],
            "target_type": r.get("target_type", "comment"),
            "target_id": r["target_id"],
            "subject_code": r.get("subject_code", OWN_CODE),
            "kind": r.get("kind", "attitude"),
            "value_json": json.dumps(r.get("value", "positive"), ensure_ascii=False),
            "calibrated_confidence": r.get("confidence"),
            "run_id": r.get(
                "run_id",
                "kol-run-test"
                if r.get("target_type", "comment") == "comment"
                and r.get("kind", "attitude") in {"kol_summary", "kol_action", "post_type"}
                else "run-test",
            ),
            "input_hash": r.get("input_hash", f"h{i}"),
            "review_state": r.get("review_state", "pending"),
            "created_at": r.get("created_at", base + timedelta(minutes=i)),
            "supersedes_id": r.get("supersedes_id"),
        }
        for i, r in enumerate(rows)
    ]
    with provider._engine.begin() as conn:
        conn.execute(annotations.insert(), payload)
    # provider 是进程单例，读过一次就把结果缓存住了。塞完数据不清缓存，后面那次查询
    # 拿到的还是「一行标注都没有」——断言会绿得毫无道理。
    provider._cache.clear()
    return provider


def add_comments(provider, rows):
    """往内存库里补评论行。

    基线只有三条评论，够所有计数断言用，但**不够测阈值**：有效态度样本的门槛是 10
    （PRD §3.5），三条评论凑不出十个判定单元。需要量的测试自己补，补出来的行只活在
    那一条测试的 provider 里 —— 加进基线会把十几条手算好的计数断言全部推倒。
    """
    payload = [
        {
            "comment_id": r["comment_id"],
            "feed_id": r.get("feed_id", 1),
            "author_uid": r.get("author_uid", "u9"),
            "author_name": r.get("author_name", "路人丙"),
            "content": r.get("content", "补一条评论。"),
            "like_count": r.get("like_count", 0),
        }
        for r in rows
    ]
    with provider._engine.begin() as conn:
        conn.execute(comments.insert(), payload)
    provider._cache.clear()
    return provider


def add_evidence(provider, rows):
    """往内存库里塞 `annotation_evidence` 行（原文引文 ＋ 字符偏移）。"""
    payload = [
        {
            "evidence_id": r["evidence_id"],
            "annotation_id": r["annotation_id"],
            "source_target_type": r.get("source_target_type", "comment"),
            "source_target_id": r["source_target_id"],
            "start_offset": r.get("start_offset"),
            "end_offset": r.get("end_offset"),
            "quote_text": r["quote_text"],
            "quote_hash": r.get("quote_hash", "x"),
        }
        for r in rows
    ]
    with provider._engine.begin() as conn:
        conn.execute(annotation_evidence.insert(), payload)
    provider._cache.clear()
    return provider
