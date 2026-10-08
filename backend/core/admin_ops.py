"""Database-backed read model and command seam for the admin pipeline page.

The web process never runs AI work and never talks to Airflow or the source
database. It reads the same product observations as the business pages and
enqueues a bounded request in Radar's own database for the worker to claim.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Mapping

from sqlalchemy import desc, func, select
from sqlalchemy.exc import SQLAlchemyError

from providers import get_provider
from providers.sentinel import MISSING
from core.data_clock import analysis_range_anchor
from core.top_products import TOP_COMMENT_PRODUCT_LIMIT, analysis_products
from radar_db import make_engine
from radar_db import clock
from radar_db.analysis_requests import (
    RANGE_PRIORITY,
    WORKER_READINESS_KEY,
    enqueue,
    list_active_requests,
    list_requests,
    worker_health,
)
from radar_db.comment_filter import is_filter_ready, load_comment_filter_config
from radar_db.comment_routes import is_ready as comment_routes_ready
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


MISSING_CODE_LIMIT = 40
LAST_DAILY_RUN_KEY = "ai_daily_last_run"
LAST_INTRADAY_RUN_KEY = "ai_intraday_last_run"


def _dirty_codes(meta: Mapping[str, Any], range_key: str) -> set[str]:
    suffix = "_" + range_key
    return {
        key[len("synth_dirty_") : -len(suffix)]
        for key, value in meta.items()
        if key.startswith("synth_dirty_") and key.endswith(suffix) and value == "1"
    }


def layer_b_states(
    meta: Mapping[str, Any], codes: list[str], range_key: str, anchor: str
) -> dict[str, dict[str, list[str]]]:
    """Per kind: products whose published generation is fresh, stale or missing.

    ``synth_current_*`` marks generated text, low-sample results and intentional
    empty results alike, so it is the completion signal, not ``synthesis_outputs``.
    Pages still show a stale generation (dirty pair or older anchor) with a
    「待更新」badge; only ``missing`` products have nothing to show.
    """

    dirty = _dirty_codes(meta, range_key)
    out = {}
    for kind, _label, _locations in AI_LAYER_B:
        fresh, stale, missing = [], [], []
        for code in codes:
            generation = meta.get(synthesis_generation_key(code, range_key, kind))
            published, _, fingerprint = str(generation or "").partition("|")
            if not anchor or not fingerprint or not published or published > anchor:
                missing.append(code)
            elif published == anchor and code not in dirty:
                fresh.append(code)
            else:
                stale.append(code)
        out[kind] = {"fresh": fresh, "stale": stale, "missing": missing}
    return out


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


class AdminBusy(AdminOpsError):
    status_code = 409
    code = "analysis_in_progress"


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
            # Resolve the small set of relevant job ids first.  A correlated
            # EXISTS here made SQLite scan the whole annotation queue for each
            # admin-page refresh; on the production-sized local database that
            # turned a status read into a 10+ second query while AI was running.
            scoped_job_ids = {
                job_id
                for (job_id,) in conn.execute(
                    select(annotation_jobs.c.job_id)
                    .where(annotation_jobs.c.scope_id.in_(scope_ids))
                    .union(
                        select(analysis_scope_jobs.c.job_id).where(
                            analysis_scope_jobs.c.scope_id.in_(scope_ids)
                        )
                    )
                )
            }
            scope_filter = annotation_jobs.c.job_id.in_(scoped_job_ids)
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

        effective_anchor = analysis_range_anchor(
            self.target_engine,
            range_key,
            meta.get("anchor"),
            meta=meta,
        )
        anchor = effective_anchor.isoformat() if effective_anchor else ""
        states = layer_b_states(meta, codes, range_key, anchor)
        dirty_codes = _dirty_codes(meta, range_key) & set(codes)
        analysis_running = progress.get("status") == "running" and range_key in progress_ranges
        expected = len(codes)
        layer_b = []
        for kind, label, locations in AI_LAYER_B:
            fresh, stale, missing = (states[kind][state] for state in ("fresh", "stale", "missing"))
            if expected and len(fresh) == expected:
                status = "complete"
            elif analysis_running:
                status = "running"
            elif fresh or stale:
                status = "partial"
            else:
                status = "not_started"
            layer_b.append(
                {
                    "id": kind,
                    "label": label,
                    "locations": locations,
                    "status": status,
                    "producedPairs": len(fresh),
                    "stalePairs": len(stale),
                    "missingPairs": len(missing),
                    "expectedPairs": expected,
                    "dirtyPairs": len(dirty_codes),
                    "staleCodes": stale[:MISSING_CODE_LIMIT],
                    "missingCodes": missing[:MISSING_CODE_LIMIT],
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
            raise AdminValidationError("range 只允许 d0、d1、d2、d7、d14、d30 或 mtd")
        try:
            refresh_provider = getattr(self.market_provider, "refresh", None)
            if callable(refresh_provider):
                refresh_provider()
            products = load_products(engine=self.target_engine)
            # The management page only needs comment counts and funnel totals.
            # SQL providers can serve that narrow read without calculating the
            # benchmark, negative rollups and compliance block used by /pool.
            admin_comment_pool = getattr(
                self.market_provider, "admin_comment_pool", None
            )
            pool_data = (
                admin_comment_pool(range_key)
                if callable(admin_comment_pool)
                else self.market_provider.pool(range_key)
            )
            if pool_data is MISSING:
                raise AdminValidationError("不支持这个时间区间")
            comment_rows = self._comment_rows(pool_data, products)
            with self.target_engine.connect() as conn:
                meta = dict(conn.execute(select(meta_kv.c.k, meta_kv.c.v)).all())
            own_limit = _json(meta.get(WORKER_READINESS_KEY), {}).get("ownTopLimit")
            top_codes = [
                row["code"]
                for row in analysis_products(
                    self.target_engine, range_key, own_limit=own_limit
                )
            ]
            products_by_code = {str(product["code"]): product for product in products}
            selected_products = [
                products_by_code[code] for code in top_codes if code in products_by_code
            ]
            with self.target_engine.connect() as conn:
                layers = self._ai_layers(conn, meta, range_key, selected_products)
            codes_by_range = {range_key: top_codes}
            for other in RANGE_PRIORITY:
                if other not in codes_by_range:
                    codes_by_range[other] = (
                        [str(product["code"]) for product in products]
                        if own_limit is None
                        else [
                            row["code"]
                            for row in analysis_products(
                                self.target_engine, other, own_limit=own_limit
                            )
                        ]
                    )
            runs = list_requests(self.target_engine, limit=20)
            active_runs = list_active_requests(self.target_engine)
        except AdminOpsError:
            raise
        except (SQLAlchemyError, OSError, RuntimeError, ValueError) as exc:
            raise AdminUnavailable("Radar 数据库暂时不可用") from exc
        observed_at = self.now()
        health = worker_health(meta, now=observed_at)
        range_states = self._range_states(runs, active_runs)
        return {
            "generatedAt": _iso(observed_at),
            "anchor": meta.get("anchor"),
            "dataThrough": meta.get("data_max_ts"),
            "selectedRange": range_key,
            "selectedRangeAnchor": layers.get("anchor"),
            "comments": comment_rows,
            "commentDisplayLimit": TOP_COMMENT_PRODUCT_LIMIT,
            "analyzedProductCodes": top_codes,
            "ownTopLimit": own_limit,
            "layers": layers,
            "coverage": self._coverage(
                meta,
                codes_by_range,
                range_states,
                target_engine=self.target_engine,
            ),
            "checks": self._checks(meta, health, [str(p["code"]) for p in products]),
            "daily": _json(meta.get(LAST_DAILY_RUN_KEY), None),
            "intraday": _json(meta.get(LAST_INTRADAY_RUN_KEY), None),
            "worker": health,
            "ranges": range_states,
            "recentRuns": runs,
        }

    @staticmethod
    def _coverage(meta, codes_by_range, range_states, *, target_engine=None):
        """Completion of every page range: products fully fresh, served stale, or missing."""

        complete_anchor = str(meta.get("anchor") or "")
        active = {row["key"]: row.get("activeRun") for row in range_states}
        rows = []
        for range_key in RANGE_PRIORITY:
            effective_anchor = (
                analysis_range_anchor(
                    target_engine,
                    range_key,
                    complete_anchor,
                    meta=meta,
                )
                if target_engine is not None
                else None
            )
            anchor = effective_anchor.isoformat() if effective_anchor else ""
            codes = codes_by_range.get(range_key) or []
            states = layer_b_states(meta, codes, range_key, anchor)
            missing = {code for state in states.values() for code in state["missing"]}
            stale = {code for state in states.values() for code in state["stale"]} - missing
            complete = len(codes) - len(missing) - len(stale)
            if codes and complete == len(codes):
                status = "complete"
            elif active.get(range_key):
                status = active[range_key]["status"]
            elif complete or stale:
                status = "partial"
            else:
                status = "not_started"
            rows.append({
                "key": range_key,
                "expected": len(codes),
                "complete": complete,
                "stale": len(stale),
                "missing": len(missing),
                "missingCodes": sorted(missing)[:MISSING_CODE_LIMIT],
                "status": status,
            })
        return rows

    @staticmethod
    def _checks(meta, health, codes):
        readiness = _json(meta.get(WORKER_READINESS_KEY), {})
        daily = _json(meta.get(LAST_DAILY_RUN_KEY), {}) or {}
        intraday = _json(meta.get(LAST_INTRADAY_RUN_KEY), {}) or {}
        filter_ready = is_filter_ready(meta, load_comment_filter_config())
        routes_ready = comment_routes_ready(meta, codes)
        checks = [
            {
                "id": "worker",
                "label": "AI worker 心跳",
                "status": "ok" if health["online"] else "fail",
                "detail": (
                    f"上次心跳 {health['lastSeenAt']}" if health["online"]
                    else "worker 未运行：在 worker 目录执行 python scheduler.py"
                ),
            },
            {
                "id": "classifier",
                "label": "Layer A 模型（逐条判定）",
                "status": "ok" if health["ready"] else "fail",
                "detail": (
                    f"{readiness.get('provider') or ''} · {readiness.get('model') or ''}".strip(" ·")
                    if health["ready"] else (health["error"] or "未就绪")
                ),
            },
            {
                "id": "generation",
                "label": "Layer B 生成模型（页面总结）",
                "status": (
                    "fail" if not health["ready"] or readiness.get("generationError") else "ok"
                ),
                "detail": readiness.get("generationError") or (
                    f"{readiness.get('generationProvider') or ''} · {readiness.get('generationModel') or ''}".strip(" ·")
                    or "等待 worker 心跳"
                ),
            },
            {
                "id": "routes",
                "label": "评论归属口径",
                "status": "ok" if routes_ready or filter_ready else "fail",
                "detail": (
                    "按正文 cashtag 归属" if routes_ready
                    else "按父帖 tag 归属" if filter_ready
                    else "评论过滤未激活：执行 python -m jobs.backfill_comment_filter --resume --activate"
                ),
            },
            {
                "id": "anchor",
                "label": "数据 anchor",
                "status": "ok" if meta.get("anchor") else "fail",
                "detail": meta.get("anchor") or "库里没有 anchor：先导入数据",
            },
            {
                "id": "mode",
                "label": "数据模式",
                "status": "info",
                "detail": (
                    "生产：每日任务先从 MarketInsight 同步最新数据"
                    if readiness.get("dataMode") == "production"
                    else "本地 dump：不同步，只分析库内已有数据"
                ),
            },
            {
                "id": "daily",
                "label": "每日定时任务",
                "status": "info" if readiness.get("dailyCron") else "fail",
                "detail": (
                    (f"{readiness['dailyCron']}（香港时间）" if readiness.get("dailyCron") else "已关闭（AI_DAILY_CRON=off）")
                    + (
                        f" · 上次 {daily.get('startedAt')}，同步 {(daily.get('sync') or {}).get('status')}"
                        if daily.get("startedAt") else " · 尚未运行过"
                    )
                ),
            },
            {
                "id": "intraday",
                "label": "当天定时分析",
                "status": "info" if readiness.get("intradayCron") else "fail",
                "detail": (
                    (
                        f"{readiness['intradayCron']}（香港时间）"
                        if readiness.get("intradayCron")
                        else "已关闭（AI_INTRADAY_CRON=off）"
                    )
                    + (
                        f" · 上次 {intraday.get('startedAt')}"
                        f"，数据日 {intraday.get('dataDate') or '未知'}"
                        f"，同步 {(intraday.get('sync') or {}).get('status')}"
                        if intraday.get("startedAt")
                        else " · 尚未运行过"
                    )
                ),
            },
        ]
        return checks

    def _require_ready_worker(self):
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

    def enqueue_analysis(self, range_key: str) -> dict[str, Any]:
        if range_key not in RANGE_PRIORITY:
            raise AdminValidationError("range 只允许 d0、d1、d2、d7、d14、d30 或 mtd")
        try:
            self._require_ready_worker()
            active = list_active_requests(self.target_engine)
            same_range = next(
                (request for request in active if request["range"] == range_key),
                None,
            )
            if same_range is not None:
                return {"run": same_range, "created": False}
            if active:
                running = active[0]
                raise AdminBusy(
                    f"已有 {running['range'].upper()} 分析正在"
                    f"{'运行' if running['status'] == 'running' else '排队'}；"
                    "完成后才能提交下一个区间"
                )
            request, created = enqueue(self.target_engine, range_key)
        except AdminOpsError:
            raise
        except ValueError as exc:
            raise AdminValidationError(str(exc)) from exc
        except SQLAlchemyError as exc:
            raise AdminUnavailable("Radar 数据库暂时不可用") from exc
        return {"run": request, "created": created}
