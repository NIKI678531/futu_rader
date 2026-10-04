"""Parent-feed qualification at the worker boundary.

The authoritative parser and policy live in :mod:`radar_db.comment_filter` so
collection, extraction, and backend reads cannot drift. This module keeps the
worker rule provenance and the route object consumed by the existing AI queue.
Every reply either inherits its qualifying parent feed's anchor or is excluded.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from radar_db.comment_filter import (
    CommentFilterConfig,
    FilterPolicy,
    extract_feed_mentions,
    load_comment_filter_config,
    normalize_source_ticker,
    qualifies_feed,
)


EXACT_RULE = "parent_feed_filter"
EXACT_RULE_VERSION = "parent-feed-v1"


def normalize_futu_ticker(value: str) -> str | None:
    """Convert a strict FUTU ticker to Radar's legacy canonical code."""

    raw = normalize_source_ticker(value)
    if raw is None:
        return None
    symbol, market = raw.rsplit(".", 1)
    if not symbol or not market or len(market) < 2 or len(market) > 4:
        return None
    if market == "HK":
        return (symbol.lstrip("0") or "0") if symbol.isdigit() else symbol
    return f"{market.lower()}:{symbol}"


def extract_futu_cashtags(text: str | None) -> tuple[str, ...]:
    """Return legacy canonical codes for strict parent-text cashtags."""

    values: list[str] = []
    for mention in extract_feed_mentions(None, text):
        code = normalize_futu_ticker(mention.raw_ticker)
        if code is not None:
            values.extend([code] * mention.occurrences)
    return tuple(values)


class ExactProductMatcher:
    """Compatibility adapter that recognizes strict cashtags only.

    Comment routing no longer consults this object. Existing preview and
    calibration callers may keep constructing it until their compatibility
    shim is removed separately.
    """

    def __init__(self, names_by_code: Mapping[str, Iterable[str]]):
        self.pool_codes = frozenset(str(code) for code in names_by_code)

    def codes_in(self, text: str | None) -> tuple[str, ...]:
        return tuple(sorted({
            code for code in extract_futu_cashtags(text) if code in self.pool_codes
        }))


@dataclass(frozen=True)
class ExactRoute:
    subject_codes: tuple[str, ...]
    anchor_rejected: bool
    reason: str
    matched_tickers: tuple[str, ...]


def route_parent_feed(
    *,
    anchor_code: str,
    source_ticker: str | None,
    mentioned_tickers: Iterable[str],
    config: CommentFilterConfig | None = None,
    policy: FilterPolicy | None = None,
) -> ExactRoute:
    """Route every reply according to its parent feed qualification."""

    selected_policy = policy or (config or load_comment_filter_config()).policy_for(anchor_code)
    tickers = tuple(sorted({
        str(value).strip().upper() for value in mentioned_tickers if str(value).strip()
    }))
    qualified = qualifies_feed(
        source_ticker,
        tickers,
        selected_policy,
        source_code=anchor_code,
    )
    return ExactRoute(
        subject_codes=(str(anchor_code),) if qualified else (),
        anchor_rejected=not qualified,
        reason=(
            f"parent_{selected_policy.mode}_qualified"
            if qualified
            else f"parent_{selected_policy.mode}_filtered"
        ),
        matched_tickers=tickers,
    )


def route_exact_comment(
    text: str | None,
    *,
    anchor_code: str,
    parent_body_codes: Iterable[str],
    matcher: ExactProductMatcher | None = None,
    source_ticker: str | None = None,
    policy: FilterPolicy | None = None,
) -> ExactRoute:
    """Compatibility wrapper with parent-only semantics.

    ``text`` and ``matcher`` are deliberately ignored: a reply cashtag or name
    can neither rescue an unqualified parent nor route into another product.
    """

    del text, matcher
    source_ticker = source_ticker or (
        f"{str(anchor_code).zfill(5)}.HK" if str(anchor_code).isdigit() else None
    )
    raw_parent_tickers = (
        value if "." in str(value) else f"{str(value).zfill(5)}.HK"
        for value in parent_body_codes
    )
    return route_parent_feed(
        anchor_code=anchor_code,
        source_ticker=source_ticker,
        mentioned_tickers=raw_parent_tickers,
        policy=policy,
    )
