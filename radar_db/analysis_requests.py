"""Durable queue shared by the admin web process and the AI worker."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import desc, insert, select, update
from sqlalchemy.exc import IntegrityError

from .schema import ai_analysis_requests, meta_kv


RANGE_PRIORITY = {"d1": 400, "d2": 300, "d7": 500, "d14": 200, "d30": 100, "mtd": 50}
REQUESTERS = ("admin", "schedule")
ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("succeeded", "partial", "failed")
WORKER_HEARTBEAT_KEY = "ai_analysis_worker_heartbeat"
WORKER_READINESS_KEY = "ai_analysis_worker_readiness"
WORKER_HEARTBEAT_STALE_SECONDS = 120


def _utcnow() -> datetime:
    return datetime.utcnow()


def _request_id(now: datetime) -> str:
    return f"ai-{now:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:12]}"


def _json(value: Any) -> Any:
    if not value:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def serialize(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.get("request_id"),
        "range": row.get("range_key"),
        "priority": row.get("priority"),
        "status": row.get("status"),
        "attempts": row.get("attempts", 0),
        "requestedAt": _iso(row.get("requested_at")),
        "startedAt": _iso(row.get("started_at")),
        "finishedAt": _iso(row.get("finished_at")),
        "summary": _json(row.get("result_json")),
        "error": row.get("error_summary"),
        "requestedBy": row.get("requested_by") or "admin",
    }


def _iso(value: Any) -> str | None:
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def record_worker_heartbeat(
    engine,
    *,
    now: datetime | str | None = None,
    readiness: Mapping[str, Any] | None = None,
) -> str:
    """Persist a cheap liveness signal for the admin page."""

    if isinstance(now, str):
        value = now
    else:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        value = current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    with engine.begin() as conn:
        values = {WORKER_HEARTBEAT_KEY: value}
        if readiness is not None:
            values[WORKER_READINESS_KEY] = json.dumps(
                dict(readiness), ensure_ascii=False
            )
        for key, item in values.items():
            updated = conn.execute(
                update(meta_kv).where(meta_kv.c.k == key).values(v=item)
            )
            if not updated.rowcount:
                conn.execute(insert(meta_kv).values(k=key, v=item))
    return value


def worker_health(
    metadata_values: Mapping[str, Any],
    *,
    now: datetime | None = None,
    stale_after_seconds: int = WORKER_HEARTBEAT_STALE_SECONDS,
) -> dict[str, Any]:
    """Return whether a queue consumer has reported in recently."""

    value = metadata_values.get(WORKER_HEARTBEAT_KEY)
    if not value:
        return {
            "online": False,
            "lastSeenAt": None,
            "ready": False,
            "error": "AI worker 未运行",
        }
    try:
        seen = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        age = (current.astimezone(timezone.utc) - seen.astimezone(timezone.utc)).total_seconds()
        online = age <= stale_after_seconds
    except (TypeError, ValueError):
        online = False
    readiness = _json(metadata_values.get(WORKER_READINESS_KEY)) or {}
    ready = bool(online and readiness.get("ready") is True)
    if not online:
        error = "AI worker 未运行"
    elif ready:
        error = None
    else:
        error = str(readiness.get("error") or "AI worker 尚未完成运行前检查")
    return {
        "online": online,
        "lastSeenAt": str(value),
        "ready": ready,
        "error": error,
    }


def enqueue(
    engine,
    range_key: str,
    *,
    requested_by: str = "admin",
    now: datetime | None = None,
) -> tuple[dict, bool]:
    """Enqueue one fixed range, returning ``(request, created)``.

    A nullable unique ``active_key`` makes duplicate active submissions safe
    across multiple web processes. Terminal rows clear the key and remain as
    audit history.
    """

    if range_key not in RANGE_PRIORITY:
        raise ValueError("range 只允许 d1、d2、d7、d14、d30 或 mtd")
    if requested_by not in REQUESTERS:
        raise ValueError("requested_by 只允许 admin 或 schedule")
    now = now or _utcnow()
    values = {
        "request_id": _request_id(now),
        "range_key": range_key,
        "priority": RANGE_PRIORITY[range_key],
        "status": "queued",
        "active_key": range_key,
        "attempts": 0,
        "requested_at": now,
        "requested_by": requested_by,
    }
    try:
        with engine.begin() as conn:
            conn.execute(insert(ai_analysis_requests).values(**values))
    except IntegrityError:
        # Read the winner in a fresh transaction. This matters on MySQL's
        # REPEATABLE READ: reusing a transaction that first saw no row can keep
        # seeing the old snapshot after another process wins the unique-key race.
        with engine.connect() as conn:
            existing = conn.execute(
                select(ai_analysis_requests).where(
                    ai_analysis_requests.c.active_key == range_key
                )
            ).mappings().first()
        if existing:
            return serialize(existing), False
        raise
    return serialize(values), True


def list_requests(engine, *, limit: int = 20) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    with engine.connect() as conn:
        rows = conn.execute(
            select(ai_analysis_requests)
            .order_by(desc(ai_analysis_requests.c.requested_at), desc(ai_analysis_requests.c.request_id))
            .limit(limit)
        ).mappings().all()
    return [serialize(row) for row in rows]


def list_active_requests(engine) -> list[dict[str, Any]]:
    """Return every active row; active button state must not depend on history paging."""

    with engine.connect() as conn:
        rows = conn.execute(
            select(ai_analysis_requests)
            .where(ai_analysis_requests.c.status.in_(ACTIVE_STATUSES))
            .order_by(
                desc(ai_analysis_requests.c.priority),
                ai_analysis_requests.c.requested_at,
                ai_analysis_requests.c.request_id,
            )
        ).mappings().all()
    return [serialize(row) for row in rows]


def claim_next(
    engine,
    *,
    owner: str | None = None,
    now: datetime | None = None,
    lease_seconds: int = 300,
) -> dict[str, Any] | None:
    """Claim the highest-priority request and recover expired claims."""

    now = now or _utcnow()
    owner = owner or uuid.uuid4().hex
    lease_until = now + timedelta(seconds=lease_seconds)
    with engine.begin() as conn:
        conn.execute(
            update(ai_analysis_requests)
            .where(
                ai_analysis_requests.c.status == "running",
                ai_analysis_requests.c.lease_until.is_not(None),
                ai_analysis_requests.c.lease_until < now,
            )
            .values(
                status="queued",
                lease_owner=None,
                lease_until=None,
                error_summary="上次 worker 中断，已自动续跑",
            )
        )
        candidate = conn.execute(
            select(ai_analysis_requests.c.request_id)
            .where(ai_analysis_requests.c.status == "queued")
            .order_by(
                desc(ai_analysis_requests.c.priority),
                ai_analysis_requests.c.requested_at,
                ai_analysis_requests.c.request_id,
            )
            .limit(1)
        ).scalar_one_or_none()
        if candidate is None:
            return None
        claimed = conn.execute(
            update(ai_analysis_requests)
            .where(
                ai_analysis_requests.c.request_id == candidate,
                ai_analysis_requests.c.status == "queued",
            )
            .values(
                status="running",
                attempts=ai_analysis_requests.c.attempts + 1,
                started_at=now,
                finished_at=None,
                lease_owner=owner,
                lease_until=lease_until,
                error_summary=None,
            )
        )
        if not claimed.rowcount:
            return None
        row = conn.execute(
            select(ai_analysis_requests).where(
                ai_analysis_requests.c.request_id == candidate
            )
        ).mappings().one()
    return serialize(row)


def renew(engine, request_id: str, owner: str, *, seconds: int = 300) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            update(ai_analysis_requests)
            .where(
                ai_analysis_requests.c.request_id == request_id,
                ai_analysis_requests.c.status == "running",
                ai_analysis_requests.c.lease_owner == owner,
            )
            .values(lease_until=_utcnow() + timedelta(seconds=seconds))
        )
    return bool(result.rowcount)


def finish(
    engine,
    request_id: str,
    owner: str,
    status: str,
    *,
    summary: Mapping[str, Any] | None = None,
    error: str | None = None,
    now: datetime | None = None,
) -> bool:
    if status not in TERMINAL_STATUSES:
        raise ValueError("terminal status must be succeeded, partial, or failed")
    with engine.begin() as conn:
        result = conn.execute(
            update(ai_analysis_requests)
            .where(
                ai_analysis_requests.c.request_id == request_id,
                ai_analysis_requests.c.status == "running",
                ai_analysis_requests.c.lease_owner == owner,
            )
            .values(
                status=status,
                active_key=None,
                finished_at=now or _utcnow(),
                lease_owner=None,
                lease_until=None,
                result_json=json.dumps(summary, ensure_ascii=False, default=str)
                if summary is not None
                else None,
                error_summary=(" ".join(str(error).split())[:500] if error else None),
            )
        )
    return bool(result.rowcount)


def requeue(engine, request_id: str, owner: str, *, error: str | None = None) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            update(ai_analysis_requests)
            .where(
                ai_analysis_requests.c.request_id == request_id,
                ai_analysis_requests.c.status == "running",
                ai_analysis_requests.c.lease_owner == owner,
            )
            .values(
                status="queued",
                started_at=None,
                lease_owner=None,
                lease_until=None,
                error_summary=(" ".join(str(error).split())[:500] if error else None),
            )
        )
    return bool(result.rowcount)
