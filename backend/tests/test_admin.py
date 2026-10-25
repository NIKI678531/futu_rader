from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.pool import StaticPool

from app import create_app
from core.admin_ops import AI_LAYER_B, AdminOperations, AdminUnavailable, AdminValidationError
from radar_db.analysis_requests import claim_next, enqueue, finish, list_requests
from radar_db.revisions import synthesis_generation_key
from radar_db.schema import analysis_scope_jobs, annotation_jobs, meta_kv, metadata


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


def test_admin_page_allows_only_one_selected_range_in_the_queue():
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(insert(meta_kv), _ready_worker_meta())
    client = _admin_client(
        AdminOperations(
            target_engine=engine,
            market_provider=object(),
            now=lambda: datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc),
        )
    )

    first = client.post("/api/v1/admin/analysis-runs", json={"range": "d7"})
    second = client.post("/api/v1/admin/analysis-runs", json={"range": "d1"})

    assert first.status_code == 202
    assert second.status_code == 409
    assert "D7" in second.json["error"]["message"]
    assert [run["range"] for run in list_requests(engine)] == ["d7"]


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
    assert [row["key"] for row in result["ranges"]] == [
        "d0", "d1", "d2", "d7", "d14", "d30", "mtd",
    ]
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
    seen = []

    def select_products(_engine, range_key, *, own_limit=None, anchor=None):
        seen.append((range_key, own_limit))
        return [{"code": "3033"}]

    class PoolProvider:
        def pool(self, _range_key):
            return {"list": []}

    _use_products(monkeypatch, [{"code": "3033", "name": "产品", "ownership": "own"}])
    monkeypatch.setattr("core.admin_ops.analysis_products", select_products)
    result = AdminOperations(target_engine=engine, market_provider=PoolProvider()).pipeline("d7")

    assert seen[0] == ("d7", 15)
    assert {range_key for range_key, _limit in seen} == {
        "d0", "d1", "d2", "d7", "d14", "d30", "mtd",
    }
    assert {limit for _range, limit in seen} == {15}
    assert result["analyzedProductCodes"] == ["3033"]
    assert [row["expected"] for row in result["coverage"]] == [1] * 7


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


def test_pipeline_uses_narrow_admin_comment_pool_when_provider_supports_it(monkeypatch):
    engine = _engine()
    products = [{"code": "3033", "name": "产品一", "ownership": "own"}]

    class NarrowProvider:
        def admin_comment_pool(self, range_key):
            assert range_key == "d1"
            return {
                "list": [
                    {"code": "3033", "comments": 4, "commentFunnel": {}}
                ]
            }

        def pool(self, _range_key):
            raise AssertionError("full pool aggregation must not run")

    _use_products(monkeypatch, products)
    result = AdminOperations(
        target_engine=engine, market_provider=NarrowProvider()
    ).pipeline("d1")

    assert result["comments"][0]["pageCommentCount"] == 4


def test_ai_layers_counts_direct_and_reused_scope_jobs():
    engine = _engine()
    now = datetime(2026, 10, 6, 1, 0)
    progress = {
        "ranges": ["d1"],
        "status": "running",
        "products": {
            "3033": {"scope": "scope-direct", "complete": False},
            "3067": {"scope": "scope-reused", "complete": False},
        },
    }
    with engine.begin() as conn:
        conn.execute(
            insert(annotation_jobs),
            [
                {
                    "job_id": 1,
                    "target_type": "comment",
                    "target_id": 1,
                    "subject_code": "3033",
                    "task": "comment_product",
                    "input_hash": "direct",
                    "status": "done",
                    "priority": 0,
                    "attempts": 0,
                    "created_at": now,
                    "updated_at": now,
                    "scope_id": "scope-direct",
                    "stage": "student",
                },
                {
                    "job_id": 2,
                    "target_type": "feed",
                    "target_id": 2,
                    "subject_code": "3067",
                    "task": "post_annotation",
                    "input_hash": "reused",
                    "status": "failed",
                    "priority": 0,
                    "attempts": 1,
                    "last_error": "provider unavailable",
                    "created_at": now,
                    "updated_at": now,
                    "scope_id": "original-scope",
                    "stage": "student",
                },
            ],
        )
        conn.execute(
            insert(analysis_scope_jobs),
            {"scope_id": "scope-reused", "job_id": 2},
        )
        layers = AdminOperations(
            target_engine=engine, market_provider=object()
        )._ai_layers(
            conn,
            {
                "anchor": "2026-08-25",
                "own_analysis_progress_d1": json.dumps(progress),
            },
            "d1",
            [
                {"code": "3033", "name": "产品一", "ownership": "own"},
                {"code": "3067", "name": "产品二", "ownership": "peer"},
            ],
        )

    layer_a = {row["id"]: row for row in layers["layerA"]}
    assert layer_a["comment_product"]["counts"]["done"] == 1
    assert layer_a["post_annotation"]["counts"]["failed"] == 1


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


