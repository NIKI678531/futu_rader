import json
from contextlib import nullcontext
from datetime import datetime
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import insert

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import config
from ai import release_regressions
from radar_db import create_all, make_engine
from radar_db.comment_filter import filter_readiness_values, load_comment_filter_config
from radar_db.comment_routes import (
    COMMENT_ROUTE_VERSION,
    readiness_values as route_readiness_values,
)
from radar_db.schema import (
    analysis_scopes,
    annotation_jobs,
    comment_product_routes,
    comments,
    feed_mentions,
    feeds,
    mentions,
    meta_kv,
)
from scripts import calibrate


def _engine(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "calibrate.db").as_posix())
    create_all(engine)
    return engine


def _mark_filter_ready(conn):
    conn.execute(insert(meta_kv), [
        {"k": key, "v": value}
        for key, value in filter_readiness_values(load_comment_filter_config()).items()
    ])


def _mark_routes_ready(conn):
    conn.execute(insert(meta_kv), [
        {"k": key, "v": value}
        for key, value in route_readiness_values().items()
    ])


def _route(conn, code, *, matched_parent=True, matched_comment=False):
    conn.execute(insert(comment_product_routes).values(
        comment_id=1,
        subject_code=code,
        feed_id=1,
        matched_parent=matched_parent,
        matched_comment=matched_comment,
        rule_version=COMMENT_ROUTE_VERSION,
        updated_at=datetime(2026, 8, 26),
    ))


def _feed(**overrides):
    values = {
        "feed_id": 1,
        "code": "3033",
        "source_ticker": "03033.HK",
        "posted_at": datetime(2026, 8, 20, 9),
        "feed_type": 1,
        "title": "ETF discussion",
        "content": "$03033.HK$",
        "like_count": 0,
        "comment_count": 0,
        "image_count": 0,
        "raw_json_broken": False,
    }
    values.update(overrides)
    return values


def test_scope_candidates_reject_cross_product_queued_pair(tmp_path):
    engine = _engine(tmp_path)
    now = datetime(2026, 8, 26)
    with engine.begin() as conn:
        _mark_filter_ready(conn)
        conn.execute(insert(feeds).values(**_feed()))
        conn.execute(insert(feed_mentions).values(
            feed_id=1, raw_ticker="03033.HK", market="HK", occurrences=1,
        ))
        conn.execute(insert(comments).values(
            comment_id=1, feed_id=1, content="費率更低", author_uid="reader",
        ))
        _mark_routes_ready(conn)
        conn.execute(insert(analysis_scopes).values(
            scope_id="scope-1", task="comment_product", codes_json='["3033"]',
            date_from=datetime(2026, 8, 19), date_to=datetime(2026, 8, 25),
            time_basis="feed_posted_at", with_baseline=False,
            prompt_version="comment-product-v3", taxonomy_version="v2",
            schema_version="v2", created_at=now,
        ))
        conn.execute(insert(annotation_jobs).values(
            target_type="comment", target_id=1, subject_code="7226",
            task="comment_product", input_hash="queued-pair", status="pending",
            priority=0, attempts=0, scope_id="scope-1", stage="llm",
            created_at=now, updated_at=now,
        ))

    rows = calibrate.candidates(
        engine, SimpleNamespace(scope="scope-1", codes=None, from_=None, to=None),
        {"3033": "own", "7226": "own"},
    )

    assert rows == []


def test_scope_candidates_ignore_superseded_jobs(tmp_path):
    engine = _engine(tmp_path)
    now = datetime(2026, 8, 26)
    with engine.begin() as conn:
        _mark_filter_ready(conn)
        conn.execute(insert(feeds).values(**_feed()))
        conn.execute(insert(feed_mentions).values(
            feed_id=1, raw_ticker="03033.HK", market="HK", occurrences=1,
        ))
        conn.execute(insert(comments).values(
            comment_id=1, feed_id=1, content="費率更低", author_uid="reader",
        ))
        _route(conn, "3033")
        _mark_routes_ready(conn)
        conn.execute(insert(analysis_scopes).values(
            scope_id="scope-1", task="comment_product", codes_json='["3033"]',
            date_from=datetime(2026, 8, 19), date_to=datetime(2026, 8, 25),
            time_basis="feed_posted_at", with_baseline=False,
            prompt_version="comment-product-v3", taxonomy_version="v2",
            schema_version="v2", created_at=now,
        ))
        conn.execute(insert(annotation_jobs).values(
            target_type="comment", target_id=1, subject_code="3033",
            task="comment_product", input_hash="stale-pair", status="superseded",
            priority=0, attempts=0, scope_id="scope-1", stage="llm",
            created_at=now, updated_at=now,
        ))

    rows = calibrate.candidates(
        engine, SimpleNamespace(scope="scope-1", codes=None, from_=None, to=None),
        {"3033": "own"},
    )

    assert rows == []


