import json
from datetime import datetime

import pytest
from sqlalchemy import insert

from ai import config
from jobs import analyze
from radar_db import create_all, make_engine
from radar_db.schema import meta_kv


@pytest.fixture
def engine(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "plan.db").as_posix())
    create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(meta_kv), [{"k": "anchor", "v": "2026-08-25"},
                                      {"k": "window_from", "v": "2026-04-28"}])
    return engine


def test_plan_uses_complete_source_day_and_deduplicates_ranges(engine):
    args = analyze.parser().parse_args(["plan", "--ranges", "d2,d1,d1", "--codes", "3033"])
    plan = analyze.make_plan(engine, args)
    assert plan["anchor"] == "2026-08-25"
    assert plan["from"] == "2026-08-22"
    assert plan["priorityDays"] == ["2026-08-25", "2026-08-24", "2026-08-23", "2026-08-22"]
    assert plan["ranges"] == ["d1", "d2"]
    assert plan["modelRequestsMade"] == 0


def test_future_source_dates_are_refused(engine):
    args = analyze.parser().parse_args(["plan", "--anchor", "2026-09-16"])
    with pytest.raises(ValueError, match="complete only"):
        analyze.make_plan(engine, args)


def test_filters_intersect_and_unknown_values_fail():
    assert analyze.selected_products(analyze.parser().parse_args([
        "plan", "--codes", "3033", "--ownership", "own", "--sector", "hk"]))[0]["code"] == "3033"
    with pytest.raises(ValueError, match="empty intersection"):
        analyze.selected_products(analyze.parser().parse_args(["plan", "--codes", "3033", "--sector", "us"]))
    with pytest.raises(ValueError, match="Invalid"):
        analyze.selected_products(analyze.parser().parse_args(["plan", "--sector", "unknown"]))


def test_plan_command_never_builds_provider_or_writes_jobs(engine, monkeypatch, capsys):
    monkeypatch.setattr(analyze, "make_engine", lambda: engine)
    monkeypatch.setattr(analyze.config, "load", lambda **kwargs: config.AiConfig(
        provider="openai_compatible", base_url="", api_key="", model="test", timeout_seconds=60,
        max_retries=2, micro_batch_size=5, max_input_tokens=8000, concurrency=2,
        prompt_version="comment-product-v2", taxonomy_version="v2", schema_version="v2",
        structured_output=True, reasoning_effort="low", store=False))
    monkeypatch.setattr(analyze, "build_provider", lambda *args, **kwargs: pytest.fail("Unexpected AI provider"))
    assert analyze.main(["plan", "--codes", "3033"]) == 0
    assert json.loads(capsys.readouterr().out)["commentPlan"]["plannedCommentBatches"] == 0


def test_run_requires_budget_before_accessing_database(monkeypatch):
    monkeypatch.setattr(analyze, "make_engine", lambda: pytest.fail("Should reject before DB access"))
    with pytest.raises(SystemExit):
        analyze.main(["run"])


def test_batch_run_requires_matching_passed_calibration():
    with pytest.raises(ValueError, match="calibration-report"):
        analyze.check_calibration(config.load(micro_batch_size=5), None)