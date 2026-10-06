"""Database-only endpoints for the internal pipeline management page."""

from functools import lru_cache

from flask import current_app, jsonify, request

from core.admin_ops import AdminOperations, AdminOpsError
from core.envelope import respond
from radar_db.product_catalog import catalog_status, review_candidate

from . import v1_bp


def _error(status, message, *, code=None, blocked_reasons=None):
    payload = {
        "error": {
            "code": status,
            "type": code or "admin_error",
            "message": message,
        }
    }
    if blocked_reasons:
        payload["error"]["blockedReasons"] = blocked_reasons
    return jsonify(payload), status


@lru_cache(maxsize=1)
def _environment_operations():
    return AdminOperations.from_environment()


def _operations():
    factory = current_app.config.get("ADMIN_OPERATIONS_FACTORY")
    return factory() if factory else _environment_operations()


def _run(callable_):
    try:
        return callable_()
    except AdminOpsError as exc:
        return _error(
            exc.status_code,
            str(exc),
            code=exc.code,
            blocked_reasons=exc.blocked_reasons,
        )


@v1_bp.get("/admin/pipeline")
def pipeline():
    range_key = request.args.get("range", "d1")
    return _run(lambda: respond(_operations().pipeline(range_key)))


@v1_bp.post("/admin/analysis-runs")
def create_analysis_run():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {"range"}:
        return _error(
            400,
            "请求内容只允许 range",
            code="invalid_task_context",
        )
    result = _run(lambda: _operations().enqueue_analysis(body.get("range")))
    if isinstance(result, tuple):
        return result
    response, _ = respond(result)
    return response, 202


@v1_bp.post("/admin/analysis-runs/all")
def create_all_analysis_runs():
    result = _run(lambda: _operations().enqueue_all())
    if isinstance(result, tuple):
        return result
    response, _ = respond(result)
    return response, 202


# Product catalog operations remain available for other internal tooling even
# though the simplified pipeline page no longer renders that management UI.
@v1_bp.get("/admin/product-catalog")
def product_catalog_status():
    return _run(lambda: respond(catalog_status(_operations().target_engine)))


@v1_bp.post("/admin/product-catalog/<code>/review")
def product_catalog_review(code):
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or body.get("decision") not in {"approve", "reject"}:
        return _error(400, "decision 只允许 approve 或 reject", code="invalid_catalog_decision")
    try:
        result = review_candidate(
            _operations().target_engine,
            code,
            body["decision"],
            own_code=body.get("ownCode"),
            reviewer=str(body.get("reviewer") or "admin")[:120],
            note=str(body.get("note") or "")[:2000] or None,
        )
    except KeyError:
        return _error(404, "目录候选不存在或已完成审核", code="catalog_candidate_not_found")
    except ValueError as exc:
        return _error(400, str(exc), code="invalid_catalog_mapping")
    return respond(result)
