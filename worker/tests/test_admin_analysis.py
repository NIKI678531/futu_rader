import os
import sys
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ai import config  # noqa: E402
from jobs import admin_analysis  # noqa: E402
from jobs.admin_analysis import process_next  # noqa: E402
from radar_db.analysis_requests import enqueue, list_requests  # noqa: E402
from radar_db.schema import metadata  # noqa: E402


def _engine():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    return engine


def test_worker_runs_the_requested_range_and_persists_success_summary():
    engine = _engine()
    queued, _ = enqueue(engine, "d14")
    seen = []

    result = process_next(
        engine=engine,
        owner="worker-1",
        runner=lambda range_key: seen.append(range_key) or {
            "status": "complete",
            "productsTotal": 135,
            "productsCompleted": 135,
            "aiRequests": 0,
            "annotationsWritten": 0,
            "synthesisWritten": 0,
            "reused": 1080,
            "errors": 0,
        },
    )

    assert seen == ["d14"]
    assert result["id"] == queued["id"]
    assert result["status"] == "succeeded"
    stored = list_requests(engine)[0]
    assert stored["status"] == "succeeded"
    assert stored["summary"]["aiRequests"] == 0
    assert stored["summary"]["reused"] == 1080


def test_worker_persists_partial_and_failed_results():
    engine = _engine()
    enqueue(engine, "d2")
    partial = process_next(
        engine=engine,
        owner="worker-1",
        runner=lambda _range: {"status": "budget_exhausted", "errors": 0},
    )
    assert partial["status"] == "partial"

    enqueue(engine, "d1")

    def fail(_range):
        raise ValueError("AI provider credential unavailable")

    failed = process_next(engine=engine, owner="worker-1", runner=fail)
    assert failed["status"] == "failed"
    stored = list_requests(engine)[0]
    assert stored["status"] == "failed"
    assert stored["error"] == "AI provider credential unavailable"
    assert stored["summary"]["errors"] == 1
    assert stored["summary"]["aiRequests"] is None


def test_global_analysis_lease_contention_requeues_the_request():
    engine = _engine()
    queued, _ = enqueue(engine, "d30")

    def contend(_range):
        raise RuntimeError("An analysis worker already holds the lease")

    result = process_next(engine=engine, owner="worker-1", runner=contend)

    assert result["id"] == queued["id"]
    assert result["status"] == "queued"
    assert list_requests(engine)[0]["status"] == "queued"


def test_run_uses_selected_range_top_comment_products_and_can_make_zero_calls(monkeypatch):
    engine = _engine()
    monkeypatch.setenv("AI_MANUAL_MAX_HTTP_REQUESTS", "500")
    cfg = config.load(
        model="test-model",
        prompt_version="comment-product-v4",
        schema_version="v3",
        taxonomy_version="product-feedback-v4",
    )
    captured = {}
    monkeypatch.delenv("AI_DATA_GOVERNANCE_APPROVED", raising=False)
    monkeypatch.setattr(admin_analysis.config, "load", lambda: cfg)
    monkeypatch.setattr(
        admin_analysis,
        "top_comment_products",
        lambda *_args, **_kwargs: [
            {"rank": 1, "code": "3033", "comments": 10},
            {"rank": 2, "code": "3067", "comments": 5},
        ],
    )

    def make_plan(_engine, args):
        captured.update(
            ranges=args.ranges,
            codes=args.codes,
            ownership=args.ownership,
            anchor_mode=args.anchor_mode,
        )
        return {"anchor": "2026-08-25", "codes": ["3033", "3067"]}

    class Budget:
        def __init__(self, *_args, **_kwargs):
            pass

        def reserve(self):
            return None

    monkeypatch.setattr(admin_analysis.analyze, "make_plan", make_plan)
    monkeypatch.setattr(admin_analysis, "DailyBudget", Budget)
    monkeypatch.setattr(
        admin_analysis,
        "build_provider",
        lambda _cfg, control: SimpleNamespace(control=control),
    )
    monkeypatch.setattr(
        admin_analysis.full_own,
        "run_manual",
        lambda *_args, **_kwargs: {
            "status": "complete",
            "products": {"3033": {"complete": True}, "3067": {"complete": True}},
            "batchRun": {"httpAttempts": 0},
            "work": {
                "annotationsWritten": 0,
                "synthesisWritten": 0,
                "synthesisReused": 16,
                "errors": 0,
            },
        },
    )

    summary = admin_analysis.run_analysis("d30", engine=engine)

    assert captured == {
        "ranges": "d30",
        "codes": "3033,3067",
        "ownership": "all",
        "anchor_mode": "latest-complete",
    }
    assert summary["productsCompleted"] == summary["productsTotal"] == 2
    assert summary["aiRequests"] == 0
    assert summary["reused"] == 16
    assert summary["selectedCodes"] == ["3033", "3067"]
    assert summary["selectionLimit"] == 10


def test_readiness_needs_no_release_reports(monkeypatch):
    cfg = config.load(model="test-model")
    monkeypatch.delenv("AI_DATA_GOVERNANCE_APPROVED", raising=False)
    monkeypatch.delenv("AI_CALIBRATION_REPORT", raising=False)
    monkeypatch.delenv("AI_QUALITY_REPORT", raising=False)
    monkeypatch.setattr(admin_analysis.config, "load", lambda: cfg)

    assert admin_analysis.runtime_readiness()["ready"] is True

    monkeypatch.setenv("AI_DATA_GOVERNANCE_APPROVED", "false")
    readiness = admin_analysis.runtime_readiness()
    assert readiness["ready"] is False
    assert "AI_DATA_GOVERNANCE_APPROVED=false" in readiness["error"]
