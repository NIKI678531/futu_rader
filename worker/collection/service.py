"""The deep collection module: source paging, idempotency and publication state."""

from __future__ import annotations

import hashlib
import json
import time as time_module
import uuid
from contextlib import nullcontext
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import and_, delete, func, insert, select, update

from radar_db.leases import WorkerLease
from radar_db.revisions import bump_revision, ensure_ai_revision, mark_synthesis
from radar_db.schema import (
    collector_checkpoints,
    comments,
    feed_counter_observations,
    feeds,
    ingestion_runs,
    mentions,
    meta_kv,
    users,
)

from .models import AiRequest, AiResult, Cursor, FeedObservation, SyncRequest, SyncResult, UserObservation
from .source import STREAMS, SourceAdapter


NORMALIZER_VERSION = "market-insight-v1"
SEMANTIC_FEED_FIELDS = ("code", "posted_at", "feed_type", "title", "content")
SEMANTIC_COMMENT_FIELDS = ("feed_id", "posted_at", "author_uid", "content", "reply_to_comment_id")
COUNTER_FIELDS = ("like_count", "comment_count", "image_count", "share_count", "browse_count")


def utc_naive(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _now():
    return datetime.utcnow()


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _put_meta(conn, key, value):
    value = None if value is None else str(value)
    if not conn.execute(update(meta_kv).where(meta_kv.c.k == key).values(v=value)).rowcount:
        conn.execute(insert(meta_kv).values(k=key, v=value))


def _different(existing, values, fields):
    return any(existing.get(field) != values.get(field) for field in fields)


def _cursor_payload(cursor):
    return cursor.as_json() if cursor else None


class FutuRefresh:
    """Synchronize all MarketInsight streams and run bounded downstream AI.

    Airflow only needs this Interface. Source schema knowledge, keyset cursors,
    partial-comment semantics and target idempotency stay in the Implementation.
    """

    def __init__(self, target_engine, source: SourceAdapter | None = None, *, now=_now):
        self.target = target_engine
        self.source = source
        self.now = now
        if source is None:
            self.config_hash = None
            return
        pool = sorted(getattr(source, "pool_codes", ()))
        self.config_hash = hashlib.sha256(_json({
            "normalizer": NORMALIZER_VERSION,
            "pool": pool,
        }).encode()).hexdigest()

    def sync(self, request: SyncRequest) -> SyncResult:
        if self.source is None:
            raise ValueError("sync requires a source adapter")
        source_run_id = request.through_source_run_id
        idempotency_key = source_run_id or request.run_id
        if idempotency_key:
            digest = hashlib.sha256(f"{self.source.source_id}:{idempotency_key}".encode()).hexdigest()[:40]
            run_id = f"sync-{digest}"
        else:
            run_id = "sync-" + self.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]

        prior = self._prior_run(source_run_id, run_id)
        if prior and prior["status"] == "succeeded":
            return self._result_from_run(prior, status="noop")

        partition = "live" if request.mode == "incremental" else f"{request.mode}:{run_id[-32:]}"
        cursors = {stream: self._load_cursor(stream, partition) for stream in STREAMS}
        # Capture the upstream completion declaration before fixing stream
        # high-watermarks. A producer that finishes while we are scanning must
        # be published by the next run, never ahead of rows this run could see.
        declared_complete = prior.get("complete_through") if prior else None
        if declared_complete is None and prior is None and source_run_id:
            declared_complete = self.source.complete_through(source_run_id)
        highwaters = self._load_highwaters(prior) if prior else {
            stream: self.source.high_watermark(stream, source_run_id) for stream in STREAMS
        }
        result = SyncResult(run_id=run_id, status="dry_run" if request.dry_run else "succeeded")
        result.cursors = {stream: _cursor_payload(cursor) for stream, cursor in cursors.items()}
        prior_counts = json.loads(prior.get("counts_json") or "{}") if prior else {}
        counts = {key: int(prior_counts.get(key, 0))
                  for key in ("rows_read", "inserted", "updated", "unchanged", "ignored")}
        changed_codes = set(prior_counts.get("changedCodes", ()))
        coverages = set(prior_counts.get("coverages", ()))
        last_revision = prior.get("data_revision") if prior else self._current_data_revision()
        anchor_changed = False

        source_kind = "all" if declared_complete is not None else (
            "backfill" if request.mode == "backfill" else "partial"
        )
        lease = nullcontext() if request.dry_run else WorkerLease(self.target, "futu-refresh", seconds=600)
        # A dry-run executes normal writes inside one connection-wide
        # transaction and lets Connection.__exit__ roll it back. This keeps
        # cross-page/stream behavior realistic (including users discovered
        # from feeds earlier in the run) without publishing any state.
        simulation = self.target.connect() if request.dry_run else nullcontext(None)
        run_opened = False
        try:
            with lease, simulation as simulation_conn:
                if not request.dry_run:
                    self._ensure_ai_revision()
                    self._open_run(run_id, source_run_id, source_kind, cursors, highwaters,
                                   declared_complete, prior)
                    run_opened = True
                for stream in STREAMS:
                    after = cursors[stream]
                    through = highwaters[stream]
                    while True:
                        page = self.source.pull_page(stream, after, through, request.page_size)
                        if not request.dry_run and lease.lost:
                            raise RuntimeError("Lost the futu-refresh lease while reading a source page")
                        if request.dry_run:
                            counts["rows_read"] += len(page.items) + page.ignored
                            counts["ignored"] += page.ignored
                            page_counts, page_codes, page_coverage, _revision = self._apply_page(
                                stream, page.items, run_id, repair_counters=False,
                                conn=simulation_conn, dry_run=False,
                            )
                        else:
                            next_counts = dict(counts)
                            next_changed_codes = set(changed_codes)
                            next_coverages = set(coverages)
                            next_cursors = {
                                **result.cursors,
                                stream: _cursor_payload(page.next_cursor),
                            }
                            with self.target.begin() as conn:
                                page_counts, page_codes, page_coverage, page_revision = self._apply_page(
                                    stream, page.items, run_id,
                                    repair_counters=request.mode == "repair", conn=conn
                                )
                                next_revision = page_revision or last_revision
                                if page.next_cursor is not None:
                                    self._save_cursor(conn, stream, partition, page.next_cursor, run_id)
                                next_counts["rows_read"] += len(page.items) + page.ignored
                                next_counts["ignored"] += page.ignored
                                for key, value in page_counts.items():
                                    next_counts[key] += value
                                next_changed_codes.update(page_codes)
                                next_coverages.update(page_coverage)
                                self._update_running_run(
                                    conn, run_id, next_counts, next_changed_codes,
                                    next_coverages, next_cursors, next_revision,
                                )
                            # Publish in-memory audit state only after the same
                            # transaction as facts/checkpoint has committed.
                            counts = next_counts
                            changed_codes = next_changed_codes
                            coverages = next_coverages
                            result.cursors = next_cursors
                            last_revision = next_revision
                        if request.dry_run:
                            for key, value in page_counts.items():
                                counts[key] += value
                            changed_codes.update(page_codes)
                            coverages.update(page_coverage)
                        after = page.next_cursor
                        if request.dry_run:
                            result.cursors[stream] = _cursor_payload(after)
                        if page.exhausted:
                            break

                complete = declared_complete
                if not request.dry_run:
                    with self.target.begin() as conn:
                        complete, anchor_changed = self._advance_complete_through(conn, complete)
                        if anchor_changed:
                            last_revision = bump_revision(conn, "data")
                        if request.mode == "backfill":
                            for stream in STREAMS:
                                self._promote_live_cursor(
                                    conn, stream, Cursor.from_json(result.cursors[stream]), run_id
                                )
                        self._finish_run(conn, run_id, counts, changed_codes, coverages, result.cursors,
                                         declared_complete, last_revision, "succeeded")
                        _put_meta(conn, "collection_last_sync_at", self.now().isoformat(timespec="seconds"))
                result.complete_through = complete
        except BaseException as exc:
            if run_opened:
                with self.target.begin() as conn:
                    conn.execute(update(ingestion_runs).where(ingestion_runs.c.run_id == run_id).values(
                        status="failed", finished_at=self.now(), error_summary=str(exc)[:4000],
                        counts_json=_json({
                            **counts,
                            "changedCodes": sorted(changed_codes),
                            "coverages": sorted(coverages),
                        }),
                        cursor_after_json=_json(result.cursors), data_revision=last_revision,
                    ))
            raise

        result.rows_read = counts["rows_read"]
        result.inserted = counts["inserted"]
        result.updated = counts["updated"]
        result.unchanged = counts["unchanged"]
        result.ignored = counts["ignored"]
        result.changed_codes = sorted(changed_codes)
        result.comment_coverage = self._coverage(coverages)
        result.data_revision = last_revision
        if not request.dry_run and not (result.inserted or result.updated or anchor_changed):
            result.status = "noop"
        return result

    def analyze(self, request: AiRequest) -> AiResult:
        """Run the existing analysis pipeline with one persistent HKT-day budget."""
        if not request.data_governance_approved:
            raise ValueError(
                "Automatic AI analysis is disabled: set "
                "AI_DATA_GOVERNANCE_APPROVED=true only after the ADR-0017/ADR-0019 "
                "data-governance prerequisites are approved"
            )

        from ai import config
        from ai.providers import build as build_provider
        from ai.providers.base import RunControl
        from jobs import analyze as analysis_job, full_own

        cfg = config.load()
        from dataclasses import replace
        cfg = replace(cfg, micro_batch_size=request.batch_size, concurrency=request.concurrency,
                      grouped_batches=True)
        analysis_job.check_calibration(cfg, request.calibration_report, require_singleton=True)
        if request.wait_for_ready:
            self._wait_for_ready(request.budget_date, request.wait_timeout_seconds)
        args = type("Args", (), {
            "codes": None, "sector": None, "struct": None, "ownership": "all", "anchor": None,
            "anchor_mode": "latest-complete", "ranges": ",".join(request.ranges),
        })()
        plan = analysis_job.make_plan(self.target, args)
        policy_hash = hashlib.sha256(_json({
            "model": cfg.model,
            "prompt": cfg.prompt_version,
            "taxonomy": cfg.taxonomy_version,
            "schema": cfg.schema_version,
            "batch": request.batch_size,
            "concurrency": request.concurrency,
        }).encode()).hexdigest()
        from .budget import DailyBudget
        budget = DailyBudget(
            self.target, policy_hash, request.max_http_attempts, now=self.now
        )
        control = RunControl(request.max_http_attempts)
        control.reserve_hook = budget.reserve
        provider = build_provider(cfg, control=control)
        progress = full_own.run_manual(self.target, cfg, plan, provider)
        snapshot = budget.snapshot()
        pending = sum(not row.get("complete", False) for row in progress.get("products", {}).values())
        status = progress.get("status", "unknown")
        completed_ranges, pending_ranges = self._analysis_range_state(
            plan["codes"], request.ranges
        )
        failure_reason = control.reason or (
            status if status in {"blocked", "error", "cancelled"} else None
        )
        return AiResult(
            status=status,
            attempts_used=control.attempts,
            attempts_remaining=snapshot["requestsRemaining"],
            pending_products=pending,
            completed_ranges=completed_ranges,
            pending_ranges=pending_ranges,
            failure_reason=failure_reason,
            detail=progress,
        )

    def _analysis_range_state(self, codes, ranges):
        """Return ranges whose synthesis is current for every requested product.

        ``synthesize.run`` clears one ``synth_dirty_<code>_<range>`` marker only
        after that product/range pair finishes without errors.  Reading those
        durable markers preserves partial progress when the daily budget stops
        halfway through a multi-range run.
        """
        codes = list(dict.fromkeys(str(code) for code in codes))
        ranges = list(dict.fromkeys(str(range_key) for range_key in ranges))
        keys = [f"synth_dirty_{code}_{range_key}" for code in codes for range_key in ranges]
        with self.target.connect() as conn:
            values = dict(conn.execute(
                select(meta_kv.c.k, meta_kv.c.v).where(meta_kv.c.k.in_(keys))
            ).all()) if keys else {}
        completed = [
            range_key for range_key in ranges
            if codes and all(values.get(f"synth_dirty_{code}_{range_key}") == "0" for code in codes)
        ]
        return completed, [range_key for range_key in ranges if range_key not in completed]

    def _wait_for_ready(self, budget_date, timeout_seconds):
        if self.source is None or not hasattr(self.source, "closing_run"):
            raise ValueError("wait_for_ready requires a collection-run capable source adapter")
        deadline = time_module.monotonic() + timeout_seconds
        while True:
            source_run = self.source.closing_run(budget_date)
            if source_run:
                with self.target.connect() as conn:
                    synced = conn.execute(select(ingestion_runs.c.run_id).where(
                        ingestion_runs.c.source == self.source.source_id,
                        ingestion_runs.c.source_run_id == source_run["run_id"],
                        ingestion_runs.c.source_kind == "all",
                        ingestion_runs.c.status == "succeeded",
                        ingestion_runs.c.complete_through >= budget_date,
                    ).limit(1)).first()
                if synced:
                    return source_run
            remaining = deadline - time_module.monotonic()
            if remaining <= 0:
                raise RuntimeError("Timed out waiting for the 19:30 comments_all run and Radar sync")
            time_module.sleep(min(30, remaining))

    def _prior_run(self, source_run_id, run_id):
        with self.target.connect() as conn:
            if source_run_id:
                match = and_(
                    ingestion_runs.c.source == self.source.source_id,
                    ingestion_runs.c.source_run_id == source_run_id,
                )
            else:
                match = ingestion_runs.c.run_id == run_id
            return conn.execute(select(ingestion_runs).where(match)).mappings().first()

    def _load_highwaters(self, prior):
        raw = json.loads(prior.get("high_watermark_json") or "{}")
        return {stream: Cursor.from_json(raw.get(stream)) for stream in STREAMS}

    def _load_cursor(self, stream, partition):
        with self.target.connect() as conn:
            row = conn.execute(select(collector_checkpoints).where(
                collector_checkpoints.c.source == self.source.source_id,
                collector_checkpoints.c.stream == stream,
                collector_checkpoints.c.partition_key == partition,
            )).mappings().first()
        if not row:
            return None
        if row["config_hash"] != self.config_hash:
            raise ValueError(f"Checkpoint configuration changed for {stream}; run an explicit backfill")
        entity_id = row["cursor_id"]
        if stream != "users" and entity_id is not None:
            entity_id = int(entity_id)
        return Cursor(row["cursor_at"], entity_id) if row["cursor_at"] is not None else None

    def _save_cursor(self, conn, stream, partition, cursor, run_id):
        match = and_(
            collector_checkpoints.c.source == self.source.source_id,
            collector_checkpoints.c.stream == stream,
            collector_checkpoints.c.partition_key == partition,
        )
        values = dict(cursor_at=utc_naive(cursor.observed_at) if cursor else None,
                      cursor_id=str(cursor.entity_id) if cursor else None,
                      updated_at=self.now(), last_run_id=run_id, config_hash=self.config_hash)
        if not conn.execute(update(collector_checkpoints).where(match).values(**values)).rowcount:
            conn.execute(insert(collector_checkpoints).values(
                source=self.source.source_id, stream=stream, partition_key=partition, **values
            ))

    def _promote_live_cursor(self, conn, stream, candidate, run_id):
        """Move a reconciliation cursor forward without regressing live sync."""

        row = conn.execute(select(collector_checkpoints).where(
            collector_checkpoints.c.source == self.source.source_id,
            collector_checkpoints.c.stream == stream,
            collector_checkpoints.c.partition_key == "live",
        )).mappings().first()
        if row and row["config_hash"] == self.config_hash:
            entity_id = row["cursor_id"]
            if stream != "users" and entity_id is not None:
                entity_id = int(entity_id)
            current = (
                Cursor(row["cursor_at"], entity_id)
                if row["cursor_at"] is not None else None
            )
            if candidate is None or (
                current is not None
                and (current.observed_at, current.entity_id)
                >= (candidate.observed_at, candidate.entity_id)
            ):
                return
        self._save_cursor(conn, stream, "live", candidate, run_id)

    def _open_run(self, run_id, source_run_id, source_kind, cursors, highwaters, declared_complete, prior):
        values = dict(
            source=self.source.source_id, source_run_id=source_run_id, source_kind=source_kind,
            status="running", started_at=prior["started_at"] if prior else self.now(), finished_at=None,
            complete_through=declared_complete,
            high_watermark_json=_json({key: _cursor_payload(value) for key, value in highwaters.items()}),
            cursor_before_json=prior.get("cursor_before_json") if prior else _json({
                key: _cursor_payload(value) for key, value in cursors.items()
            }),
            cursor_after_json=_json({key: _cursor_payload(value) for key, value in cursors.items()}),
            counts_json=prior.get("counts_json") if prior else _json({}), error_summary=None,
        )
        with self.target.begin() as conn:
            if prior:
                conn.execute(update(ingestion_runs).where(ingestion_runs.c.run_id == prior["run_id"]).values(**values))
            else:
                conn.execute(insert(ingestion_runs).values(run_id=run_id, **values))

    def _ensure_ai_revision(self):
        """Establish a semantic baseline before general fact revisions move."""

        with self.target.begin() as conn:
            ensure_ai_revision(conn)

    def _current_data_revision(self):
        with self.target.connect() as conn:
            return conn.execute(select(meta_kv.c.v).where(
                meta_kv.c.k == "data_revision"
            )).scalar()

    def _update_running_run(
        self, conn, run_id, counts, changed_codes, coverages, cursors, revision
    ):
        conn.execute(update(ingestion_runs).where(ingestion_runs.c.run_id == run_id).values(
            counts_json=_json({
                **counts,
                "changedCodes": sorted(changed_codes),
                "coverages": sorted(coverages),
            }),
            cursor_after_json=_json(cursors), data_revision=revision,
        ))

    def _finish_run(
        self, conn, run_id, counts, changed_codes, coverages, cursors, complete, revision, status
    ):
        conn.execute(update(ingestion_runs).where(ingestion_runs.c.run_id == run_id).values(
            status=status, finished_at=self.now(), complete_through=complete,
            counts_json=_json({
                **counts,
                "changedCodes": sorted(changed_codes),
                "coverages": sorted(coverages),
            }),
            cursor_after_json=_json(cursors), data_revision=revision, error_summary=None,
        ))

    def _advance_complete_through(self, conn, candidate):
        if candidate is None:
            current = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "source_complete_through")).scalar()
            return (date.fromisoformat(current) if current else None), False
        if isinstance(candidate, datetime):
            candidate = candidate.date()
        elif isinstance(candidate, str):
            candidate = date.fromisoformat(candidate[:10])
        current_raw = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "source_complete_through")).scalar()
        current = date.fromisoformat(current_raw[:10]) if current_raw else None
        if current and candidate < current:
            return current, False
        changed = current is None or candidate > current
        if changed:
            _put_meta(conn, "source_complete_through", candidate.isoformat())
            anchor_raw = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "anchor")).scalar()
            if not anchor_raw or candidate > date.fromisoformat(anchor_raw[:10]):
                _put_meta(conn, "anchor", candidate.isoformat())
                _put_meta(conn, "anchor_ts", datetime.combine(candidate, time.max).replace(microsecond=0))
        return candidate, changed

    @staticmethod
    def _coverage(values):
        if "retryable_incomplete" in values:
            return "retryable_incomplete"
        if "partial" in values:
            return "partial"
        if values and values == {"complete"}:
            return "complete"
        return "unknown"

    def _apply_page(
        self, stream, items, run_id, *, repair_counters=False, conn=None, dry_run=False
    ):
        owned = conn is None and not dry_run
        context = self.target.begin() if owned else self.target.connect() if conn is None else nullcontext(conn)
        counts = {"inserted": 0, "updated": 0, "unchanged": 0, "ignored": 0}
        changed_codes = set()
        coverages = set()
        revision = None
        with context as active:
            user_ids = {
                item.user["user_id"] for item in items
                if isinstance(item, UserObservation)
            }
            in_scope_users = set()
            if user_ids:
                in_scope_users.update(active.execute(select(feeds.c.author_uid).where(
                    feeds.c.author_uid.in_(user_ids)
                ).distinct()).scalars())
            for item in items:
                if isinstance(item, UserObservation):
                    # Source profiles have no stock key. Resolve scope against
                    # already-normalized target feeds so legacy rows whose
                    # source author_uid was recovered from raw_json are not
                    # silently missed, while out-of-pool users stay excluded.
                    if item.user["user_id"] not in in_scope_users:
                        counts["ignored"] += 1
                        continue
                    changed = self._apply_user(active, item, dry_run)
                    counts["updated" if changed == "updated" else "inserted" if changed == "inserted" else "unchanged"] += 1
                    continue
                outcome, affected_codes = self._apply_feed(
                    active, stream, item, run_id, dry_run, repair_counters=repair_counters
                )
                counts[outcome] += 1
                changed_codes.update(affected_codes)
                coverages.add(item.coverage)
            if not dry_run and (counts["inserted"] or counts["updated"]):
                revision = bump_revision(active, "data")
            if not dry_run and changed_codes:
                bump_revision(active, "ai_input")
                mark_synthesis(active, changed_codes, True)
        return counts, changed_codes, coverages, revision

    def _apply_user(self, conn, observation, dry_run):
        values = dict(observation.user)
        existing = conn.execute(select(users).where(users.c.user_id == values["user_id"])).mappings().first()
        if existing is None:
            if not dry_run:
                conn.execute(insert(users).values(**values))
            return "inserted"
        for field, value in tuple(values.items()):
            if value is None and existing.get(field) is not None:
                values[field] = existing[field]
        if not _different(existing, values, values.keys()):
            return "unchanged"
        if not dry_run:
            conn.execute(update(users).where(users.c.user_id == values["user_id"]).values(**values))
        return "updated"

    def _apply_feed(self, conn, stream, observation, run_id, dry_run, *, repair_counters=False):
        values = dict(observation.feed)
        values["posted_at"] = utc_naive(values["posted_at"])
        values["source_observed_at"] = utc_naive(values["source_observed_at"])
        if any(values[field] is None for field in ("like_count", "comment_count", "image_count")):
            raise ValueError(f"Feed {values['feed_id']} is missing a required counter")
        if any(values[field] is not None and values[field] < 0 for field in COUNTER_FIELDS):
            raise ValueError(f"Feed {values['feed_id']} contains a negative counter")
        existing = conn.execute(select(feeds).where(feeds.c.feed_id == values["feed_id"])).mappings().first()

        # A missing field in an upstream snapshot is not an instruction to
        # erase a previously observed value. This is especially important for
        # partial comment pages and payloads whose raw JSON was truncated.
        if existing:
            for field, value in tuple(values.items()):
                if value is None and existing.get(field) is not None:
                    values[field] = existing[field]

        if stream == "feed_details" and existing is None:
            # A detail record can race the initial feed stream. Let the feed stream create the canonical row.
            return "unchanged", set()

        if existing and stream == "feed_details":
            update_values = {}
            if values.get("content") and len(values["content"]) > len(existing.get("content") or ""):
                update_values["content"] = values["content"]
            if values["source_observed_at"] and (
                existing.get("source_observed_at") is None
                or values["source_observed_at"] > existing["source_observed_at"]
            ):
                update_values["source_observed_at"] = values["source_observed_at"]
            semantic = "content" in update_values
            if update_values and not dry_run:
                conn.execute(update(feeds).where(feeds.c.feed_id == values["feed_id"]).values(**update_values))
            affected = self._feed_product_codes(
                conn, values["feed_id"], existing.get("code"), values.get("code")
            ) if semantic else set()
            return ("updated" if semantic else "unchanged"), affected

        semantic = existing is None or _different(existing, values, SEMANTIC_FEED_FIELDS)
        if existing and values["raw_json_broken"]:
            for field in ("share_count", "browse_count", "comments_parsed", "comments_truncated",
                          "comment_coverage_status", "original_lang"):
                values[field] = existing.get(field)

        counter_values = {field: values[field] for field in COUNTER_FIELDS}
        selected_counters = self._record_counter_observation(
            conn, values["feed_id"], values["posted_at"], values["source_observed_at"],
            counter_values, run_id, dry_run, repair=repair_counters,
        )
        if selected_counters is None and existing:
            selected_counters = {field: existing[field] for field in COUNTER_FIELDS}
        if selected_counters is not None:
            values.update(selected_counters)

        # comments_parsed is a target-derived cumulative count. A partial
        # source snapshot may contain fewer bodies than we already retained;
        # comparing that transient snapshot count would create a false update.
        meaningful_fields = tuple(
            field for field in values
            if field not in ("source_observed_at", "comments_parsed")
        )
        changed = existing is None or _different(existing, values, meaningful_fields)
        observation_advanced = bool(
            existing and values["source_observed_at"]
            and (existing.get("source_observed_at") is None
                 or values["source_observed_at"] > existing["source_observed_at"])
        )
        if existing is None:
            if not dry_run:
                conn.execute(insert(feeds).values(**values))
            outcome = "inserted"
        elif changed or observation_advanced:
            if not dry_run:
                conn.execute(update(feeds).where(feeds.c.feed_id == values["feed_id"]).values(**values))
            outcome = "updated" if changed else "unchanged"
        else:
            outcome = "unchanged"

        comments_fact_changed = False
        comments_semantic_changed = False
        semantic_affected = set()
        if stream == "feeds":
            existing_comments = {
                row["comment_id"]: row
                for row in conn.execute(select(comments).where(
                    comments.c.feed_id == values["feed_id"]
                )).mappings()
            }
            desired_comment_ids = set()
            for comment in observation.comments:
                comment_values = dict(comment)
                comment_values["posted_at"] = utc_naive(comment_values["posted_at"])
                desired_comment_ids.add(comment_values["comment_id"])
                prior = existing_comments.get(comment_values["comment_id"])
                if prior is None:
                    # Protect the global comment identity even if a malformed
                    # source payload attaches it to a different feed.
                    prior = conn.execute(select(comments).where(
                        comments.c.comment_id == comment_values["comment_id"]
                    )).mappings().first()
                if prior and prior["feed_id"] != values["feed_id"]:
                    raise ValueError(f"Comment {comment_values['comment_id']} belongs to another feed")
                if prior is None:
                    if not dry_run:
                        conn.execute(insert(comments).values(**comment_values))
                    comments_fact_changed = True
                    comments_semantic_changed = True
                else:
                    for field, value in tuple(comment_values.items()):
                        if value is None and prior.get(field) is not None:
                            comment_values[field] = prior[field]
                if prior is not None and _different(prior, comment_values, comment_values.keys()):
                    if not dry_run:
                        conn.execute(update(comments).where(
                            comments.c.comment_id == comment_values["comment_id"]
                        ).values(**comment_values))
                    comments_fact_changed = True
                    comments_semantic_changed = (
                        comments_semantic_changed
                        or _different(prior, comment_values, SEMANTIC_COMMENT_FIELDS)
                    )

            # A complete snapshot is authoritative. Partial/unknown snapshots
            # are additive only and must never erase comments obtained before.
            if observation.coverage == "complete" and not values["raw_json_broken"]:
                stale_comment_ids = set(existing_comments) - desired_comment_ids
                if stale_comment_ids:
                    if not dry_run:
                        conn.execute(delete(comments).where(
                            comments.c.comment_id.in_(stale_comment_ids)
                        ))
                    comments_fact_changed = True
                    comments_semantic_changed = True

            desired_mentions = {
                (mention["code"], mention["source"]): {
                    **mention,
                    "in_pool": self._code_is_in_pool(mention["code"]),
                }
                for mention in observation.mentions
            }
            existing_mentions = {
                (row["code"], row["source"]): row
                for row in conn.execute(select(mentions).where(
                    mentions.c.feed_id == values["feed_id"]
                )).mappings()
            }
            for mention in observation.mentions:
                mention_values = desired_mentions[(mention["code"], mention["source"])]
                match = and_(
                    mentions.c.feed_id == mention_values["feed_id"],
                    mentions.c.code == mention_values["code"],
                    mentions.c.source == mention_values["source"],
                )
                prior_mention = existing_mentions.get((mention_values["code"], mention_values["source"]))
                if prior_mention is None:
                    if self._code_is_in_pool(mention_values["code"]):
                        semantic_affected.add(mention_values["code"])
                    if not dry_run:
                        conn.execute(insert(mentions).values(**mention_values))
                    semantic = True
                    comments_fact_changed = True
                elif prior_mention["in_pool"] != mention_values["in_pool"]:
                    if prior_mention["in_pool"] or mention_values["in_pool"]:
                        semantic_affected.add(mention_values["code"])
                    if not dry_run:
                        conn.execute(update(mentions).where(match).values(
                            in_pool=mention_values["in_pool"]
                        ))
                    semantic = True
                    comments_fact_changed = True

            # A valid source payload is an authoritative mention snapshot. A
            # malformed payload is not: in that case preserve prior mentions
            # exactly as partial comment snapshots preserve prior comments.
            if not values["raw_json_broken"]:
                stale_mentions = set(existing_mentions) - set(desired_mentions)
                for code, source in stale_mentions:
                    if self._code_is_in_pool(code):
                        semantic_affected.add(code)
                    if not dry_run:
                        conn.execute(delete(mentions).where(
                            mentions.c.feed_id == values["feed_id"],
                            mentions.c.code == code,
                            mentions.c.source == source,
                        ))
                    semantic = True
                    comments_fact_changed = True

            if not dry_run:
                parsed = conn.execute(select(func.count()).select_from(comments).where(
                    comments.c.feed_id == values["feed_id"]
                )).scalar_one()
                coverage = values.get("comment_coverage_status")
                if coverage == "unknown" and existing:
                    coverage = existing.get("comment_coverage_status") or "unknown"
                conn.execute(update(feeds).where(feeds.c.feed_id == values["feed_id"]).values(
                    comments_parsed=parsed, comment_coverage_status=coverage
                ))

        semantic = semantic or comments_semantic_changed
        if semantic:
            semantic_affected.update(
                mention["code"] for mention in desired_mentions.values()
                if mention["in_pool"]
            )
        affected_codes = self._feed_product_codes(
            conn, values["feed_id"], existing.get("code") if existing else None, values.get("code")
        ) if semantic else set()
        affected_codes.update(semantic_affected)
        if comments_fact_changed and outcome == "unchanged":
            outcome = "updated"
        return outcome, affected_codes

    def _code_is_in_pool(self, code):
        pool = getattr(self.source, "pool_codes", None)
        return bool(code) and (pool is None or code in pool)

    def _feed_product_codes(self, conn, feed_id, *anchor_codes):
        """Return every in-scope product whose AI inputs a feed can affect."""

        result = {code for code in anchor_codes if self._code_is_in_pool(code)}
        result.update(conn.execute(select(mentions.c.code).where(
            mentions.c.feed_id == feed_id,
            mentions.c.in_pool.is_(True),
        )).scalars())
        return result

    def _record_counter_observation(
        self, conn, feed_id, posted_at, observed_at, values, run_id, dry_run, *, repair=False
    ):
        if observed_at is None:
            return values
        existing_observation = conn.execute(select(feed_counter_observations.c.feed_id).where(
            feed_counter_observations.c.feed_id == feed_id,
            feed_counter_observations.c.observed_at == observed_at,
        )).first()
        settled = conn.execute(select(feed_counter_observations.c.observed_at).where(
            feed_counter_observations.c.feed_id == feed_id,
            feed_counter_observations.c.is_settled.is_(True),
        ).limit(1)).first()
        eligible = posted_at is not None and observed_at >= posted_at + timedelta(hours=24)
        make_settled = eligible and settled is None
        if existing_observation is None and not dry_run:
            conn.execute(insert(feed_counter_observations).values(
                feed_id=feed_id, observed_at=observed_at, is_settled=make_settled,
                source_run_id=run_id, **values,
            ))
        if not repair:
            return values if settled is None else None

        # Repair is deliberately explicit. Re-evaluate all append-only
        # observations and select the first one at/after age 24h; when none is
        # eligible, expose the latest provisional observation.
        eligible_at = posted_at + timedelta(hours=24) if posted_at is not None else None
        query = select(feed_counter_observations).where(
            feed_counter_observations.c.feed_id == feed_id
        )
        if eligible_at is not None:
            candidate = conn.execute(query.where(
                feed_counter_observations.c.observed_at >= eligible_at
            ).order_by(feed_counter_observations.c.observed_at).limit(1)).mappings().first()
        else:
            candidate = None
        if candidate is None:
            candidate = conn.execute(query.order_by(
                feed_counter_observations.c.observed_at.desc()
            ).limit(1)).mappings().first()
        if candidate is None:  # only possible for a dry-run of a new observation
            return values
        if not dry_run:
            conn.execute(update(feed_counter_observations).where(
                feed_counter_observations.c.feed_id == feed_id
            ).values(is_settled=False))
            if eligible_at is not None and candidate["observed_at"] >= eligible_at:
                conn.execute(update(feed_counter_observations).where(
                    feed_counter_observations.c.feed_id == feed_id,
                    feed_counter_observations.c.observed_at == candidate["observed_at"],
                ).values(is_settled=True))
        return {field: candidate[field] for field in COUNTER_FIELDS}

    def _result_from_run(self, row, status):
        counts = json.loads(row.get("counts_json") or "{}")
        with self.target.connect() as conn:
            current_meta = dict(conn.execute(select(meta_kv.c.k, meta_kv.c.v).where(
                meta_kv.c.k.in_(("source_complete_through", "data_revision"))
            )).all())
        current_complete = current_meta.get("source_complete_through")
        complete_through = (
            date.fromisoformat(current_complete[:10])
            if current_complete else row.get("complete_through")
        )
        return SyncResult(
            run_id=row["run_id"], status=status,
            # This invocation performed no work. Keep the original receipt in
            # ingestion_runs for audit, but do not report its historical
            # mutations as if the replay had repeated them.
            rows_read=0, inserted=0, updated=0, unchanged=0,
            ignored=0, changed_codes=[],
            complete_through=complete_through,
            data_revision=current_meta.get("data_revision") or row.get("data_revision"),
            comment_coverage=self._coverage(set(counts.get("coverages", ()))),
            cursors=json.loads(row.get("cursor_after_json") or "{}"),
        )
