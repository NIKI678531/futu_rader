"""Token-protected operational endpoints for the independent admin page."""

import os
from functools import lru_cache, wraps

from flask import current_app, jsonify, request

from core.admin_ops import (
    AdminOperations,
    AdminOpsError,
    constant_time_token_matches,
)
from core.envelope import respond

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


def admin_token_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        expected = os.getenv("ADMIN_API_TOKEN")
        if not expected:
            return _error(503, "管理接口尚未配置", code="admin_not_configured")
        if not constant_time_token_matches(expected, request.headers.get("X-Admin-Token")):
            return _error(401, "管理令牌无效", code="admin_unauthorized")
        return view(*args, **kwargs)

    return wrapped


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


@v1_bp.get("/admin/data-sources")
@admin_token_required
def data_sources():
    return _run(lambda: respond(_operations().data_sources()))


@v1_bp.get("/admin/tasks")
@admin_token_required
def tasks():
    return _run(lambda: respond(_operations().tasks()))


@v1_bp.get("/admin/job-runs")
@admin_token_required
def job_runs():
    task = request.args.get("task") or None
    if task not in (None, "sync", "analyze"):
        return _error(400, "task 只允许 sync 或 analyze", code="invalid_task")
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        return _error(400, "limit 必须是整数", code="invalid_limit")
    if not 1 <= limit <= 200:
        return _error(400, "limit 必须在 1 到 200 之间", code="invalid_limit")
    return _run(lambda: respond(_operations().job_runs(task, limit)))


@v1_bp.post("/admin/tasks/sync/run")
@admin_token_required
def run_sync():
    result = _run(lambda: _operations().trigger("sync"))
    if isinstance(result, tuple):
        return result
    response, _ = respond(result)
    return response, 202


@v1_bp.post("/admin/tasks/analyze/run")
@admin_token_required
def run_analyze():
    result = _run(lambda: _operations().trigger("analyze"))
    if isinstance(result, tuple):
        return result
    response, _ = respond(result)
    return response, 202