def test_non_scope_candidates_include_every_content_tag_route(tmp_path):
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        _mark_filter_ready(conn)
        conn.execute(insert(feeds).values(**_feed(content="$03033.HK$ $07226.HK$")))
        conn.execute(insert(comments).values(
            comment_id=1, feed_id=1, content="$07226.HK$ 費率更低", author_uid="reader",
        ))
        conn.execute(insert(mentions), [
            {"feed_id": 1, "code": "3033", "source": "anchor", "in_pool": True},
            {"feed_id": 1, "code": "3033", "source": "body", "in_pool": True},
            {"feed_id": 1, "code": "7226", "source": "body", "in_pool": True},
        ])
        conn.execute(insert(feed_mentions), [
            {"feed_id": 1, "raw_ticker": "03033.HK", "market": "HK", "occurrences": 1},
            {"feed_id": 1, "raw_ticker": "07226.HK", "market": "HK", "occurrences": 1},
        ])
        _route(conn, "3033")
        _route(conn, "7226", matched_parent=True, matched_comment=True)
        _mark_routes_ready(conn)

    rows = calibrate.candidates(
        engine,
        SimpleNamespace(scope=None, codes="3033,7226", from_="2026-08-19", to="2026-08-25"),
        {"3033": "own", "7226": "own"},
    )

    assert [(row.comment_id, row.code) for row in rows] == [(1, "3033"), (1, "7226")]


def test_scope_candidates_accept_3037_when_comment_itself_has_tag(tmp_path):
    engine = _engine(tmp_path)
    now = datetime(2026, 8, 26)
    with engine.begin() as conn:
        _mark_filter_ready(conn)
        conn.execute(insert(feeds).values(**_feed(
            code="3037",
            source_ticker="03037.HK",
            content="$800000.HK$",
        )))
        conn.execute(insert(feed_mentions).values(
            feed_id=1, raw_ticker="800000.HK", market="HK", occurrences=1,
        ))
        conn.execute(insert(comments).values(
            comment_id=1, feed_id=1, content="$03037.HK$ reply cannot rescue parent",
            author_uid="reader",
        ))
        _route(conn, "3037", matched_parent=False, matched_comment=True)
        _mark_routes_ready(conn)
        conn.execute(insert(analysis_scopes).values(
            scope_id="scope-3037", task="comment_product", codes_json='["3037"]',
            date_from=datetime(2026, 8, 19), date_to=datetime(2026, 8, 25),
            time_basis="feed_posted_at", with_baseline=False,
            prompt_version="comment-product-v3", taxonomy_version="v2",
            schema_version="v2", created_at=now,
        ))
        conn.execute(insert(annotation_jobs).values(
            target_type="comment", target_id=1, subject_code="3037",
            task="comment_product", input_hash="excluded-parent", status="pending",
            priority=0, attempts=0, scope_id="scope-3037", stage="llm",
            created_at=now, updated_at=now,
        ))

    rows = calibrate.candidates(
        engine, SimpleNamespace(scope="scope-3037", codes=None, from_=None, to=None),
        {"3037": "own"},
    )

    assert [(row.comment_id, row.code) for row in rows] == [(1, "3037")]


@pytest.mark.parametrize(
    "args",
    [
        SimpleNamespace(scope="scope-1", codes=None, from_=None, to=None),
        SimpleNamespace(scope=None, codes="3033", from_="2026-08-19", to="2026-08-25"),
    ],
    ids=("scope", "date-range"),
)
def test_candidates_fail_closed_without_route_readiness(tmp_path, args):
    engine = _engine(tmp_path)

    with pytest.raises(RuntimeError, match="comment AI routes are not ready"):
        calibrate.candidates(engine, args, {"3033": "own"})


def test_candidates_fail_closed_with_stale_route_readiness(tmp_path):
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        _mark_routes_ready(conn)
        conn.execute(
            meta_kv.update()
            .where(meta_kv.c.k == "comment_route_pool_digest")
            .values(v="stale-digest")
        )

    with pytest.raises(RuntimeError, match="comment AI routes are not ready"):
        calibrate.candidates(
            engine,
            SimpleNamespace(scope=None, codes="3033", from_="2026-08-19", to="2026-08-25"),
            {"3033": "own"},
        )


def test_effective_batch_size_honours_provider_limit():
    assert calibrate.effective_batch_size(SimpleNamespace(max_batch_size=1), 5) == 1
    assert calibrate.effective_batch_size(SimpleNamespace(max_batch_size=None), 5) == 5


