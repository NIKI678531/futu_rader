from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.pool import StaticPool

from app import create_app
from core.admin_ops import AI_LAYER_B, AdminOperations, AdminUnavailable, AdminValidationError
from radar_db.analysis_requests import claim_next, enqueue, finish, list_requests
from radar_db.revisions import synthesis_generation_key
from radar_db.schema import meta_kv, metadata


class DummyOperations:
    def __init__(self):
        self.ranges = []

    def pipeline(self, range_key):
        self.ranges.append(range_key)
        return {"selectedRange": range_key, "comments": [], "ranges": []}

    def enqueue_analysis(self, range_key):
        self.ranges.append(range_key)
        return {
            "created": True,
            "run": {"id": "ai-1", "range": range_key, "status": "queued"},
        }


def _engine():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    return engine


def _admin_client(operations=None):
    app = create_app()
    app.config.update(
        TESTING=True,
        ADMIN_OPERATIONS_FACTORY=lambda: operations or DummyOperations(),
    )
    return app.test_client()


def test_pipeline_defaults_to_d1_and_accepts_a_selected_range():
    operations = DummyOperations()
    client = _admin_client(operations)

    default = client.get("/api/v1/admin/pipeline")
    selected = client.get("/api/v1/admin/pipeline?range=d14")

    assert default.status_code == selected.status_code == 200
    assert default.json["data"]["selectedRange"] == "d1"
    assert selected.json["data"]["selectedRange"] == "d14"
    assert operations.ranges == ["d1", "d14"]


def test_analysis_run_returns_202_and_only_accepts_range():
    operations = DummyOperations()
    client = _admin_client(operations)

    response = client.post("/api/v1/admin/analysis-runs", json={"range": "d7"})

    assert response.status_code == 202
    assert response.json["data"]["run"]["status"] == "queued"
    assert operations.ranges == ["d7"]
    assert client.post("/api/v1/admin/analysis-runs", json={}).status_code == 400
    assert client.post(
        "/api/v1/admin/analysis-runs", json={"range": "d1", "force": True}
    ).status_code == 400
    assert client.post("/api/v1/admin/analysis-runs", data="not-json").status_code == 400


def test_duplicate_active_analysis_submission_returns_the_existing_run():
    engine = _engine()
    now = datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc)
    with engine.begin() as conn:
        conn.execute(insert(meta_kv), [
            {"k": "ai_analysis_worker_heartbeat", "v": "2026-10-06T00:59:30Z"},
            {
                "k": "ai_analysis_worker_readiness",
                "v": '{"ready":true,"error":null}',
            },
        ])
    operations = AdminOperations(
        target_engine=engine,
        market_provider=object(),
        now=lambda: now,
    )
    client = _admin_client(operations)

    first = client.post("/api/v1/admin/analysis-runs", json={"range": "d1"})
    duplicate = client.post("/api/v1/admin/analysis-runs", json={"range": "d1"})

    assert first.status_code == duplicate.status_code == 202
    assert first.json["data"]["created"] is True
    assert duplicate.json["data"]["created"] is False
    assert duplicate.json["data"]["run"]["id"] == first.json["data"]["run"]["id"]


def test_analysis_submission_fails_before_queueing_when_worker_is_not_ready():
    engine = _engine()
    operations = AdminOperations(target_engine=engine, market_provider=object())

    response = _admin_client(operations).post(
        "/api/v1/admin/analysis-runs", json={"range": "d1"}
    )

    assert response.status_code == 503
    assert response.json["error"]["message"] == "AI worker 未运行"
    assert list_requests(engine) == []


def test_removed_airflow_management_routes_are_not_registered():
    client = _admin_client()

    assert client.get("/api/v1/admin/data-sources").status_code == 404
    assert client.get("/api/v1/admin/tasks").status_code == 404
    assert client.get("/api/v1/admin/job-runs").status_code == 404
    assert client.post("/api/v1/admin/tasks/analyze/run").status_code == 404


