from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, select

from radar_db.anchor_clock import publish_system_anchor
from radar_db.schema import meta_kv, metadata


def test_system_anchor_publishes_the_current_hkt_date(monkeypatch):
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    monkeypatch.setenv("RADAR_ANCHOR_MODE", "system")

    result = publish_system_anchor(
        engine,
        at=datetime(2026, 11, 12, 21, 45, tzinfo=ZoneInfo("Asia/Hong_Kong")),
    )

    with engine.connect() as conn:
        values = dict(conn.execute(select(meta_kv.c.k, meta_kv.c.v)).all())
    assert result.isoformat() == "2026-11-12"
    assert values["anchor"] == "2026-11-12"
    assert values["anchor_ts"] == "2026-11-12 23:59:59"


def test_data_anchor_mode_does_not_rewrite_metadata(monkeypatch):
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    monkeypatch.setenv("RADAR_ANCHOR_MODE", "data")

    assert publish_system_anchor(engine) is None
    with engine.connect() as conn:
        assert conn.execute(select(meta_kv.c.k)).all() == []
