from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
    update,
)


EXAMPLES = Path(__file__).resolve().parents[1] / "upstream" / "collector_examples"
SPEC = importlib.util.spec_from_file_location(
    "verify_futu_feed_details", EXAMPLES / "verify_futu_feed_details.py"
)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def test_full_article_guard_fails_until_every_monitored_account_post_has_detail(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "source.sqlite").as_posix())
    metadata = MetaData()
    feeds = Table(
        "futu_comments_feeds",
        metadata,
        Column("feed_id", Integer, primary_key=True),
        Column("posted_at", DateTime, nullable=False),
        Column("author_uid", String(40)),
        Column("author_name", String(200)),
        Column("content_text", String(2000)),
        Column("detail_updated_at", DateTime),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(feeds), [
            {
                "feed_id": 1,
                "posted_at": datetime(2026, 10, 27, 1, 34),
                "author_uid": "11350158",
                "author_name": "丹尼斯王子",
                "content_text": "短预览...",
                "detail_updated_at": None,
            },
            {
                "feed_id": 2,
                "posted_at": datetime(2026, 10, 27, 14, 47),
                "author_uid": "30481835",
                "author_name": "牛头法师",
                "content_text": "包含全文和全部 ETF",
                "detail_updated_at": datetime(2026, 10, 28, 10, 36),
            },
            {
                "feed_id": 3,
                "posted_at": datetime(2026, 10, 27, 9),
                "author_uid": "outside-roster",
                "author_name": "普通用户",
                "content_text": "不属于 KOL 范围",
                "detail_updated_at": None,
            },
            {
                "feed_id": 4,
                "posted_at": datetime(2026, 10, 28, 8),
                "author_uid": "12917760",
                "author_name": "恒生投資管理有限公司",
                "content_text": "官号列表预览...",
                "detail_updated_at": None,
            },
        ])

    roster = EXAMPLES / "futu_radar_kol_roster.json"
    official_roster = EXAMPLES / "futu_radar_official_roster.json"
    result = guard.audit(
        engine,
        roster,
        recent_days=30,
        before=datetime(2026, 10, 29),
        official_roster=official_roster,
    )
    assert result["status"] == "incomplete"
    assert result["posts"] == 3
    assert result["incomplete"] == 2
    assert [row["feedId"] for row in result["incompletePosts"]] == [4, 1]

    with engine.begin() as connection:
        connection.execute(
            update(feeds)
            .where(feeds.c.feed_id.in_([1, 4]))
            .values(
                content_text="全文：$易方達亞太高股息ETF (03483.HK)$",
                detail_updated_at=datetime(2026, 10, 29, 1),
            )
        )
    result = guard.audit(
        engine,
        roster,
        recent_days=30,
        before=datetime(2026, 10, 30),
        official_roster=official_roster,
    )
    assert result["status"] == "ok"
    assert result["complete"] == 3
    assert result["incomplete"] == 0
