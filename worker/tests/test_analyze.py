import json
from datetime import datetime

import pytest
from sqlalchemy import insert

from ai import config
from ai import release_regressions
from jobs import analyze
from scripts import calibrate
from radar_db import create_all, make_engine
from radar_db.comment_routes import COMMENT_ROUTE_VERSION, product_pool_digest
from radar_db.schema import comments, feeds, mentions, meta_kv


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


def test_plan_candidate_count_uses_hkt_bounds_for_utc_naive_feeds(engine):
    timestamps = (
        datetime(2026, 8, 23, 15, 59, 59),
        datetime(2026, 8, 23, 16, 0, 0),
        datetime(2026, 8, 25, 15, 59, 59),
        datetime(2026, 8, 25, 16, 0, 0),
    )
    with engine.begin() as conn:
        conn.execute(insert(feeds), [
            {
                "feed_id": i,
                "code": "3033",
                "posted_at": posted_at,
                "feed_type": 1,
                "like_count": 0,
                "comment_count": 1,
                "image_count": 0,
                "raw_json_broken": False,
            }
            for i, posted_at in enumerate(timestamps, 1)
        ])
        conn.execute(insert(comments), [
            {"comment_id": i, "feed_id": i, "content": "ETF", "author_uid": f"u{i}"}
            for i in range(1, 5)
        ])

    args = analyze.parser().parse_args(["plan", "--ranges", "d1", "--codes", "3033"])
    plan = analyze.make_plan(engine, args)

    # d1 includes its 08-24 baseline when estimating candidates: HKT [08-24, 08-26).
    assert plan["candidateComments"] == 2


def test_future_source_dates_are_refused(engine):
    args = analyze.parser().parse_args(["plan", "--anchor", "2026-09-16"])
    with pytest.raises(ValueError, match="complete only"):
        analyze.make_plan(engine, args)


def test_preview_uses_exact_routes_and_counts_the_actual_subject(engine):
    with engine.begin() as conn:
        conn.execute(insert(feeds).values(
            feed_id=77, code="3037", source_ticker="03037.HK",
            posted_at=datetime(2026, 8, 25, 3),
            feed_type=1, title="恒指讨论", content="市场讨论",
            like_count=0, comment_count=2, image_count=0, raw_json_broken=False,
        ))
        conn.execute(insert(mentions).values(
            feed_id=77, code="3037", source="anchor", in_pool=True,
        ))
        conn.execute(insert(comments), [
            {"comment_id": 7701, "feed_id": 77, "content": "恒指和 HSI 今天走弱", "author_uid": "u1"},
            {"comment_id": 7702, "feed_id": 77, "content": "$盈富基金 (02800.HK)$ 费率更低", "author_uid": "u2"},
        ])

    args = analyze.parser().parse_args(["plan", "--ranges", "d1", "--codes", "3037"])
    plan = analyze.make_plan(engine, args)
    cfg = config.load(
        _allow_missing_key=True, prompt_version="comment-product-v3",
        schema_version="v2", taxonomy_version="v2",
    )
    result = analyze.preview(engine, cfg, plan, 100)

    # 3037 uses exclude mode and neither reply can rescue or reroute its parent;
    # both remain anchored to 3037 because the parent contains no excluded tag.
    assert result["commentPlan"]["filtered"] == 0
    assert result["commentPlan"]["newOrPending"] == 2
    assert result["commentPlan"]["plannedCommentBatches"] == 2


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
    with pytest.raises(ValueError, match="calibration-report"):
        analyze.check_calibration(
            config.load(micro_batch_size=1), None, require_singleton=True
        )


def test_calibration_accepts_inline_secret_json():
    cfg = config.load(micro_batch_size=5)
    analyze.check_calibration(cfg, {
        "policy": calibrate.calibration_policy(
            cfg,
            requested_batch_size=5,
            effective_batch_size=5,
            max_input_tokens=cfg.max_input_tokens,
            max_payload_bytes=cfg.max_payload_bytes,
            max_output_tokens=cfg.max_output_tokens,
        ),
        "batchGatePassed": True,
    })


def _passed_gold_report(cfg):
    llm = {
        "n_relevance": 400,
        "relevant_precision": 0.9524,
        "relevant_recall": 0.9,
        "complaint_n": 3,
        "complaint_pass_count": 3,
        "complaint_passed": True,
        "confusion": {"relevance": {
            "relevant": {"relevant": 180, "irrelevant": 20, "needs_context": 0, "none": 0},
            "irrelevant": {"relevant": 9, "irrelevant": 191, "needs_context": 0, "none": 0},
            "needs_context": {"relevant": 0, "irrelevant": 0, "needs_context": 0, "none": 0},
        }},
    }
    manifest, digest = release_regressions.load_manifest()
    fixed = {
        "manifestVersion": manifest["manifestVersion"],
        "manifestSha256": digest,
        "commentCases": [{
            "caseId": row["caseId"],
            "expectedRelevance": row["expectedRelevance"],
            "humanRelevance": row["expectedRelevance"],
            "modelRelevance": row["expectedRelevance"],
            "passed": True,
        } for row in manifest["commentCases"]],
        "officialAttributionCases": release_regressions.official_regression_results(),
        "passed": True,
    }
    return {
        "reportType": "comment-relevance-human-gold-v1",
        "sample_source": "llm",
        "n": 400,
        "n_gold": 400,
        "policy": {
            "model": "provider-returned-snapshot",
            "requestedModel": cfg.model,
            "promptVersion": cfg.prompt_version,
            "schemaVersion": cfg.schema_version,
            "taxonomyVersion": cfg.taxonomy_version,
            "commentRouteVersion": COMMENT_ROUTE_VERSION,
            "productPoolDigest": product_pool_digest(),
        },
        "by_system": {"student": {}, "llm": llm, "combined": {}},
        "fixedRegressions": fixed,
        "qualityGate": {"passed": True},
        "ai_validation": {"level": "spot_check", "n": 400},
    }


def test_production_analysis_requires_matching_v3_human_gold_report():
    cfg = config.load(
        model="gpt-5.6-luna", prompt_version="comment-product-v3",
        schema_version="v2", taxonomy_version="v2", micro_batch_size=5,
    )
    with pytest.raises(ValueError, match="quality-report"):
        analyze.check_quality(cfg, None)

    report = _passed_gold_report(cfg)
    analyze.check_quality(cfg, report)

    stale_routes = json.loads(json.dumps(report))
    stale_routes["policy"].pop("commentRouteVersion")
    stale_routes["policy"].pop("productPoolDigest")
    with pytest.raises(ValueError, match="policy"):
        analyze.check_quality(cfg, stale_routes)

    old = json.loads(json.dumps(report))
    old["policy"]["promptVersion"] = "comment-product-v2"
    with pytest.raises(ValueError, match="comment-product-v3"):
        analyze.check_quality(cfg, old)

    forged = json.loads(json.dumps(report))
    forged["by_system"]["llm"]["relevant_precision"] = 1.0
    with pytest.raises(ValueError, match="confusion matrix"):
        analyze.check_quality(cfg, forged)
