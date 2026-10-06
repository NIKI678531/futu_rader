"""Database-backed read model and command seam for the admin pipeline page.

The web process never runs AI work and never talks to Airflow or the source
database. It reads the same product observations as the business pages and
enqueues a bounded request in Radar's own database for the worker to claim.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Mapping

from sqlalchemy import desc, func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from providers import get_provider
from providers.sentinel import MISSING
from core.top_products import TOP_COMMENT_PRODUCT_LIMIT
from radar_db import make_engine
from radar_db import clock
from radar_db.analysis_requests import (
    RANGE_PRIORITY,
    enqueue,
    list_active_requests,
    list_requests,
    worker_health,
)
from radar_db.product_catalog import load_products
from radar_db.revisions import synthesis_generation_key
from radar_db.schema import (
    analysis_scope_jobs,
    annotation_jobs,
    meta_kv,
)


AI_LAYER_A = (
    {
        "id": "comment_product",
        "label": "评论产品与观点判定",
        "description": "判断评论属于哪只产品，并标注相关性、态度、主题和合规。",
        "locations": ["板块总览", "产品监控"],
    },
    {
        "id": "post_annotation",
        "label": "帖子类型与产品标注",
        "description": "识别帖子类型、涉及产品和方向。",
        "locations": ["KOL 监控", "KOL 详情", "官方账号"],
    },
    {
        "id": "kol_comment_opinion",
        "label": "KOL 评论观点",
        "description": "识别 KOL 在评论中提到的产品、方向和操作。",
        "locations": ["KOL 详情"],
    },
)

AI_LAYER_B = (
    ("hot_summary", "ETF 榜单热议总结", ["板块总览"]),
    ("summary", "产品舆情总结", ["产品监控"]),
    ("theme_label", "正负面观点主题", ["产品监控"]),
    ("neg_category", "负面舆情分类", ["产品监控"]),
    ("stage_unit", "阶段观点分类", ["产品监控"]),
    ("stage_summary", "阶段观点总结", ["产品监控"]),
    ("topic_label", "产品话题标签", ["产品监控"]),
    ("competitor_reason", "竞品关联原因", ["产品监控 · 关联竞品"]),
)


class AdminOpsError(RuntimeError):
    status_code = 502
    code = "admin_operation_failed"

    def __init__(self, message: str):
        super().__init__(message)
        self.blocked_reasons: list[str] = []


class AdminValidationError(AdminOpsError):
    status_code = 400
    code = "invalid_task_context"


class AdminUnavailable(AdminOpsError):
    status_code = 503
    code = "dependency_unavailable"


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


def sanitize_error(value: Any, *, limit: int = 500) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).split())[:limit] or None


class AdminOperations:
    """Deep module behind the admin page's two database-only workflows."""

    def __init__(self, *, target_engine, market_provider=None, now=None):
        self.target_engine = target_engine
        self.market_provider = market_provider or get_provider()
        self.now = now or (lambda: clock.now(timezone.utc))

    @classmethod
    def from_environment(cls):
        return cls(target_engine=make_engine(), market_provider=get_provider())

    @staticmethod
    def _layer_state(counts: Mapping[str, int]) -> str:
        if counts.get("claimed", 0):
            return "running"
        if counts.get("failed", 0) or counts.get("dead", 0):
            return "failed"
        if counts.get("pending", 0):
            return "waiting"
        active_total = sum(
            value for key, value in counts.items() if key != "superseded"
        )
        return "complete" if active_total else "not_started"

    def _ai_layers(
        self,
        conn,
        meta: Mapping[str, Any],
        range_key: str,
        products: list[dict[str, Any]],
    ) -> dict[str, Any]:
        progress = _json(
            meta.get(f"own_analysis_progress_{range_key}")
            or meta.get("own_analysis_progress"),
            {},
        )
        codes = [str(row["code"]) for row in products]
        progress_ranges = set(progress.get("ranges") or ())
        product_rows = progress.get("products") or {}
        scope_ids = []
        if range_key in progress_ranges:
            scope_ids = [
                product_rows[code].get("scope")
                for code in codes
                if isinstance(product_rows.get(code), Mapping)
                and product_rows[code].get("scope")
            ]

        task_counts = {
            row["id"]: {
                status: 0
                for status in ("pending", "claimed", "done", "failed", "dead", "superseded")
            }
            for row in AI_LAYER_A
        }
        latest_errors = {row["id"]: None for row in AI_LAYER_A}
        if scope_ids:
            linked_to_scope = select(analysis_scope_jobs.c.job_id).where(
                analysis_scope_jobs.c.scope_id.in_(scope_ids),
                analysis_scope_jobs.c.job_id == annotation_jobs.c.job_id,
            ).exists()
            scope_filter = or_(
                annotation_jobs.c.scope_id.in_(scope_ids), linked_to_scope
            )
            rows = conn.execute(
                select(annotation_jobs.c.task, annotation_jobs.c.status, func.count())
                .where(annotation_jobs.c.task.in_(tuple(task_counts)), scope_filter)
                .group_by(annotation_jobs.c.task, annotation_jobs.c.status)
            ).all()
            for task, status, count in rows:
                if task in task_counts and status in task_counts[task]:
                    task_counts[task][status] = int(count)
            for task in task_counts:
                latest_errors[task] = sanitize_error(
                    conn.execute(
                        select(annotation_jobs.c.last_error)
                        .where(
                            annotation_jobs.c.task == task,
                            scope_filter,
                            annotation_jobs.c.status.in_(("failed", "dead")),
                        )
                        .order_by(desc(annotation_jobs.c.updated_at))
                        .limit(1)
                    ).scalar()
                )

        layer_a = [
            {
                **definition,
                "status": self._layer_state(task_counts[definition["id"]]),
                "counts": task_counts[definition["id"]],
                "error": latest_errors[definition["id"]],
            }
            for definition in AI_LAYER_A
        ]

        anchor = str(meta.get("anchor") or "")
        # Readers publish a Layer-B generation through ``synth_current_*``.
        # That marker exists for generated text, low-sample results and
        # intentional empty results alike. Counting only synthesis_outputs rows
        # leaves valid empty generations looking "not started" forever.
        published_pairs = {kind: set() for kind, _label, _locations in AI_LAYER_B}
        if anchor:
            anchor_prefix = anchor + "|"
            for kind in published_pairs:
                for code in codes:
                    generation = meta.get(
                        synthesis_generation_key(code, range_key, kind)
                    )
                    if isinstance(generation, str) and generation.startswith(
                        anchor_prefix
                    ):
                        published_pairs[kind].add(code)

        dirty_codes = {
            key[len("synth_dirty_") : -len("_" + range_key)]
            for key, value in meta.items()
            if key.startswith("synth_dirty_")
            and key.endswith("_" + range_key)
            and value == "1"
        } & set(codes)
        analysis_running = progress.get("status") == "running" and range_key in progress_ranges
        expected = len(codes)
        layer_b = []
        for kind, label, locations in AI_LAYER_B:
            produced = len(published_pairs[kind])
            if dirty_codes:
                status = "running" if analysis_running else "waiting"
            elif expected and produced >= expected:
                status = "complete"
            elif produced:
                status = "partial"
            else:
                status = "not_started"
            layer_b.append(
                {
                    "id": kind,
                    "label": label,
                    "locations": locations,
                    "status": status,
                    "producedPairs": produced,
                    "expectedPairs": expected,
                    "dirtyPairs": len(dirty_codes),
                }
            )

        selected_progress = {
            code: product_rows.get(code, {})
            for code in codes
            if isinstance(product_rows.get(code), Mapping)
        }
        completed_products = sum(
            bool(row.get("complete")) for row in selected_progress.values()
        )
        current_code = next(
            (
                code
                for code in codes
                if code in selected_progress
                and not selected_progress[code].get("complete")
            ),
            None,
        )
        active_jobs = sum(
            counts.get("pending", 0) + counts.get("claimed", 0)
            for counts in task_counts.values()
        )
        completed_jobs = sum(counts.get("done", 0) for counts in task_counts.values())
        failed_jobs = sum(
            counts.get("failed", 0) + counts.get("dead", 0)
            for counts in task_counts.values()
        )
        total_jobs = completed_jobs + active_jobs + failed_jobs
        if expected:
            percent = round(completed_products / expected * 100)
        else:
            percent = 100

        return {
            "anchor": anchor or None,
            "range": range_key,
            "productCount": len(codes),
            "dirtyPairs": len(dirty_codes),
            "layerA": layer_a,
            "layerB": layer_b,
            "progress": {
                "status": progress.get("status") if range_key in progress_ranges else "not_started",
                "completedProducts": completed_products,
                "totalProducts": expected,
                "percent": percent,
                "currentCode": current_code,
                "completedJobs": completed_jobs,
                "activeJobs": active_jobs,
                "failedJobs": failed_jobs,
                "totalJobs": total_jobs,
                "updatedAt": progress.get("updatedAt"),
            },
        }

    @staticmethod
    def _comment_rows(pool_data: Any, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        observed = {
            str(row.get("code")): row
            for row in ((pool_data or {}).get("list") or [])
            if isinstance(row, Mapping) and row.get("code") is not None
        }
        rows = []
        for product in products:
            code = str(product["code"])
            item = observed.get(code)
            funnel = item.get("commentFunnel") if item else None
            page_count = item.get("comments") if item else None
            if page_count is None:
                page_state, page_label = "unavailable", "暂不可用"
            elif page_count > 0:
                page_state, page_label = "shown", f"有评论 · {page_count:,} 条"
            else:
                page_state, page_label = "zero", "0 条"
            rows.append(
                {
                    "code": code,
                    "name": product.get("name"),
                    "ownership": product.get("ownership"),
                    "rawPlatformCount": funnel.get("rawPlatformCount") if funnel else None,
                    "platformCount": funnel.get("platformCount") if funnel else None,
                    "parsedCount": funnel.get("parsedCount") if funnel else None,
                    "ruleEligibleCount": funnel.get("ruleEligibleCount") if funnel else None,
                    "aiCompletedCount": funnel.get("aiCompletedCount") if funnel else None,
                    "relevantCount": funnel.get("relevantCount") if funnel else None,
                    "pageCommentCount": page_count,
                    "pageCommentState": page_state,
                    "pageCommentLabel": page_label,
                }
            )
        rows.sort(
            key=lambda row: (
                row["pageCommentCount"] is None,
                -(row["pageCommentCount"] or 0),
                row["code"],
            )
        )
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
        return rows

    @staticmethod
    def _range_states(
        runs: list[dict[str, Any]], active_runs: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        by_range: dict[str, list[dict[str, Any]]] = {
            key: [] for key in RANGE_PRIORITY
        }
        for run in runs:
            if run.get("range") in by_range:
                by_range[run["range"]].append(run)
        active_by_range = {run["range"]: run for run in active_runs}
        return [
            {
                "key": key,
                "priority": priority,
                "activeRun": active_by_range.get(key),
                "lastRun": by_range[key][0] if by_range[key] else None,
            }
            for key, priority in RANGE_PRIORITY.items()
        ]

    def pipeline(self, range_key: str) -> dict[str, Any]:
        if range_key not in RANGE_PRIORITY:
            raise AdminValidationError("range 只允许 d1、d2、d7、d14 或 d30")
        try:
            refresh_provider = getattr(self.market_provider, "refresh", None)
            if callable(refresh_provider):
                refresh_provider()
            products = load_products(engine=self.target_engine)
            pool_data = self.market_provider.pool(range_key)
            if pool_data is MISSING:
                raise AdminValidationError("不支持这个时间区间")
            comment_rows = self._comment_rows(pool_data, products)
            top_codes = [
                row["code"]
                for row in comment_rows
                if row["pageCommentCount"] is not None
            ][:TOP_COMMENT_PRODUCT_LIMIT]
            products_by_code = {str(product["code"]): product for product in products}
            selected_products = [
                products_by_code[code] for code in top_codes if code in products_by_code
            ]
            with self.target_engine.connect() as conn:
                meta = dict(conn.execute(select(meta_kv.c.k, meta_kv.c.v)).all())
                layers = self._ai_layers(conn, meta, range_key, selected_products)
            runs = list_requests(self.target_engine, limit=20)
            active_runs = list_active_requests(self.target_engine)
        except AdminOpsError:
            raise
        except (SQLAlchemyError, OSError, RuntimeError, ValueError) as exc:
            raise AdminUnavailable("Radar 数据库暂时不可用") from exc
        observed_at = self.now()
        return {
            "generatedAt": _iso(observed_at),
            "anchor": meta.get("anchor"),
            "selectedRange": range_key,
            "comments": comment_rows,
            "commentDisplayLimit": TOP_COMMENT_PRODUCT_LIMIT,
            "analyzedProductCodes": top_codes,
            "layers": layers,
            "worker": worker_health(meta, now=observed_at),
            "ranges": self._range_states(runs, active_runs),
            "recentRuns": runs,
        }

    def enqueue_analysis(self, range_key: str) -> dict[str, Any]:
        if range_key not in RANGE_PRIORITY:
            raise AdminValidationError("range 只允许 d1、d2、d7、d14 或 d30")
        try:
            with self.target_engine.connect() as conn:
                meta = dict(
                    conn.execute(
                        select(meta_kv.c.k, meta_kv.c.v).where(
                            meta_kv.c.k.in_((
                                "ai_analysis_worker_heartbeat",
                                "ai_analysis_worker_readiness",
                            ))
                        )
                    ).all()
                )
            health = worker_health(meta, now=self.now())
            if not health["ready"]:
                raise AdminUnavailable(health["error"])
            request, created = enqueue(self.target_engine, range_key)
        except AdminOpsError:
            raise
        except ValueError as exc:
            raise AdminValidationError(str(exc)) from exc
        except SQLAlchemyError as exc:
            raise AdminUnavailable("Radar 数据库暂时不可用") from exc
        return {"run": request, "created": created}
