import pytest
from sqlalchemy import select

from jobs.ingest import ingest
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