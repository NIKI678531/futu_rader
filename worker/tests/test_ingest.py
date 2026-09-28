import json

import pytest
from datetime import datetime, timezone
from sqlalchemy import delete, select, update

from jobs.ingest import ingest
from collection.normalization import normalize_feed
from collection.models import SyncRequest
from collection.service import FutuRefresh
from collection.source import MemorySourceAdapter
from radar_db import create_all, make_engine
from radar_db.schema import feeds, meta_kv


def test_sources_share_identity_and_update_without_rebuild(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "facts.sqlite").as_posix())
    create_all(engine)
    row = {"feed_id": 1, "code": "3033", "posted_at": "2026-08-01T10:00:00", "observed_at": "2026-08-02T10:00:00",
           "feed_type": 1, "content": "ETF", "like_count": 1, "comment_count": 0, "image_count": 0}
    assert ingest(engine, [row], "export-a", ["3033"])["updated"] == 1
    assert ingest(engine, [row], "export-b", ["3033"])["unchanged"] == 1
    with engine.connect() as conn:
        previous = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "data_revision")).scalar()
    assert ingest(engine, [{**row, "share_count": 0}], "export-b", ["3033"])["updated"] == 1
    with engine.connect() as conn:
        assert conn.execute(select(feeds.c.share_count)).scalar_one() == 0
        assert conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "data_revision")).scalar() != previous
    with pytest.raises(ValueError, match="same observation"):
        ingest(engine, [{**row, "observed_at": "2026-09-01T10:00:00"}], "export-b", ["3033"])


def test_aware_timestamps_are_stored_as_utc_naive(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "facts-utc.sqlite").as_posix())
    create_all(engine)
    row = {
        "feed_id": 2, "code": "3033", "posted_at": "2026-08-01T18:00:00+08:00",
        "observed_at": "2026-08-02T18:00:00+08:00", "feed_type": 1,
        "content": "ETF", "like_count": 1, "comment_count": 0, "image_count": 0,
    }
    ingest(engine, [row], "export-a", ["3033"])
    with engine.connect() as conn:
        stored = conn.execute(select(feeds.c.posted_at, feeds.c.source_observed_at)).one()
    assert stored == (datetime(2026, 8, 1, 10, 0), datetime(2026, 8, 2, 10, 0))


def test_jsonl_recovery_advances_semantic_revision_but_counter_only_change_does_not(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "recovery-revision.sqlite").as_posix())
    create_all(engine)
    observed = datetime(2026, 8, 2, 10, 0)
    source_row = {
        "feed_id": 1, "stock_id": 1, "feed_type": 1,
        "posted_at": datetime(2026, 8, 1, 10, 0), "author_uid": "u1",
        "author_name": "作者", "feed_title": "标题", "content_text": "原正文",
        "like_count": 1, "comment_count": 0, "image_count": 0,
        "raw_json": json.dumps({"common": {}, "comment": {"comment_items": []}}),
    }
    online = normalize_feed(source_row, "3033", observed)
    FutuRefresh(engine, MemorySourceAdapter({"feeds": [online]})).sync(
        SyncRequest(run_id="online")
    )
    with engine.begin() as conn:
        # Simulate a database migrated from before semantic revisions existed.
        conn.execute(delete(meta_kv).where(meta_kv.c.k == "ai_input_revision"))
        conn.execute(update(meta_kv).where(meta_kv.c.k == "data_revision").values(v="legacy-revision"))
        original_ai_revision = "legacy-revision"
        conn.execute(update(meta_kv).where(meta_kv.c.k.like("synth_dirty_%")).values(v="0"))

    recovery = {
        "feed_id": 1, "code": "3033", "posted_at": "2026-08-01T10:00:00",
        "observed_at": "2026-08-02T10:00:00", "feed_type": 1,
        "title": "标题", "content": "原正文", "like_count": 2,
        "comment_count": 0, "image_count": 0,
    }
    ingest(engine, [recovery], "recovery", ["3033"])
    with engine.connect() as conn:
        assert conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == "ai_input_revision"
        )).scalar_one() == original_ai_revision
        assert conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == "synth_dirty_3033_d1"
        )).scalar_one() == "0"

    ingest(engine, [{**recovery, "content": "修复后的正文"}], "recovery", ["3033"])
    with engine.connect() as conn:
        assert conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == "ai_input_revision"
        )).scalar_one() != original_ai_revision
        assert conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == "synth_dirty_3033_d1"
        )).scalar_one() == "1"
