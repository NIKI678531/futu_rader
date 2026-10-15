import json
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from jobs import refresh
from radar_db import make_engine
from radar_db.source_db import _parse_url, make_source_engine, require_separate_databases


def test_local_worker_normalizes_legacy_mysql_driver_to_installed_pymysql():
    parsed = _parse_url(
        "mysql+mysqldb://reader:p%24ss@source/market_insight?charset=utf8mb4"
    )

    assert parsed.drivername == "mysql+pymysql"
    assert parsed.password == "p$ss"


def test_source_connection_is_read_only_and_does_not_create_files(tmp_path, monkeypatch):
    monkeypatch.delenv("RADAR_DB_URL", raising=False)
    path = tmp_path / "source.sqlite"
    url = "sqlite:///" + path.as_posix()
    setup = create_engine(url)
    with setup.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE facts (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("INSERT INTO facts VALUES (1)")
    setup.dispose()

    source = make_source_engine(url)
    with source.connect() as conn:
        assert conn.exec_driver_sql("SELECT id FROM facts").scalar_one() == 1
        for statement in ("INSERT INTO facts VALUES (2)", "CREATE TABLE unsafe (id INTEGER)"):
            with pytest.raises(ValueError, match="only allow read queries"):
                conn.exec_driver_sql(statement)
    raw = source.raw_connection()
    try:
        cursor = raw.cursor()
        try:
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                cursor.execute("INSERT INTO facts VALUES (2)")
        finally:
            cursor.close()
    finally:
        raw.close()
    source.dispose()

    missing = tmp_path / "missing.sqlite"
    with pytest.raises(ValueError, match="does not exist"):
        make_source_engine("sqlite:///" + missing.as_posix())
    assert not missing.exists()


def test_application_engine_rejects_source_database_before_connecting(monkeypatch):
    monkeypatch.setenv("MARKET_INSIGHT_DATABASE_URL", "mysql+pymysql://reader:secret@db/source")
    with pytest.raises(ValueError, match="separate databases"):
        make_engine("mysql+pymysql://writer:other@DB:3306/source")
    require_separate_databases("mysql+pymysql://writer:other@db/application")


def test_source_connection_also_rejects_application_database(monkeypatch):
    monkeypatch.setenv("RADAR_DB_URL", "mysql+pymysql://writer:secret@db/source")
    with pytest.raises(ValueError, match="separate databases"):
        make_source_engine("mysql+pymysql://reader:other@DB:3306/source")


def test_source_connection_requires_configuration_and_masks_invalid_url(monkeypatch):
    monkeypatch.delenv("MARKET_INSIGHT_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="Missing MARKET_INSIGHT_DATABASE_URL"):
        make_source_engine()
    with pytest.raises(ValueError) as raised:
        make_source_engine("secret-invalid-url")
    assert "secret-invalid-url" not in str(raised.value)


def test_source_check_never_initializes_application_or_ai(monkeypatch, capsys):
    disposed = []
    source = SimpleNamespace(dispose=lambda: disposed.append(True))
    monkeypatch.setattr(refresh, "_source_engine", lambda: source)
    monkeypatch.setattr(refresh, "make_engine", lambda: pytest.fail("application DB must not be opened"))
    monkeypatch.setattr(refresh, "_refresh", lambda **kwargs: pytest.fail("AI must not initialize"))

    def inspect_only_source(engine, codes, sample_size):
        assert engine is source
        assert codes == ("3033", "2802")
        assert sample_size == 3
        return {"status": "sample_checked", "productionReady": False}

    monkeypatch.setattr(refresh, "inspect_source", inspect_only_source)
    assert refresh.main(["check-source", "--codes", "3033,2802", "--sample-size", "3"]) == 0
    assert disposed == [True]
    assert json.loads(capsys.readouterr().out)["productionReady"] is False


def test_source_check_masks_driver_errors_and_closes_connection(monkeypatch, capsys):
    disposed = []
    source = SimpleNamespace(dispose=lambda: disposed.append(True))
    monkeypatch.setattr(refresh, "_source_engine", lambda: source)

    def fail_inspection(*args, **kwargs):
        raise RuntimeError("driver failed with password=private-secret and private-body")

    monkeypatch.setattr(refresh, "inspect_source", fail_inspection)
    with pytest.raises(SystemExit) as raised:
        refresh.main(["check-source", "--codes", "3033"])
    assert raised.value.code == 2
    assert disposed == [True]
    error = capsys.readouterr().err
    assert "Source inspection failed" in error
    assert "private-secret" not in error
    assert "private-body" not in error


def test_source_check_reports_missing_tables_without_creating_them(tmp_path, monkeypatch):
    monkeypatch.delenv("RADAR_DB_URL", raising=False)
    path = tmp_path / "source.sqlite"
    path.touch()
    source = make_source_engine("sqlite:///" + path.as_posix())
    try:
        report = refresh.inspect_source(source, ("3033",), sample_size=1)
        assert report["status"] == "blocked"
        assert report["productionReady"] is False
        assert {issue["table"] for issue in report["issues"]} == {
            "futu_comments_stocks", "futu_comments_feeds", "futu_comments_users",
        }
        with source.connect() as conn:
            assert conn.exec_driver_sql("SELECT count(*) FROM sqlite_master").scalar_one() == 0
        with pytest.raises(ValueError, match="sample_size"):
            refresh.inspect_source(source, ("3033",), sample_size=101)
    finally:
        source.dispose()


def test_refresh_wires_only_market_insight_database_source(monkeypatch):
    captured = {}

    class FakeSource:
        source_id = "market_insight"
        pool_codes = {"3037"}

    def build_source(engine, pool_codes):
        captured.update(
            engine=engine,
            pool_codes=pool_codes,
        )
        return FakeSource()

    monkeypatch.setattr(refresh, "make_engine", lambda: "target-engine")
    monkeypatch.setattr(refresh, "_source_engine", lambda: "source-engine")
    monkeypatch.setattr(refresh, "pool_codes", lambda: ["3037"])
    monkeypatch.setattr(refresh, "MarketInsightMySqlAdapter", build_source)

    worker = refresh._refresh()

    assert worker.source.source_id == "market_insight"
    assert captured == {
        "engine": "source-engine",
        "pool_codes": {"3037"},
    }


@pytest.mark.parametrize("value", ["1", "yes", "approved", "false-ish"])
def test_governance_approval_rejects_ambiguous_secret_values(monkeypatch, value):
    monkeypatch.setenv("AI_DATA_GOVERNANCE_APPROVED", value)

    with pytest.raises(ValueError, match="must be exactly true or false"):
        refresh._data_governance_approved()


@pytest.mark.parametrize("value", [None, "", "true", " TRUE "])
def test_governance_switch_defaults_to_on(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("AI_DATA_GOVERNANCE_APPROVED", raising=False)
    else:
        monkeypatch.setenv("AI_DATA_GOVERNANCE_APPROVED", value)

    assert refresh._data_governance_approved() is True


def test_governance_switch_explicit_false_turns_ai_off(monkeypatch):
    monkeypatch.setenv("AI_DATA_GOVERNANCE_APPROVED", " FALSE ")

    assert refresh._data_governance_approved() is False


def test_analyze_cli_stops_before_runtime_setup_when_switched_off(monkeypatch, capsys):
    monkeypatch.setenv("AI_DATA_GOVERNANCE_APPROVED", "false")
    monkeypatch.setattr(
        refresh,
        "_refresh",
        lambda **_kwargs: pytest.fail("runtime must not initialize when AI is switched off"),
    )

    with pytest.raises(SystemExit) as raised:
        refresh.main(["analyze", "--budget-date", "2026-09-25"])

    assert raised.value.code == 2
    assert "AI_DATA_GOVERNANCE_APPROVED" in capsys.readouterr().err


def test_analyze_cli_runs_without_release_reports(monkeypatch, capsys):
    captured = {}

    class FakeRefresh:
        def analyze(self, request):
            captured["request"] = request
            return SimpleNamespace(status="complete", as_dict=lambda: {"status": "complete"})

    monkeypatch.delenv("AI_DATA_GOVERNANCE_APPROVED", raising=False)
    monkeypatch.setattr(refresh, "_refresh", lambda **_kwargs: FakeRefresh())

    assert refresh.main(["analyze", "--budget-date", "2026-09-25"]) == 0
    assert captured["request"].data_governance_approved is True
    assert captured["request"].max_http_attempts == 100000
    assert json.loads(capsys.readouterr().out) == {"status": "complete"}


def test_analyze_cli_accepts_staged_codes_and_ranges(monkeypatch, capsys):
    captured = {}

    class FakeRefresh:
        def analyze(self, request):
            captured["request"] = request
            return SimpleNamespace(status="complete", as_dict=lambda: {"status": "complete"})

    monkeypatch.setenv("AI_DATA_GOVERNANCE_APPROVED", "true")
    monkeypatch.setattr(refresh, "_refresh", lambda **_kwargs: FakeRefresh())

    assert refresh.main([
        "analyze", "--budget-date", "2026-09-25",
        "--codes", "3037,3042,3046,3066,3068", "--ranges", "d7,d14",
    ]) == 0
    assert captured["request"].codes == ("3037", "3042", "3046", "3066", "3068")
    assert captured["request"].ranges == ("d7", "d14")
    assert json.loads(capsys.readouterr().out) == {"status": "complete"}


def test_sync_cli_accepts_repeatable_feed_ids_for_targeted_database_reread(
    monkeypatch, capsys
):
    captured = {}

    class FakeRefresh:
        def sync(self, request):
            captured["request"] = request
            return SimpleNamespace(status="succeeded", as_dict=lambda: {"status": "succeeded"})

    monkeypatch.setattr(refresh, "_refresh", lambda **_kwargs: FakeRefresh())

    assert refresh.main([
        "sync", "--run-id", "repair-3037", "--feed-id", "117", "--feed-id", "118",
    ]) == 0
    assert captured["request"].feed_ids == (117, 118)
    assert json.loads(capsys.readouterr().out) == {"status": "succeeded"}
