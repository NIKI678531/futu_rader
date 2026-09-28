"""Persistent, concurrency-safe daily HTTP attempt budget."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from ai.providers.base import RunStopped
from radar_db.schema import ai_daily_budget

HARD_DAILY_MAX = 500
HKT = ZoneInfo("Asia/Hong_Kong")


class DailyBudget:
    """Atomically charge each attempt to the HKT day when it is made.

    The day is intentionally not caller supplied: otherwise two retries could
    choose different labels and each receive a fresh 500-request allowance.
    """

    def __init__(self, engine, policy_hash, request_limit=500, *, now=datetime.utcnow):
        if request_limit < 1 or request_limit > HARD_DAILY_MAX:
            raise ValueError(f"request_limit must be between 1 and {HARD_DAILY_MAX}")
        self.engine = engine
        self.policy_hash = policy_hash
        self.request_limit = request_limit
        self.now = now
        self._ensure_row(self._budget_date())

    def _budget_date(self):
        value = self.now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(HKT).date()

    def _ensure_row(self, budget_date):
        with self.engine.connect() as conn:
            row = conn.execute(select(ai_daily_budget).where(
                ai_daily_budget.c.budget_date == budget_date
            )).mappings().first()
        if row is None:
            conflict = None
            try:
                # Keep the insert in its own transaction. Under MySQL's default
                # REPEATABLE READ isolation, a transaction that first observed
                # no row cannot see a concurrent winner after its INSERT loses
                # the primary-key race, even after rolling back a savepoint.
                with self.engine.begin() as conn:
                    conn.execute(insert(ai_daily_budget).values(
                        budget_date=budget_date, policy_hash=self.policy_hash,
                        request_limit=self.request_limit, requests_used=0,
                        status="open", updated_at=self.now(),
                    ))
            except IntegrityError as exc:
                conflict = exc
            with self.engine.connect() as conn:
                row = conn.execute(select(ai_daily_budget).where(
                    ai_daily_budget.c.budget_date == budget_date
                )).mappings().first()
            if row is None:
                if conflict is not None:
                    raise conflict
                raise RuntimeError("AI daily budget row was not persisted")
        if row["policy_hash"] != self.policy_hash:
            raise ValueError("AI policy changed within the same HKT budget day")
        if row["request_limit"] != self.request_limit:
            raise ValueError("AI daily request limit cannot change within the same HKT day")

    def reserve(self):
        budget_date = self._budget_date()
        self._ensure_row(budget_date)
        with self.engine.begin() as conn:
            result = conn.execute(update(ai_daily_budget).where(
                ai_daily_budget.c.budget_date == budget_date,
                ai_daily_budget.c.policy_hash == self.policy_hash,
                ai_daily_budget.c.status == "open",
                ai_daily_budget.c.requests_used < ai_daily_budget.c.request_limit,
            ).values(
                requests_used=ai_daily_budget.c.requests_used + 1,
                updated_at=self.now(),
            ))
            if not result.rowcount:
                conn.execute(update(ai_daily_budget).where(
                    ai_daily_budget.c.budget_date == budget_date,
                    ai_daily_budget.c.requests_used >= ai_daily_budget.c.request_limit,
                ).values(status="exhausted", updated_at=self.now()))
                raise RunStopped("budget_exhausted")
            used = conn.execute(select(ai_daily_budget.c.requests_used).where(
                ai_daily_budget.c.budget_date == budget_date
            )).scalar_one()
            if used >= self.request_limit:
                conn.execute(update(ai_daily_budget).where(
                    ai_daily_budget.c.budget_date == budget_date
                ).values(status="exhausted", updated_at=self.now()))

    def snapshot(self):
        budget_date = self._budget_date()
        self._ensure_row(budget_date)
        with self.engine.connect() as conn:
            row = conn.execute(select(ai_daily_budget).where(
                ai_daily_budget.c.budget_date == budget_date
            )).mappings().one()
        return {
            "budgetDate": row["budget_date"].isoformat(),
            "requestLimit": row["request_limit"],
            "requestsUsed": row["requests_used"],
            "requestsRemaining": row["request_limit"] - row["requests_used"],
            "status": row["status"],
        }