def test_product_catalog_backend_remains_available():
    operations = DummyOperations()
    operations.target_engine = _engine()

    response = _admin_client(operations).get("/api/v1/admin/product-catalog")

    assert response.status_code == 200
    assert response.json["data"]["available"] is True


def test_admin_errors_keep_their_http_contract():
    class Invalid(DummyOperations):
        def pipeline(self, range_key):
            raise AdminValidationError("range 无效")

    class Unavailable(DummyOperations):
        def pipeline(self, range_key):
            raise AdminUnavailable("Radar 数据库暂时不可用")

    assert _admin_client(Invalid()).get("/api/v1/admin/pipeline").status_code == 400
    response = _admin_client(Unavailable()).get("/api/v1/admin/pipeline")
    assert response.status_code == 503
    assert response.json["error"]["message"] == "Radar 数据库暂时不可用"


def test_comment_rows_reuse_pool_counts_and_preserve_zero_vs_unavailable(monkeypatch):
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(insert(meta_kv), {"k": "anchor", "v": "2026-08-25"})
    products = [
        {"code": "3067", "name": "后一个", "ownership": "own"},
        {"code": "3033", "name": "前一个", "ownership": "own"},
        {"code": "3088", "name": "无页面数值", "ownership": "peer"},
    ]
    pool_rows = [
        {
            "code": "3033", "comments": 9,
            "commentFunnel": {
                "rawPlatformCount": 12, "platformCount": 11, "parsedCount": 10,
                "ruleEligibleCount": 8, "aiCompletedCount": 7, "relevantCount": 6,
            },
        },
        {
            "code": "3067", "comments": 0,
            "commentFunnel": {
                "rawPlatformCount": 0, "platformCount": 0, "parsedCount": 0,
                "ruleEligibleCount": 0, "aiCompletedCount": 0, "relevantCount": 0,
            },
        },
    ]

    class PoolProvider:
        def pool(self, range_key):
            assert range_key == "d1"
            return {"list": pool_rows}

    _use_products(monkeypatch, products)
    result = AdminOperations(target_engine=engine, market_provider=PoolProvider()).pipeline("d1")

    assert [row["code"] for row in result["comments"]] == ["3033", "3067", "3088"]
    assert [row["rank"] for row in result["comments"]] == [1, 2, 3]
    assert result["commentDisplayLimit"] == 10
    assert result["analyzedProductCodes"] == ["3033", "3067", "3088"]
    assert [row["key"] for row in result["ranges"]] == ["d1", "d2", "d7", "d14", "d30", "mtd"]
    assert result["recentRuns"] == []
    first = result["comments"][0]
    source = pool_rows[0]
    assert [
        first[key] for key in (
            "rawPlatformCount", "platformCount", "parsedCount",
            "ruleEligibleCount", "aiCompletedCount", "relevantCount",
        )
    ] == [
        source["commentFunnel"][key] for key in (
            "rawPlatformCount", "platformCount", "parsedCount",
            "ruleEligibleCount", "aiCompletedCount", "relevantCount",
        )
    ]
    assert first["pageCommentLabel"] == "有评论 · 9 条"
    assert result["comments"][1]["pageCommentLabel"] == "0 条"
    assert result["comments"][2]["pageCommentLabel"] == "暂不可用"


def _use_products(monkeypatch, products):
    monkeypatch.setattr("core.admin_ops.load_products", lambda **_kwargs: products)
    monkeypatch.setattr("core.top_products.load_products", lambda **_kwargs: products)


