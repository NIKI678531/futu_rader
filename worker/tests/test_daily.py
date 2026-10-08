import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, insert, select
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jobs import daily, refresh  # noqa: E402
from jobs.admin_analysis import process_next  # noqa: E402
from radar_db.analysis_requests import claim_next, finish, list_requests  # noqa: E402
from radar_db.product_catalog import load_products  # noqa: E402
from radar_db.schema import meta_kv, metadata  # noqa: E402


def _engine():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(meta_kv).values(k="anchor", v="2026-08-25"))
    return engine


def _last_run(engine):
    with engine.connect() as conn:
        return json.loads(conn.execute(
            select(meta_kv.c.v).where(meta_kv.c.k == daily.LAST_RUN_KEY)
        ).scalar_one())


def test_daily_run_enqueues_only_the_short_range_completion_set_with_d7_first():
    engine = _engine()

    report = daily.run_daily(
        engine,
        sync=lambda _today: {"status": "skipped", "mode": "dump"},
        prices=lambda _engine: {"status": "skipped"},
    )

    assert [item["range"] for item in report["queued"]] == ["d7", "d1", "d2"]
    rows = list_requests(engine)
    assert {row["range"] for row in rows} == {"d1", "d2", "d7"}
    assert {row["requestedBy"] for row in rows} == {"schedule"}
    assert _last_run(engine)["anchor"] == "2026-08-25"


def test_failed_sync_still_queues_analysis_on_existing_facts():
    engine = _engine()

    def broken_sync(_today):
        raise RuntimeError("MarketInsight unreachable")

    report = daily.run_daily(engine, sync=broken_sync, prices=lambda _engine: {"status": "skipped"})

    assert report["sync"] == {"status": "failed", "error": "MarketInsight unreachable"}
    assert len(report["queued"]) == 3
    assert _last_run(engine)["sync"]["status"] == "failed"


def test_repeated_daily_run_reuses_active_requests():
    engine = _engine()
    steps = dict(sync=lambda _today: {"status": "skipped"}, prices=lambda _engine: {"status": "skipped"})

    daily.run_daily(engine, **steps)
    again = daily.run_daily(engine, **steps)

    assert {item["created"] for item in again["queued"]} == {False}
    assert len(list_requests(engine)) == 3


def test_intraday_slots_enqueue_d0_idempotently_and_coalesce_an_active_followup():
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(insert(meta_kv).values(k="data_max_ts", v="2026-10-08 15:00:00"))
    steps = {
        "sync": lambda _at: {"status": "succeeded", "mode": "incremental"},
        "prices": lambda _engine, _day: {"status": "ok"},
    }
    hkt = ZoneInfo("Asia/Hong_Kong")

    first = daily.run_intraday(
        engine,
        scheduled_at=datetime(2026, 10, 8, 13, 0, tzinfo=hkt),
        **steps,
    )
    duplicate = daily.run_intraday(
        engine,
        scheduled_at=datetime(2026, 10, 8, 13, 30, tzinfo=hkt),
        **steps,
    )
    second_slot = daily.run_intraday(
        engine,
        scheduled_at=datetime(2026, 10, 8, 15, 0, tzinfo=hkt),
        **steps,
    )

    assert first["queued"][0]["created"] is True
    assert duplicate["analysis"]["status"] == "deduplicated"
    assert second_slot["analysis"] == {
        "status": "followup_queued",
        "followupRequested": True,
    }
    assert len(list_requests(engine)) == 1

    seen = []
    runner = lambda range_key: seen.append(range_key) or {"status": "complete", "errors": 0}
    assert process_next(engine=engine, owner="slot-13", runner=runner)["status"] == "succeeded"
    assert process_next(engine=engine, owner="slot-15", runner=runner)["status"] == "succeeded"
    assert seen == ["d0", "d0"]
    assert len(list_requests(engine)) == 2


