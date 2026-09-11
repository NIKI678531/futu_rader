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
from datetime import datetime
from pathlib import Path

from core.calendar import parse_anchor

# 先导入 provider：它会把仓库根塞进 sys.path，下面的 radar_db 才 import 得到
# （见 providers/sql.py 顶部的 sys.path 守卫）。
from providers.sql import SqlProvider
from radar_db import create_all
from radar_db.schema import comments, feeds, mentions, meta_kv

ANCHOR = "2026-08-25"  # 真实数据的锚点：最近一个完整自然日
MASTER = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "demo" / "master.json").read_text(
        encoding="utf-8"
    )
)
OWN_CODE = "3033"  # 恒生科技指數ETF，CSOP 南方东英
PEER_CODE = "3032"  # 同名竞品，恒生投资
OFFICIAL_FULL = "恒生投資管理有限公司"  # 有 comps ⇒ 发行商官号
OFFICIAL_SHORT = "恒生投资"
KOL_NAME = "孫子的末代傳人"  # master.json 里 active 的 KOL


def _feed(feed_id, day, hour, uid, name, likes, n_comments, shares, browse=None):
    return {
        "feed_id": feed_id,
        "code": OWN_CODE,  # 冗余列，读路径一律走 mentions，这里只是不能为空
        "posted_at": datetime(2026, 8, day, hour, 0),
        "feed_type": 1,
        "author_uid": uid,
        "author_name": name,
        "title": None,
        "content": "正文",
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
        _feed(3, 25, 14, "u3", "路人甲", 5, 2, 0),
        # 08-24：基准区间那天
        _feed(4, 24, 11, "u4", "路人乙", 100, 10, 1),
    ]
    rows_comments = [
        {"comment_id": 11, "feed_id": 1, "author_uid": "u9", "like_count": 3},
        {"comment_id": 12, "feed_id": 1, "author_uid": "u1", "like_count": 0},
        {"comment_id": 13, "feed_id": 3, "author_uid": "u9", "like_count": 1},
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
    with p._engine.begin() as conn:
        conn.execute(feeds.insert(), rows_feeds)
        conn.execute(comments.insert(), rows_comments)
        conn.execute(mentions.insert(), rows_mentions)
        if anchor:
            conn.execute(
                meta_kv.insert(),
                [{"k": "anchor", "v": anchor}, {"k": "anchor_ts", "v": f"{anchor} 23:59:59"}],
            )

    # provider 在 __init__ 里就把 meta 读进来了（生产环境是先导库后起服务）。内存库只能
    # 由 provider 自己那个 engine 建，顺序反了，所以这里重放 __init__ 的最后两行。
    p._meta = p._read_meta()
    p._anchor = parse_anchor(p._meta.get("anchor"))
    return p
