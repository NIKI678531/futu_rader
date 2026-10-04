import json
from pathlib import Path
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


def test_calibration_report_accepts_inline_secret_json(monkeypatch):
    report = {"policy": {"model": "test"}, "batchGatePassed": True}
    monkeypatch.setenv("AI_CALIBRATION_REPORT", json.dumps(report))

    assert refresh._calibration_report(None) == report


def test_calibration_report_keeps_explicit_or_secret_path(monkeypatch):
    explicit = Path("explicit.json")
    monkeypatch.setenv("AI_CALIBRATION_REPORT", "secret.json")

    assert refresh._calibration_report(explicit) == explicit
    assert refresh._calibration_report(None) == Path("secret.json")


def test_quality_report_accepts_inline_secret_json_or_path(monkeypatch):
    report = {"reportType": "comment-relevance-human-gold-v1"}
    monkeypatch.setenv("AI_QUALITY_REPORT", json.dumps(report))
    assert refresh._quality_report(None) == report

    explicit = Path("gold-v3.json")
    assert refresh._quality_report(explicit) == explicit


@pytest.mark.parametrize("value", ["1", "yes", "approved", "false-ish"])
def test_governance_approval_rejects_ambiguous_secret_values(monkeypatch, value):
    monkeypatch.setenv("AI_DATA_GOVERNANCE_APPROVED", value)

    with pytest.raises(ValueError, match="must be exactly true or false"):
        refresh._data_governance_approved()


@pytest.mark.parametrize("value", [None, "", "false", " FALSE "])
def test_governance_approval_defaults_to_false(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("AI_DATA_GOVERNANCE_APPROVED", raising=False)
    else:
        monkeypatch.setenv("AI_DATA_GOVERNANCE_APPROVED", value)

    assert refresh._data_governance_approved() is False


@pytest.mark.parametrize("value", [None, "false"])
def test_analyze_cli_fails_closed_before_calibration_or_runtime_setup(
    monkeypatch, capsys, value
):
    if value is None:
        monkeypatch.delenv("AI_DATA_GOVERNANCE_APPROVED", raising=False)
    else:
        monkeypatch.setenv("AI_DATA_GOVERNANCE_APPROVED", value)
    monkeypatch.setattr(
        refresh,
        "_calibration_report",
        lambda *_args: pytest.fail("calibration report must not load before approval"),
    )
    monkeypatch.setattr(
        refresh,
        "_refresh",
        lambda **_kwargs: pytest.fail("runtime must not initialize before approval"),
    )

    with pytest.raises(SystemExit) as raised:
        refresh.main(["analyze", "--budget-date", "2026-09-25"])

    assert raised.value.code == 2
    assert "AI_DATA_GOVERNANCE_APPROVED=true" in capsys.readouterr().err


def test_analyze_cli_injects_explicit_governance_approval(monkeypatch, capsys):
    captured = {}

    class FakeRefresh:
        def analyze(self, request):
            captured["request"] = request
            return SimpleNamespace(status="complete", as_dict=lambda: {"status": "complete"})

    monkeypatch.setenv("AI_DATA_GOVERNANCE_APPROVED", "true")
    monkeypatch.setenv("AI_CALIBRATION_REPORT", json.dumps({"batchGatePassed": True}))
    monkeypatch.setenv("AI_QUALITY_REPORT", json.dumps({"reportType": "comment-relevance-human-gold-v1"}))
    monkeypatch.setattr(refresh, "_refresh", lambda **_kwargs: FakeRefresh())

    assert refresh.main(["analyze", "--budget-date", "2026-09-25"]) == 0
    assert captured["request"].data_governance_approved is True
    assert captured["request"].quality_report == {"reportType": "comment-relevance-human-gold-v1"}
    assert json.loads(capsys.readouterr().out) == {"status": "complete"}


def test_analyze_cli_accepts_staged_codes_and_ranges(monkeypatch, capsys):
    captured = {}

    class FakeRefresh:
        def analyze(self, request):
            captured["request"] = request
            return SimpleNamespace(status="complete", as_dict=lambda: {"status": "complete"})

    monkeypatch.setenv("AI_DATA_GOVERNANCE_APPROVED", "true")
    monkeypatch.setenv("AI_CALIBRATION_REPORT", json.dumps({"batchGatePassed": True}))
    monkeypatch.setenv(
        "AI_QUALITY_REPORT",
        json.dumps({"reportType": "comment-relevance-human-gold-v1"}),
    )
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
