"""Public request/result types and private normalized observations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Literal, Mapping


Coverage = Literal["complete", "partial", "retryable_incomplete", "unknown"]
SyncMode = Literal["incremental", "backfill", "repair"]
AiMode = Literal["daily", "weekly"]


@dataclass(frozen=True)
class Cursor:
    """Opaque-at-the-public-seam keyset cursor used by source adapters."""

    observed_at: datetime
    entity_id: int | str

    def as_json(self) -> dict:
        return {"observedAt": self.observed_at.isoformat(), "entityId": self.entity_id}

    @classmethod
    def from_json(cls, value: dict | None) -> Cursor | None:
        if not value:
            return None
        return cls(datetime.fromisoformat(value["observedAt"]), value["entityId"])


@dataclass(frozen=True)
class SyncRequest:
    mode: SyncMode = "incremental"
    run_id: str | None = None
    through_source_run_id: str | None = None
    page_size: int = 1000
    dry_run: bool = False
    # A bounded repair may re-read specific parent rows after the third-party
    # collector has updated MarketInsight. Keep IDs at the synchronization seam
    # rather than teaching the normal keyset cursor another addressing mode.
    feed_ids: tuple[int, ...] = ()

    def __post_init__(self):
        if self.page_size < 1:
            raise ValueError("page_size must be positive")
        if self.mode == "repair" and self.dry_run:
            raise ValueError("repair mode cannot be combined with dry_run")
        normalized = tuple(dict.fromkeys(int(value) for value in self.feed_ids))
        if any(value <= 0 for value in normalized):
            raise ValueError("feed_ids must contain positive integers")
        if len(normalized) > 500:
            raise ValueError("feed_ids is limited to 500 items per targeted sync")
        if normalized and self.through_source_run_id:
            raise ValueError("feed_ids cannot be combined with through_source_run_id")
        object.__setattr__(self, "feed_ids", normalized)


@dataclass
class SyncResult:
    run_id: str
    status: Literal["succeeded", "failed", "dry_run", "noop"]
    rows_read: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    ignored: int = 0
    changed_codes: list[str] = field(default_factory=list)
    complete_through: date | None = None
    comment_coverage: Coverage = "unknown"
    data_revision: str | None = None
    cursors: dict[str, dict | None] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict:
        value = asdict(self)
        value["complete_through"] = self.complete_through.isoformat() if self.complete_through else None
        return value


@dataclass(frozen=True)
class AiRequest:
    budget_date: date
    anchor: date | None = None
    mode: AiMode = "daily"
    calibration_report: Path | Mapping[str, object] | None = None
    quality_report: Path | Mapping[str, object] | None = None
    max_http_attempts: int = 500
    batch_size: int = 5
    concurrency: int = 2
    wait_for_ready: bool = False
    wait_timeout_seconds: int = 28800
    data_governance_approved: bool = False
    codes: tuple[str, ...] = ()
    range_keys: tuple[str, ...] = ()

    def __post_init__(self):
        if type(self.data_governance_approved) is not bool:
            raise TypeError("data_governance_approved must be an explicit bool")
        if self.max_http_attempts < 1 or self.max_http_attempts > 500:
            raise ValueError("max_http_attempts must be between 1 and the hard daily cap of 500")
        if self.batch_size < 1 or self.batch_size > 5:
            raise ValueError("batch_size must be between 1 and 5")
        if self.concurrency < 1 or self.concurrency > 4:
            raise ValueError("concurrency must be between 1 and 4")
        if self.wait_timeout_seconds < 0:
            raise ValueError("wait_timeout_seconds cannot be negative")
        codes = tuple(dict.fromkeys(str(value).strip() for value in self.codes if str(value).strip()))
        if any(not value.isdigit() for value in codes):
            raise ValueError("codes must contain numeric product codes")
        ranges = tuple(dict.fromkeys(str(value).strip() for value in self.range_keys if str(value).strip()))
        supported = {"d1", "d2", "d7", "d14", "d30", "mtd"}
        if set(ranges) - supported:
            raise ValueError("range_keys contains an unsupported range")
        object.__setattr__(self, "codes", codes)
        object.__setattr__(self, "range_keys", ranges)

    @property
    def ranges(self) -> tuple[str, ...]:
        if self.range_keys:
            return self.range_keys
        return ("d1", "d2", "d7", "d14", "d30", "mtd") if self.mode == "weekly" else ("d1", "d2")


@dataclass(frozen=True)
class AiResult:
    status: str
    attempts_used: int
    attempts_remaining: int
    pending_products: int
    completed_ranges: list[str] = field(default_factory=list)
    pending_ranges: list[str] = field(default_factory=list)
    failure_reason: str | None = None
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class FeedObservation:
    feed: dict
    comments: tuple[dict, ...]
    mentions: tuple[dict, ...]
    feed_mentions: tuple[dict, ...]
    observed_at: datetime
    coverage: Coverage


@dataclass(frozen=True)
class UserObservation:
    user: dict
    observed_at: datetime


@dataclass(frozen=True)
class SourcePage:
    items: tuple[FeedObservation | UserObservation, ...]
    next_cursor: Cursor | None
    exhausted: bool
    ignored: int = 0
