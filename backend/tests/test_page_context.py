"""Actual page comment clocks stay separate from feed/publication times."""

from datetime import datetime

from sqlalchemy import update
from radar_db.schema import comments, meta_kv


def test_official_clock_tracks_the_comments_used_by_current_page_content(sql_provider):
    with sql_provider._engine.begin() as conn:
        conn.execute(
            update(comments)
            .where(comments.c.comment_id == 11)
            .values(posted_at=datetime(2026, 8, 25, 3, 45))
        )
        conn.execute(meta_kv.insert(), [
            {"k": "data_revision", "v": "live-source-revision-1"},
            {"k": "ai_public_revision", "v": "published-1"},
            {"k": "ai_public_updated_at", "v": "2026-08-25T08:00:00Z"},
        ])
    sql_provider.refresh()

    data = sql_provider.page_context("official", "d1")

    assert data["commentClock"] == {
        "status": "partial",
        "postedThrough": "2026-08-25T11:45+08:00",
        "timestamped": 1,
        "stored": 2,
    }
    assert data["pagePublishedAt"] == "2026-08-25T16:00+08:00"
    assert data["latestFeedPostedAt"] != data["commentClock"]["postedThrough"]

    with sql_provider._engine.begin() as conn:
        conn.execute(
            update(comments)
            .where(comments.c.comment_id == 11)
            .values(posted_at=datetime(2026, 8, 25, 4, 30))
        )
        conn.execute(
            update(meta_kv)
            .where(meta_kv.c.k == "data_revision")
            .values(v="new-live-source-revision")
        )
    sql_provider.refresh()

    refreshed = sql_provider.page_context("official", "d1")
    assert refreshed["commentClock"]["postedThrough"] == "2026-08-25T12:30+08:00"
    assert refreshed["commentClock"]["postedThrough"] != data["commentClock"]["postedThrough"]


def test_page_context_http_rejects_unknown_screen(sql_client):
    response = sql_client.get("/api/v1/page-context?screen=unknown&range=d1")

    assert response.status_code == 404


def test_kol_clock_reports_a_completed_empty_scope(sql_client):
    response = sql_client.get("/api/v1/page-context?screen=kol&range=d1")

    assert response.status_code == 200
    assert response.get_json()["data"]["commentClock"]["status"] == "empty"
