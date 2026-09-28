import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobs import refresh


def test_calibration_report_accepts_inline_secret_json(monkeypatch):
    report = {"policy": {"model": "test"}, "batchGatePassed": True}
    monkeypatch.setenv("AI_CALIBRATION_REPORT", json.dumps(report))

    assert refresh._calibration_report(None) == report


def test_calibration_report_keeps_explicit_or_secret_path(monkeypatch):
    explicit = Path("explicit.json")
    monkeypatch.setenv("AI_CALIBRATION_REPORT", "secret.json")

    assert refresh._calibration_report(explicit) == explicit
    assert refresh._calibration_report(None) == Path("secret.json")


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
    monkeypatch.setattr(refresh, "_refresh", lambda **_kwargs: FakeRefresh())

    assert refresh.main(["analyze", "--budget-date", "2026-09-25"]) == 0
    assert captured["request"].data_governance_approved is True
    assert json.loads(capsys.readouterr().out) == {"status": "complete"}
