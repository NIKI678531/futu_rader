import json
from types import SimpleNamespace

import pytest

from jobs import refresh


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