def test_pipeline_orders_comments_by_volume_and_analyzes_every_product(monkeypatch):
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(insert(meta_kv), {"k": "anchor", "v": "2026-08-25"})
    products = [
        {"code": f"{3000 + index}", "name": f"产品 {index}", "ownership": "own"}
        for index in range(12)
    ]
    pool_rows = [
        {"code": product["code"], "comments": index, "commentFunnel": {}}
        for index, product in enumerate(products)
    ]

    class PoolProvider:
        def pool(self, _range_key):
            return {"list": pool_rows}

    _use_products(monkeypatch, products)
    result = AdminOperations(target_engine=engine, market_provider=PoolProvider()).pipeline("d1")

    expected = [f"{3000 + index}" for index in range(11, 1, -1)]
    assert [row["code"] for row in result["comments"][:10]] == expected
    assert sorted(result["analyzedProductCodes"]) == sorted(p["code"] for p in products)
    assert result["layers"]["productCount"] == 12


def test_pipeline_selection_follows_the_worker_own_limit(monkeypatch):
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(insert(meta_kv), [
            {"k": "anchor", "v": "2026-08-25"},
            {"k": "ai_analysis_worker_readiness", "v": '{"ready":true,"ownTopLimit":15}'},
        ])
    seen = {}

    def select_products(_engine, range_key, *, own_limit=None, anchor=None):
        seen.update(range=range_key, own_limit=own_limit)
        return [{"code": "3033"}]

    class PoolProvider:
        def pool(self, _range_key):
            return {"list": []}

    _use_products(monkeypatch, [{"code": "3033", "name": "产品", "ownership": "own"}])
    monkeypatch.setattr("core.admin_ops.analysis_products", select_products)
    result = AdminOperations(target_engine=engine, market_provider=PoolProvider()).pipeline("d7")

    assert seen == {"range": "d7", "own_limit": 15}
    assert result["analyzedProductCodes"] == ["3033"]


def test_pipeline_refreshes_its_cached_market_provider_before_reading(monkeypatch):
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(insert(meta_kv), {"k": "anchor", "v": "2026-08-25"})
    products = [{"code": "3033", "name": "产品一", "ownership": "own"}]

    class RefreshingProvider:
        def __init__(self):
            self.ready = False

        def refresh(self):
            self.ready = True

        def pool(self, _range_key):
            return {
                "list": [
                    {
                        "code": "3033",
                        "comments": 12 if self.ready else None,
                        "commentFunnel": {},
                    }
                ]
            }

    provider = RefreshingProvider()
    _use_products(monkeypatch, products)

    result = AdminOperations(target_engine=engine, market_provider=provider).pipeline("d1")

    assert result["analyzedProductCodes"] == ["3033"]
    assert result["comments"][0]["pageCommentCount"] == 12


def test_pipeline_reports_worker_heartbeat_health(monkeypatch):
    engine = _engine()
    now = datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc)
    with engine.begin() as conn:
        conn.execute(insert(meta_kv), [
            {"k": "anchor", "v": "2026-08-25"},
            {"k": "ai_analysis_worker_heartbeat", "v": "2026-10-06T00:59:30Z"},
            {
                "k": "ai_analysis_worker_readiness",
                "v": '{"ready":true,"error":null}',
            },
        ])
    _use_products(monkeypatch, [])

    class EmptyProvider:
        def pool(self, _range_key):
            return {"list": []}

    result = AdminOperations(
        target_engine=engine,
        market_provider=EmptyProvider(),
        now=lambda: now,
    ).pipeline("d1")

    assert result["worker"] == {
        "online": True,
        "lastSeenAt": "2026-10-06T00:59:30Z",
        "ready": True,
        "error": None,
    }


def test_pipeline_reports_missing_worker_as_offline(monkeypatch):
    engine = _engine()
    _use_products(monkeypatch, [])

    class EmptyProvider:
        def pool(self, _range_key):
            return {"list": []}

    result = AdminOperations(
        target_engine=engine,
        market_provider=EmptyProvider(),
    ).pipeline("d1")

    assert result["worker"] == {
        "online": False,
        "lastSeenAt": None,
        "ready": False,
        "error": "AI worker 未运行",
    }