def test_batch_release_gate_requires_quality_and_three_x_throughput():
    good = calibrate.batch_release_gate(
        sample_count=300,
        failed_single=0,
        failed_batch=0,
        relevance_agreement=0.96,
        attitude_agreement=0.94,
        throughput_multiplier=3.2,
        effective_batch_size=5,
    )
    assert good["passed"] is True
    assert calibrate.batch_release_gate(**{**good["inputs"], "throughput_multiplier": 2.9})["passed"] is False
    assert calibrate.batch_release_gate(**{**good["inputs"], "effective_batch_size": 1})["passed"] is False


def test_batch_30_is_not_a_supported_calibration_configuration():
    with pytest.raises(SystemExit):
        calibrate.main([
            "--codes", "3033", "--from", "2026-08-01", "--to", "2026-08-31",
            "--batch", "30", "--max-http-requests", "100",
        ])


def test_nonempty_strata_report_is_json_serializable(tmp_path, monkeypatch):
    sample = SimpleNamespace(
        content="ETF fees are too high", code="3033", comment_id=1,
        author_uid="test-user", feed_id=1, parent_content="The direct reply",
        grandparent_content="The middle reply", great_grandparent_content="The root reply",
        title="ETF discussion", post_content="Product discussion",
    )
    label = SimpleNamespace(
        attitude="negative", relevance="relevant", needs_review=False,
        compliance_tags=[],
    )
    cfg = config.load(model="test-model", prompt_version="comment-product-v3",
                      schema_version="v2", taxonomy_version="v2")
    monkeypatch.setattr(calibrate.config, "load", lambda: cfg)
    monkeypatch.setattr(calibrate, "make_engine", lambda: object())
    monkeypatch.setattr(calibrate, "report_policy_fields", lambda _engine: {
        "commentRouteVersion": COMMENT_ROUTE_VERSION,
        "productPoolDigest": route_readiness_values()["comment_route_pool_digest"],
    })
    monkeypatch.setattr(calibrate, "candidates", lambda *args: [sample])
    monkeypatch.setattr(release_regressions, "comment_case_rows", lambda **_kwargs: [])
    monkeypatch.setattr(calibrate, "build_provider", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(calibrate, "WorkerLease", lambda *args: nullcontext())
    payloads = []

    def label_batch(*args):
        payloads.append(args[3][0][1])
        if len(args) > 5:
            args[5]["comment:1|product:3033"] = "returned-model-revision"
        return {"comment:1|product:3033": label}, 0

    monkeypatch.setattr(calibrate, "label_batch", label_batch)
    monkeypatch.setattr(calibrate.offpool_stocks, "load_from_db", lambda *args: [])
    monkeypatch.setattr(calibrate, "OUT_DIR", tmp_path)
    monkeypatch.setattr(calibrate, "default_data_dir", lambda: tmp_path)

    assert calibrate.main([
        "--codes", "3033", "--from", "2026-08-01", "--to", "2026-08-31", "--n", "1",
        "--max-http-requests", "10",
    ]) == 0

    report = json.loads(next(tmp_path.glob("calibration-*.json")).read_text(encoding="utf-8"))
    assert report["strata"] == {"zh-Hans|own": 1}
    assert report["b1_vs_b5"]["n"] == 1
    assert report["v1_vs_current"]["n"] == 1
    assert report["batchGatePassed"] is False
    assert report["policy"]["batchSize"] == 5
    assert payloads[0]["parent_comments"] == [
        "The direct reply", "The middle reply", "The root reply",
    ]
    gold = next(tmp_path.glob("calibration-*-gold/gold-llm-1.xlsx"))
    labels = next(tmp_path.glob("calibration-*-gold/gold-llm-1-model-labels.xlsx"))
    from scripts import evaluate_gold
    model = evaluate_gold.read_model_labels(labels)
    assert model["G0001"]["llm_policy"] == {
        "model": "returned-model-revision", "requestedModel": "test-model",
        "promptVersion": "comment-product-v3",
        "schemaVersion": "v2", "taxonomyVersion": "v2",
        "commentRouteVersion": COMMENT_ROUTE_VERSION,
        "productPoolDigest": route_readiness_values()["comment_route_pool_digest"],
    }
    assert gold.exists()


def test_fixed_complaint_cases_are_injected_into_the_bounded_gold_sample():
    ordinary = [SimpleNamespace(comment_id=i, code="3033", content=f"ordinary {i}",
                                parent_content=None) for i in range(20)]
    picked = calibrate.calibration_sample(ordinary, 10, {"3033": "own", "3037": "own"})

    manifest, _digest = release_regressions.load_manifest()
    case_ids = {row["caseId"] for row in manifest["commentCases"]}
    assert len(picked) == 10
    assert {getattr(row, "regression_case_id", None) for row in picked} >= case_ids
