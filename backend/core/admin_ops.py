"""Admin-only operational view of the collection, sync and AI pipeline.

The public Flask process never runs a long-lived job.  This module is the
single seam around the two external control planes instead:

* MarketInsight is read-only and is used to choose an exact, verified source
  receipt.
* Airflow is the only process allowed to start sync or analysis work.

Keeping those details here makes the HTTP routes deliberately boring and,
more importantly, prevents a future admin screen from growing a shell-command
escape hatch.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from sqlalchemy import MetaData, Table, and_, create_engine, desc, func, select

from radar_db import make_engine
from radar_db.comment_routes import is_ready as comment_routes_ready
from radar_db.product_catalog import load_products
from radar_db.schema import ai_daily_budget, collector_checkpoints, ingestion_runs, meta_kv


TASKS = {
    "sync": {
        "label": "同步最新完整批次",
        "dag_id_env": "AIRFLOW_SYNC_DAG_ID",
        "dag_id": "futu_radar_sync",
        "schedule": "上游 comments_all 完整批次到达后",
    },
    "analyze": {
        "label": "分析当前完整日期",
        "dag_id_env": "AIRFLOW_ANALYZE_DAG_ID",
        "dag_id": "futu_radar_ai_analysis",
        "schedule": "工作日 20:30（香港时间）",
    },
}
ACTIVE_RUN_STATES = frozenset({"queued", "running"})
_SECRET_RE = re.compile(
    r"(?i)(password|passwd|pwd|token|api[_-]?key|secret)(\s*[=:]\s*)[^\s,;&]+"
)
_URL_USERINFO_RE = re.compile(r"([a-z][a-z0-9+.-]*://)[^/@\s]+@", re.I)


class AdminOpsError(RuntimeError):
    status_code = 502
    code = "admin_operation_failed"

    def __init__(self, message: str, *, blocked_reasons: list[str] | None = None):
        super().__init__(message)
        self.blocked_reasons = blocked_reasons or []


class AdminConflict(AdminOpsError):
    status_code = 409
    code = "task_blocked"


class AdminUnavailable(AdminOpsError):
    status_code = 503
    code = "dependency_unavailable"


def configured_symbols_fingerprint(codes: list[str]) -> str:
    """Fingerprint required by the upstream ``comments_all`` receipt contract."""

    material = "\n".join(sorted(set(str(code) for code in codes))).encode("utf-8")
    return "sha256:" + hashlib.sha256(material).hexdigest()


def constant_time_token_matches(expected: str | None, supplied: str | None) -> bool:
    expected_bytes = (expected or "").encode("utf-8")
    supplied_bytes = (supplied or "").encode("utf-8")
    return bool(expected_bytes) and hmac.compare_digest(expected_bytes, supplied_bytes)


def sanitize_error(value: Any, *, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    text = _URL_USERINFO_RE.sub(r"\1<redacted>@", text)
    text = _SECRET_RE.sub(r"\1\2<redacted>", text)
    return text[:limit] or None


def _json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _row_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row._mapping if hasattr(row, "_mapping") else row)


class MarketInsightStatusAdapter:
    """Read-only adapter for the upstream collection receipt table."""

    REQUIRED_COLUMNS = {
        "run_id",
        "collection_kind",
        "status",
        "complete_through",
        "configured_symbol_count",
        "configured_symbols_fingerprint",
        "attempted_symbol_count",
        "succeeded_symbol_count",
    }

    def __init__(
        self,
        database_url: str | None = None,
        *,
        engine=None,
        connect_timeout: int = 5,
    ):
        self.database_url = (database_url or "").strip()
        self._engine = engine
        self.connect_timeout = connect_timeout

    @property
    def configured(self) -> bool:
        return self._engine is not None or bool(self.database_url)

    def _get_engine(self):
        if self._engine is None:
            kwargs = {"future": True, "pool_pre_ping": True}
            if self.database_url.startswith("mysql"):
                kwargs["connect_args"] = {"connect_timeout": self.connect_timeout}
            self._engine = create_engine(self.database_url, **kwargs)
        return self._engine

    def status(self) -> dict[str, Any]:
        if not self.configured:
            return {
                "configured": False,
                "available": False,
                "latestRun": None,
                "latestCompleteRun": None,
                "error": "未配置 MarketInsight 只读连接",
            }
        try:
            engine = self._get_engine()
            table = Table("futu_comments_collection_runs", MetaData(), autoload_with=engine)
            missing = sorted(self.REQUIRED_COLUMNS - set(table.c.keys()))
            if missing:
                raise RuntimeError("collection receipt is missing required columns")
            order_columns = [
                table.c[name]
                for name in ("finished_at", "scheduled_for", "started_at")
                if name in table.c
            ]
            order_by = [desc(column) for column in order_columns] or [desc(table.c.run_id)]
            pool_codes = [str(product["code"]) for product in load_products()]
            proof = and_(
                table.c.collection_kind == "comments_all",
                table.c.status == "succeeded",
                table.c.complete_through.is_not(None),
                table.c.configured_symbol_count == len(pool_codes),
                table.c.configured_symbols_fingerprint
                == configured_symbols_fingerprint(pool_codes),
                table.c.attempted_symbol_count == table.c.configured_symbol_count,
                table.c.succeeded_symbol_count == table.c.configured_symbol_count,
            )
            with engine.connect() as conn:
                latest = conn.execute(
                    select(table)
                    .where(table.c.collection_kind == "comments_all")
                    .order_by(*order_by)
                    .limit(1)
                ).first()
                complete = conn.execute(
                    select(table)
                    .where(proof)
                    .order_by(desc(table.c.complete_through), *order_by)
                    .limit(1)
                ).first()
            return {
                "configured": True,
                "available": True,
                "latestRun": self._serialize_run(_row_dict(latest)),
                "latestCompleteRun": self._serialize_run(_row_dict(complete)),
                "error": None,
            }
        except Exception:
            # Connection strings and database-driver messages can contain credentials.
            return {
                "configured": True,
                "available": False,
                "latestRun": None,
                "latestCompleteRun": None,
                "error": "MarketInsight 状态暂时不可用",
            }

    @staticmethod
    def _serialize_run(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "runId": str(row.get("run_id")),
            "status": row.get("status"),
            "collectionKind": row.get("collection_kind"),
            "completeThrough": _iso(row.get("complete_through")),
            "scheduledFor": _iso(row.get("scheduled_for")),
            "startedAt": _iso(row.get("started_at")),
            "finishedAt": _iso(row.get("finished_at")),
            "configuredSymbolCount": row.get("configured_symbol_count"),
            "attemptedSymbolCount": row.get("attempted_symbol_count"),
            "succeededSymbolCount": row.get("succeeded_symbol_count"),
            "error": sanitize_error(row.get("error_summary")),
        }


class AirflowAdapter:
    """Small Airflow stable-REST adapter; credentials never leave this object."""

    def __init__(
        self,
        base_url: str | None,
        token: str | None,
        *,
        timeout: float = 5.0,
        opener=urlopen,
    ):
        self.base_url = (base_url or "").strip().rstrip("/")
        self.token = (token or "").strip()
        self.timeout = timeout
        self._opener = opener

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if not self.configured:
            raise AdminUnavailable("Airflow API 未配置")
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/v1{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise AdminUnavailable("Airflow API 鉴权失败") from None
            raise AdminUnavailable(f"Airflow API 请求失败（HTTP {exc.code}）") from None
        except (URLError, TimeoutError, OSError):
            raise AdminUnavailable("Airflow API 暂时不可用") from None
        except (UnicodeDecodeError, ValueError):
            raise AdminUnavailable("Airflow API 返回了无法识别的响应") from None

    def snapshot(self, dag_id: str, *, run_limit: int = 20) -> dict[str, Any]:
        if not self.configured:
            return {
                "configured": False,
                "available": False,
                "isPaused": None,
                "nextRunAt": None,
                "runs": [],
                "error": "Airflow API 未配置",
            }
        try:
            dag = self._request("GET", f"/dags/{quote(dag_id, safe='')}")
            params = urlencode({"limit": run_limit, "order_by": "-execution_date"})
            payload = self._request(
                "GET", f"/dags/{quote(dag_id, safe='')}/dagRuns?{params}"
            )
            return {
                "configured": True,
                "available": True,
                "isPaused": dag.get("is_paused"),
                "nextRunAt": dag.get("next_dagrun") or dag.get("next_dagrun_create_after"),
                "runs": [self._serialize_run(row) for row in payload.get("dag_runs", [])],
                "error": None,
            }
        except AdminUnavailable as exc:
            return {
                "configured": True,
                "available": False,
                "isPaused": None,
                "nextRunAt": None,
                "runs": [],
                "error": sanitize_error(exc),
            }

    def trigger(self, dag_id: str, *, dag_run_id: str, conf: dict[str, Any]) -> dict:
        result = self._request(
            "POST",
            f"/dags/{quote(dag_id, safe='')}/dagRuns",
            {"dag_run_id": dag_run_id, "conf": conf},
        )
        return self._serialize_run(result)

    @staticmethod
    def _serialize_run(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "dagRunId": row.get("dag_run_id") or row.get("run_id"),
            "status": row.get("state") or "unknown",
            "logicalDate": row.get("logical_date") or row.get("execution_date"),
            "startedAt": row.get("start_date"),
            "finishedAt": row.get("end_date"),
            "conf": row.get("conf") if isinstance(row.get("conf"), dict) else {},
            "error": sanitize_error(row.get("note")),
        }


@dataclass(frozen=True)
class TaskDefinition:
    name: str
    label: str
    dag_id: str
    schedule: str


class AdminOperations:
    def __init__(self, *, target_engine, source, airflow, environment=None, now=None):
        self.target_engine = target_engine
        self.source = source
        self.airflow = airflow
        self.environment = environment if environment is not None else os.environ
        self.now = now or (lambda: datetime.now(timezone.utc))

    @classmethod
    def from_environment(cls):
        timeout = float(os.getenv("ADMIN_HTTP_TIMEOUT_SECONDS", "5"))
        return cls(
            target_engine=make_engine(),
            source=MarketInsightStatusAdapter(
                os.getenv("MARKET_INSIGHT_DATABASE_URL"),
                connect_timeout=max(1, int(timeout)),
            ),
            airflow=AirflowAdapter(
                os.getenv("AIRFLOW_API_BASE_URL"),
                os.getenv("AIRFLOW_API_TOKEN"),
                timeout=timeout,
            ),
        )

    def _definition(self, name: str) -> TaskDefinition:
        try:
            spec = TASKS[name]
        except KeyError:
            raise AdminOpsError("未知任务；只允许 sync 或 analyze") from None
        return TaskDefinition(
            name=name,
            label=spec["label"],
            dag_id=self.environment.get(spec["dag_id_env"], spec["dag_id"]),
            schedule=spec["schedule"],
        )

    def _target_status(self) -> dict[str, Any]:
        try:
            with self.target_engine.connect() as conn:
                meta = dict(conn.execute(select(meta_kv.c.k, meta_kv.c.v)).all())
                latest = conn.execute(
                    select(ingestion_runs).order_by(desc(ingestion_runs.c.started_at)).limit(1)
                ).first()
                latest_success = conn.execute(
                    select(ingestion_runs)
                    .where(
                        ingestion_runs.c.status == "succeeded",
                        ingestion_runs.c.complete_through.is_not(None),
                    )
                    .order_by(
                        desc(ingestion_runs.c.complete_through),
                        desc(ingestion_runs.c.finished_at),
                    )
                    .limit(1)
                ).first()
                checkpoints = conn.execute(
                    select(
                        func.count().label("count"),
                        func.max(collector_checkpoints.c.updated_at).label("updated_at"),
                    )
                ).first()
                budget = conn.execute(
                    select(ai_daily_budget)
                    .order_by(desc(ai_daily_budget.c.budget_date))
                    .limit(1)
                ).first()
            latest_row = _row_dict(latest)
            success_row = _row_dict(latest_success)
            budget_row = _row_dict(budget)
            progress = _json(meta.get("own_analysis_progress"), {})
            ready = comment_routes_ready(meta)
            return {
                "configured": True,
                "available": True,
                "anchor": meta.get("anchor"),
                "anchorUpdatedAt": meta.get("anchor_ts"),
                "sourceCompleteThrough": _iso(
                    success_row.get("complete_through") if success_row else None
                ),
                "latestSync": self._serialize_ingestion(latest_row),
                "lastSuccessfulSync": self._serialize_ingestion(success_row),
                "checkpointCount": int(checkpoints.count or 0) if checkpoints else 0,
                "checkpointUpdatedAt": _iso(checkpoints.updated_at if checkpoints else None),
                "ai": {
                    "routingReady": ready,
                    "commentRouteVersion": meta.get("comment_route_version"),
                    "productPoolDigest": meta.get("comment_route_pool_digest"),
                    "analysisAnchor": progress.get("anchor"),
                    "status": progress.get("status") or "not_started",
                    "completedProducts": progress.get("completedProducts")
                    or progress.get("completed")
                    or 0,
                    "totalProducts": progress.get("totalProducts")
                    or progress.get("total")
                    or 0,
                    "error": sanitize_error(
                        progress.get("failureReason") or progress.get("error")
                    ),
                    "budget": None
                    if not budget_row
                    else {
                        "date": _iso(budget_row.get("budget_date")),
                        "used": budget_row.get("requests_used"),
                        "limit": budget_row.get("request_limit"),
                        "status": budget_row.get("status"),
                    },
                },
                "error": None,
            }
        except Exception:
            return {
                "configured": True,
                "available": False,
                "anchor": None,
                "sourceCompleteThrough": None,
                "latestSync": None,
                "lastSuccessfulSync": None,
                "checkpointCount": 0,
                "checkpointUpdatedAt": None,
                "ai": {
                    "routingReady": False,
                    "commentRouteVersion": None,
                    "productPoolDigest": None,
                    "analysisAnchor": None,
                    "status": "unavailable",
                    "completedProducts": 0,
                    "totalProducts": 0,
                    "error": None,
                    "budget": None,
                },
                "error": "Radar 数据库状态暂时不可用",
            }

    @staticmethod
    def _serialize_ingestion(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "runId": row.get("run_id"),
            "sourceRunId": row.get("source_run_id"),
            "status": row.get("status"),
            "sourceKind": row.get("source_kind"),
            "startedAt": _iso(row.get("started_at")),
            "finishedAt": _iso(row.get("finished_at")),
            "completeThrough": _iso(row.get("complete_through")),
            "counts": _json(row.get("counts_json"), {}),
            "error": sanitize_error(row.get("error_summary")),
        }

    def data_sources(self) -> dict[str, Any]:
        upstream = self.source.status()
        radar = self._target_status()
        complete = upstream.get("latestCompleteRun") or {}
        upstream_date = complete.get("completeThrough")
        radar_date = radar.get("sourceCompleteThrough") or radar.get("anchor")
        waiting = bool(upstream_date and (not radar_date or upstream_date > radar_date))
        if not upstream.get("available"):
            chain_state = "upstream_unavailable"
        elif not radar.get("available"):
            chain_state = "radar_unavailable"
        elif waiting:
            chain_state = "waiting_for_sync"
        elif (radar.get("latestSync") or {}).get("status") == "failed":
            chain_state = "sync_failed"
        else:
            chain_state = "current"
        return {
            "generatedAt": _iso(self.now()),
            "chainState": chain_state,
            "waitingForSync": waiting,
            "upstream": upstream,
            "radar": radar,
            "ai": radar.get("ai", {}),
        }

    def _gate_reasons(self, name: str, sources: dict[str, Any]) -> list[str]:
        upstream = sources["upstream"]
        radar = sources["radar"]
        reasons: list[str] = []
        if name == "sync":
            if not upstream.get("configured"):
                reasons.append("尚未配置 MarketInsight 只读连接")
            elif not upstream.get("available"):
                reasons.append("MarketInsight 状态不可用")
            elif not upstream.get("latestCompleteRun"):
                reasons.append("上游还没有通过完整性校验的 comments_all 批次")
            return reasons

        if not radar.get("available"):
            return ["Radar 数据库状态不可用"]
        if not radar.get("ai", {}).get("routingReady"):
            reasons.append("评论到产品的 AI 路由历史回填尚未激活")
        anchor = radar.get("anchor")
        complete = radar.get("sourceCompleteThrough")
        if not anchor or not complete or anchor != complete:
            reasons.append("当前 anchor 尚未完成同步")
        if sources.get("waitingForSync"):
            reasons.append("上游存在更新的完整批次，请先同步")
        if self.environment.get("AI_DATA_GOVERNANCE_APPROVED", "").lower() != "true":
            reasons.append("AI 数据治理尚未批准")
        reasons.extend(self._report_gate_reasons(radar.get("ai", {})))
        return reasons

    def _read_report(self, name: str) -> dict[str, Any] | None:
        value = self.environment.get(name, "").strip()
        if not value:
            return None
        try:
            payload = json.loads(value)
        except (TypeError, ValueError):
            try:
                payload = json.loads(Path(value).read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError):
                return None
        return payload if isinstance(payload, dict) else None

    def _report_gate_reasons(self, ai: Mapping[str, Any]) -> list[str]:
        """Cheap fail-closed mirror of the worker's full release validation.

        The worker remains authoritative and recomputes confusion-matrix and
        fixed-regression evidence.  This check prevents a clearly absent or
        stale report from enabling the admin button in the first place.
        """

        model = self.environment.get("AI_PRIMARY_MODEL", "").strip()
        if not model:
            return ["未配置当前 AI 模型"]
        try:
            batch_size = int(self.environment.get("AI_MICRO_BATCH_SIZE", "5"))
            max_input_tokens = int(self.environment.get("AI_MAX_INPUT_TOKENS", "8000"))
            max_payload_bytes = int(self.environment.get("AI_MAX_PAYLOAD_BYTES", "12288"))
            max_output_tokens = int(self.environment.get("AI_MAX_OUTPUT_TOKENS", "8192"))
        except (TypeError, ValueError):
            return ["AI 批处理参数无效"]
        expected = {
            "promptVersion": self.environment.get(
                "AI_PROMPT_VERSION", "comment-product-v3"
            ).strip(),
            "schemaVersion": self.environment.get("AI_SCHEMA_VERSION", "v2").strip(),
            "taxonomyVersion": self.environment.get("AI_TAXONOMY_VERSION", "v2").strip(),
            "commentRouteVersion": ai.get("commentRouteVersion"),
            "productPoolDigest": ai.get("productPoolDigest"),
        }
        calibration = self._read_report("AI_CALIBRATION_REPORT")
        if calibration is None:
            calibration_ok = False
        else:
            policy = calibration.get("policy") or {}
            calibration_expected = {
                "model": model,
                **expected,
                "batchSize": batch_size,
                "maxInputTokens": max_input_tokens,
                "maxPayloadBytes": max_payload_bytes,
                "maxOutputTokens": max_output_tokens,
            }
            calibration_ok = calibration.get("batchGatePassed") is True and all(
                policy.get(key) == value and value
                for key, value in calibration_expected.items()
            )
        quality = self._read_report("AI_QUALITY_REPORT")
        if quality is None:
            quality_ok = False
        else:
            policy = quality.get("policy") or {}
            quality_expected = {"requestedModel": model, **expected}
            quality_ok = (
                quality.get("reportType") == "comment-relevance-human-gold-v1"
                and quality.get("sample_source") == "llm"
                and isinstance(quality.get("n"), int)
                and quality["n"] >= 400
                and quality.get("qualityGate", {}).get("passed") is True
                and all(policy.get(key) == value and value for key, value in quality_expected.items())
            )
        reasons = []
        if not calibration_ok:
            reasons.append("校准报告缺失、未通过或与当前模型/Prompt/schema/taxonomy/产品池不匹配")
        if not quality_ok:
            reasons.append("400 条人工金标报告缺失、未通过或与当前策略不匹配")
        return reasons

    def _task_snapshot(self, name: str, sources: dict[str, Any]) -> dict[str, Any]:
        definition = self._definition(name)
        airflow = self.airflow.snapshot(definition.dag_id)
        runs = airflow.get("runs", [])
        active = [run for run in runs if run.get("status") in ACTIVE_RUN_STATES]
        reasons = self._gate_reasons(name, sources)
        if not airflow.get("configured"):
            reasons.append("Airflow API 未配置")
        elif not airflow.get("available"):
            reasons.append("Airflow API 暂时不可用")
        elif airflow.get("isPaused") is True:
            reasons.append("DAG 当前已暂停，请先在 Airflow 完成验收并启用")
        if active:
            reasons.append("已有同类任务正在运行")
        return {
            "id": name,
            "label": definition.label,
            "dagId": definition.dag_id,
            "schedule": definition.schedule,
            "isPaused": airflow.get("isPaused"),
            "isRunning": bool(active),
            "lastRun": runs[0] if runs else None,
            "nextRunAt": airflow.get("nextRunAt"),
            "canRun": not reasons,
            "blockedReasons": reasons,
            "airflowAvailable": airflow.get("available", False),
            "error": airflow.get("error"),
        }

    def tasks(self) -> dict[str, Any]:
        sources = self.data_sources()
        return {
            "generatedAt": _iso(self.now()),
            "items": [self._task_snapshot(name, sources) for name in TASKS],
        }

    def job_runs(self, task: str | None, limit: int) -> dict[str, Any]:
        names = [task] if task else list(TASKS)
        items: list[dict[str, Any]] = []
        configured = self.airflow.configured
        available = configured
        errors = []
        for name in names:
            definition = self._definition(name)
            snapshot = self.airflow.snapshot(definition.dag_id, run_limit=limit)
            available = available and snapshot.get("available", False)
            if snapshot.get("error"):
                errors.append(snapshot["error"])
            for run in snapshot.get("runs", []):
                items.append({"task": name, **run})
        source_ids = {
            item.get("conf", {}).get("sourceRunId")
            for item in items
            if item.get("task") == "sync"
            and isinstance(item.get("conf"), dict)
            and item.get("conf", {}).get("sourceRunId")
        }
        if source_ids:
            try:
                with self.target_engine.connect() as conn:
                    rows = conn.execute(
                        select(
                            ingestion_runs.c.source_run_id,
                            ingestion_runs.c.error_summary,
                        ).where(ingestion_runs.c.source_run_id.in_(source_ids))
                    ).all()
                errors_by_source = {
                    source_run_id: sanitize_error(error)
                    for source_run_id, error in rows
                    if error
                }
                for item in items:
                    source_run_id = item.get("conf", {}).get("sourceRunId")
                    if source_run_id in errors_by_source:
                        item["error"] = errors_by_source[source_run_id]
            except Exception:
                # History stays useful when the target DB is briefly unavailable.
                pass
        items.sort(
            key=lambda row: row.get("startedAt") or row.get("logicalDate") or "", reverse=True
        )
        return {
            "configured": configured,
            "available": available,
            "items": items[:limit],
            "error": errors[0] if errors else None,
        }

    def trigger(self, name: str) -> dict[str, Any]:
        definition = self._definition(name)
        sources = self.data_sources()
        snapshot = self.airflow.snapshot(definition.dag_id)
        reasons = self._gate_reasons(name, sources)
        if not snapshot.get("configured"):
            reasons.append("Airflow API 未配置")
        elif not snapshot.get("available"):
            reasons.append("Airflow API 暂时不可用")
        elif snapshot.get("isPaused") is True:
            reasons.append("DAG 当前已暂停，请先在 Airflow 完成验收并启用")
        if any(run.get("status") in ACTIVE_RUN_STATES for run in snapshot.get("runs", [])):
            reasons.append("已有同类任务正在运行")
        if reasons:
            raise AdminConflict("任务当前不能启动", blocked_reasons=reasons)

        conf: dict[str, Any]
        if name == "sync":
            # Re-query immediately before submission.  The exact receipt is the
            # idempotency boundary; a date alone is deliberately insufficient.
            complete = self.source.status().get("latestCompleteRun")
            if not complete:
                raise AdminConflict(
                    "任务当前不能启动",
                    blocked_reasons=["上游完整批次在提交前已不可用，请刷新后重试"],
                )
            conf = {"sourceRunId": complete["runId"], "mode": "incremental"}
        else:
            conf = {"mode": "auto", "anchor": sources["radar"].get("anchor")}
        stamp = self.now().strftime("%Y%m%dT%H%M%SZ")
        dag_run_id = f"manual__{name}__{stamp}__{uuid.uuid4().hex[:8]}"
        return {
            "task": name,
            "run": self.airflow.trigger(
                definition.dag_id, dag_run_id=dag_run_id, conf=conf
            ),
        }