def test_layer_b_counts_published_empty_generations_as_complete(monkeypatch):
    """An intentional empty result is complete, even though it has no output row."""

    engine = _engine()
    products = [
        {"code": "3033", "name": "产品一", "ownership": "own"},
        {"code": "3067", "name": "产品二", "ownership": "peer"},
    ]
    anchor = "2026-08-25"
    metadata_rows = [{"k": "anchor", "v": anchor}]
    for code in ("3033", "3067"):
        for kind, _label, _locations in AI_LAYER_B:
            metadata_rows.append(
                {
                    "k": synthesis_generation_key(code, "d1", kind),
                    "v": f"{anchor}|empty-fingerprint",
                }
            )
    with engine.begin() as conn:
        conn.execute(insert(meta_kv), metadata_rows)

    class PoolProvider:
        def pool(self, _range_key):
            return {
                "list": [
                    {"code": "3033", "comments": 10, "commentFunnel": {}},
                    {"code": "3067", "comments": 5, "commentFunnel": {}},
                ]
            }

    _use_products(monkeypatch, products)
    result = AdminOperations(target_engine=engine, market_provider=PoolProvider()).pipeline("d1")

    assert all(row["status"] == "complete" for row in result["layers"]["layerB"])
    assert all(row["producedPairs"] == 2 for row in result["layers"]["layerB"])


def test_queue_deduplicates_active_range_and_claims_in_range_priority_order():
    engine = _engine()
    submitted = datetime(2026, 10, 5, 8, 0)
    d30, created = enqueue(engine, "d30", now=submitted)
    d1, _ = enqueue(engine, "d1", now=submitted + timedelta(seconds=1))
    duplicate, duplicate_created = enqueue(engine, "d1", now=submitted + timedelta(seconds=2))

    assert created is True
    assert d30["requestedAt"] == "2026-10-05T08:00:00Z"
    assert duplicate_created is False
    assert duplicate["id"] == d1["id"]
    claimed = claim_next(engine, owner="worker-1", now=submitted + timedelta(seconds=3))
    assert claimed["range"] == "d1"
    assert finish(engine, claimed["id"], "worker-1", "succeeded", summary={"aiRequests": 0})
    assert claim_next(engine, owner="worker-1", now=submitted + timedelta(seconds=4))["id"] == d30["id"]


def test_expired_request_is_recovered_and_can_continue():
    engine = _engine()
    submitted = datetime(2026, 10, 5, 8, 0)
    queued, _ = enqueue(engine, "d2", now=submitted)
    first = claim_next(engine, owner="dead-worker", now=submitted, lease_seconds=5)

    recovered = claim_next(
        engine, owner="new-worker", now=submitted + timedelta(seconds=6), lease_seconds=5
    )

    assert first["id"] == queued["id"] == recovered["id"]
    assert recovered["attempts"] == 2
    assert recovered["status"] == "running"


def test_terminal_request_allows_a_new_run_for_the_same_range():
    engine = _engine()
    first, _ = enqueue(engine, "d7")
    running = claim_next(engine, owner="worker-1")
    finish(engine, running["id"], "worker-1", "failed", error="provider unavailable")
    second, created = enqueue(engine, "d7")

    assert created is True
    assert second["id"] != first["id"]
    history = list_requests(engine)
    assert [row["status"] for row in history] == ["queued", "failed"]


@pytest.mark.parametrize("range_key", ["d90", "ytd", ""])
def test_unknown_analysis_range_is_rejected(range_key):
    with pytest.raises(ValueError):
        enqueue(_engine(), range_key)


def test_month_to_date_and_schedule_source_are_queueable():
    engine = _engine()
    request, created = enqueue(engine, "mtd", requested_by="schedule")

    assert created is True
    assert (request["range"], request["requestedBy"]) == ("mtd", "schedule")
    assert list_requests(engine)[0]["requestedBy"] == "schedule"
    with pytest.raises(ValueError):
        enqueue(engine, "d1", requested_by="cron")