def test_intraday_sync_uses_a_stable_hourly_id_and_incremental_keyset_path(monkeypatch):
    monkeypatch.setenv("MARKET_INSIGHT_DATABASE_URL", "mysql+pymysql://source/market_insight")
    captured = {}
    service = type("Service", (), {"source": object()})()
    monkeypatch.setattr(refresh, "_refresh", lambda: service)
    monkeypatch.setattr(
        refresh,
        "sync_once",
        lambda **kwargs: captured.update(kwargs) or {
            "status": "succeeded",
            "complete_through": "2026-10-07",
            "rows_read": 3,
            "inserted": 2,
            "updated": 1,
        },
    )

    result = daily.intraday_sync_step(
        datetime(2026, 10, 8, 13, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    )

    assert captured == {
        "mode": "incremental",
        "run_id": "intraday-20261008-13",
        "service": service,
    }
    assert result["mode"] == "incremental"
    assert result["rowsRead"] == 3


def test_dump_mode_skips_sync_without_a_source_url(monkeypatch):
    monkeypatch.delenv("MARKET_INSIGHT_DATABASE_URL", raising=False)
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.setattr(refresh, "_refresh", lambda **_kw: (_ for _ in ()).throw(AssertionError("no source")))

    engine = _engine()
    report = daily.run_daily(engine)

    assert report["sync"]["status"] == "skipped" and report["sync"]["mode"] == "dump"
    assert report["prices"]["status"] == "skipped"


def test_price_step_wires_yfinance_as_the_fmp_identity_fallback(monkeypatch):
    from jobs import sync_prices
    from market_data import fmp, yahoo

    monkeypatch.setenv("FMP_API_KEY", "test-key")
    primary, fallback, captured = object(), object(), {}
    monkeypatch.setattr(fmp, "FmpClient", lambda: primary)
    monkeypatch.setattr(yahoo, "YahooFinanceClient", lambda: fallback)

    def fake_sync(engine, client, codes, start, end, **kwargs):
        captured.update(
            engine=engine,
            client=client,
            codes=codes,
            start=start,
            end=end,
            kwargs=kwargs,
        )
        return []

    monkeypatch.setattr(sync_prices, "sync", fake_sync)
    engine = _engine()
    report = daily.price_step(engine)

    assert report["status"] == "ok"
    assert captured["engine"] is engine
    assert captured["client"] is primary
    assert captured["kwargs"]["fallback_client"] is fallback
    assert captured["end"].isoformat() == "2026-08-25"
    assert captured["start"].isoformat() == "2026-06-27"
    expected = load_products(engine=engine)
    assert captured["codes"] == [product["code"] for product in expected]
    assert {product["ownership"] for product in expected} == {"own", "peer"}


def test_production_sync_goes_through_the_latest_closing_receipt(monkeypatch):
    from datetime import date

    monkeypatch.setenv("MARKET_INSIGHT_DATABASE_URL", "mysql+pymysql://source/market_insight")
    built, synced = [], {}

    class Service:
        source = object()

    monkeypatch.setattr(refresh, "_refresh", lambda **kw: built.append(kw) or Service())
    monkeypatch.setattr(
        refresh, "latest_closing_run",
        lambda _source, day: {"run_id": f"comments_all:{day.isoformat()}"},
    )

    def sync_once(**kwargs):
        synced.update(kwargs)
        return {"status": "succeeded", "complete_through": "2026-10-05", "inserted": 3, "updated": 1}

    monkeypatch.setattr(refresh, "sync_once", sync_once)

    result = daily.sync_step(date(2026, 10, 6))

    assert built == [{}, {"through_source_run_id": "comments_all:2026-10-06"}]
    assert synced["through_source_run_id"] == "comments_all:2026-10-06"
    assert synced["run_id"].startswith("daily-20261006-")
    assert result["status"] == "succeeded" and result["completeThrough"] == "2026-10-05"


def test_latest_closing_run_falls_back_to_the_previous_day():
    from datetime import date

    class Source:
        def closing_run(self, day):
            return {"run_id": "yesterday"} if day == date(2026, 10, 5) else None

    assert refresh.latest_closing_run(Source(), date(2026, 10, 6)) == {"run_id": "yesterday"}
    assert refresh.latest_closing_run(object(), date(2026, 10, 6)) is None


def test_drain_executes_until_the_queue_is_empty():
    engine = _engine()
    daily.run_daily(engine, ranges=("d1", "d2"),
                    sync=lambda _today: {"status": "skipped"}, prices=lambda _engine: {"status": "skipped"})

    def process(*, engine):
        request = claim_next(engine, owner="pod")
        if request is None:
            return None
        finish(engine, request["id"], "pod", "succeeded", summary={"status": "complete"})
        return {**request, "status": "succeeded"}

    results = daily.drain(engine, process=process, sleep=lambda _seconds: None)

    assert [row["range"] for row in results] == ["d1", "d2"]
    assert {row["status"] for row in list_requests(engine)} == {"succeeded"}