def _ready_worker_meta(now_text="2026-10-06T00:59:30Z", **readiness):
    import json

    return [
        {"k": "ai_analysis_worker_heartbeat", "v": now_text},
        {"k": "ai_analysis_worker_readiness", "v": json.dumps({"ready": True, "error": None, **readiness})},
    ]


def test_layer_b_status_separates_fresh_stale_and_missing_per_kind(monkeypatch):
    engine = _engine()
    anchor = "2026-08-25"
    rows = [{"k": "anchor", "v": anchor}, {"k": "synth_dirty_3067_d1", "v": "1"}]
    for kind, _label, _locations in AI_LAYER_B:
        rows.append({"k": synthesis_generation_key("3033", "d1", kind), "v": f"{anchor}|fp"})
        rows.append({"k": synthesis_generation_key("3067", "d1", kind), "v": f"{anchor}|fp"})
    rows.append({"k": synthesis_generation_key("3088", "d1", "summary"), "v": "2026-08-24|old"})
    with engine.begin() as conn:
        conn.execute(insert(meta_kv), rows)

    class PoolProvider:
        def pool(self, _range_key):
            return {"list": []}

    _use_products(monkeypatch, [
        {"code": "3033", "name": "一", "ownership": "own"},
        {"code": "3067", "name": "二", "ownership": "own"},
        {"code": "3088", "name": "三", "ownership": "peer"},
    ])
    result = AdminOperations(target_engine=engine, market_provider=PoolProvider()).pipeline("d1")

    summary = next(row for row in result["layers"]["layerB"] if row["id"] == "summary")
    assert (summary["producedPairs"], summary["stalePairs"], summary["missingPairs"]) == (1, 2, 0)
    assert summary["status"] == "partial"
    hot = next(row for row in result["layers"]["layerB"] if row["id"] == "hot_summary")
    assert hot["missingCodes"] == ["3088"] and hot["staleCodes"] == ["3067"]
    d1 = next(row for row in result["coverage"] if row["key"] == "d1")
    assert (d1["expected"], d1["complete"], d1["stale"], d1["missing"]) == (3, 1, 1, 1)
    assert d1["missingCodes"] == ["3088"]
    d30 = next(row for row in result["coverage"] if row["key"] == "d30")
    assert (d30["missing"], d30["status"]) == (3, "not_started")


def test_checks_explain_what_blocks_page_data(monkeypatch):
    engine = _engine()
    now = datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            insert(meta_kv),
            [
                {"k": "anchor", "v": "2026-08-25"},
                {
                    "k": "ai_intraday_last_run",
                    "v": json.dumps({
                        "startedAt": "2026-10-06T05:00:00Z",
                        "dataDate": "2026-10-06",
                        "sync": {"status": "succeeded"},
                    }),
                },
            ]
            + _ready_worker_meta(
                provider="typesafe_system_one",
                model="jev-latest",
                generationError="Layer B 需要能生成文字的模型",
                dataMode="dump",
                dailyCron="0 8 * * *",
                intradaySyncCron="5,35 * * * *",
                intradayCron="40 * * * *",
                intradayDeadlineSeconds=1200,
            ),
        )

    class PoolProvider:
        def pool(self, _range_key):
            return {"list": []}

    _use_products(monkeypatch, [])
    result = AdminOperations(
        target_engine=engine, market_provider=PoolProvider(), now=lambda: now,
    ).pipeline("d1")

    checks = {row["id"]: row for row in result["checks"]}
    assert checks["worker"]["status"] == "ok"
    assert checks["classifier"]["status"] == "ok" and "jev-latest" in checks["classifier"]["detail"]
    assert checks["generation"]["status"] == "fail"
    assert "生成文字" in checks["generation"]["detail"]
    assert "本地 dump" in checks["mode"]["detail"]
    assert "0 8 * * *" in checks["daily"]["detail"] and "尚未运行过" in checks["daily"]["detail"]
    assert checks["intraday"]["status"] == "info"
    assert "5,35 * * * *" in checks["intraday"]["detail"]
    assert "40 * * * *" in checks["intraday"]["detail"]
    assert "1200 秒" in checks["intraday"]["detail"]
    assert "2026-10-06" in checks["intraday"]["detail"]
    assert result["intraday"]["sync"]["status"] == "succeeded"


def test_fill_all_route_is_removed_so_ranges_cannot_be_bulk_queued():
    engine = _engine()
    now = datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc)
    with engine.begin() as conn:
        conn.execute(insert(meta_kv), _ready_worker_meta())
    operations = AdminOperations(target_engine=engine, market_provider=object(), now=lambda: now)
    client = _admin_client(operations)

    response = client.post("/api/v1/admin/analysis-runs/all")

    assert response.status_code == 404
    assert list_requests(engine) == []


def test_d7_is_claimed_before_other_ranges():
    engine = _engine()
    submitted = datetime(2026, 10, 6, 1, 0)
    enqueue(engine, "d1", now=submitted)
    enqueue(engine, "d2", now=submitted)
    d7, _ = enqueue(engine, "d7", now=submitted)

    claimed = claim_next(engine, owner="worker-1", now=submitted + timedelta(seconds=1))

    assert claimed["id"] == d7["id"]
