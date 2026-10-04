"""Parent-feed cashtag extraction and qualification shared by all read/write paths.

The module intentionally has one small interface for both Python decisions and
SQLAlchemy queries.  It only understands facts present on the parent feed:
``source_ticker`` and strict FUTU cashtags extracted from ``title + content``.
Replies, product aliases, rich-text metadata, and AI output are outside this
module's seam.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from sqlalchemy import and_, exists, false, func, literal, or_, select
from sqlalchemy.sql.elements import ColumnElement

from .schema import feed_mentions, meta_kv


FilterMode = Literal["exact", "exclude"]

DEFAULT_CONFIG_PATH = Path(__file__).with_name("comment_filters.json")

COMMENT_FILTER_READY_META_KEY = "comment_filter_ready"
COMMENT_FILTER_VERSION_META_KEY = "comment_filter_config_version"
COMMENT_FILTER_DIGEST_META_KEY = "comment_filter_config_digest"
COMMENT_FILTER_READY_VALUE = "1"

_PRODUCT_CODE = re.compile(r"^[1-9][0-9]{3}$")
_CASHTAG_CANDIDATE = re.compile(r"\$([^$]{1,120})\$")
_TICKER = re.compile(r"^[.\-A-Za-z0-9]{1,20}\.[A-Za-z]{2,4}$")
_PREFIX_TICKER = re.compile(
    r"^(?P<market>[A-Za-z]{2,4})\.(?P<symbol>[.\-A-Za-z0-9]{1,20})$"
)
_SOURCE_PREFIX_MARKETS = frozenset({"HK", "US", "SH", "SZ", "SG", "JP", "KR"})
_TRAILING_TICKER = re.compile(
    r"[\(（]\s*(?P<ticker>[.\-A-Za-z0-9]{1,20}\.[A-Za-z]{2,4})\s*[\)）]\s*$"
)


class CommentFilterConfigError(ValueError):
    """Raised when the versioned filter configuration is malformed."""


@dataclass(frozen=True)
class CashtagMention:
    """One distinct ticker extracted from a parent feed."""

    raw_ticker: str
    market: str
    occurrences: int

    def __post_init__(self) -> None:
        raw_ticker = str(self.raw_ticker).strip()
        ticker = normalize_source_ticker(raw_ticker)
        if ticker is None:
            raise ValueError(f"invalid cashtag ticker: {self.raw_ticker!r}")
        market = ticker.rsplit(".", 1)[1]
        if str(self.market).strip().upper() != market:
            raise ValueError(
                f"cashtag market {self.market!r} does not match ticker {ticker!r}"
            )
        if isinstance(self.occurrences, bool) or not isinstance(self.occurrences, int):
            raise TypeError("cashtag occurrences must be an integer")
        if self.occurrences < 1:
            raise ValueError("cashtag occurrences must be positive")
        # ``raw_ticker`` is an audit fact copied from the parent text.  Keep its
        # original spelling; normalization belongs to comparisons, not storage.
        object.__setattr__(self, "raw_ticker", raw_ticker)
        object.__setattr__(self, "market", market)

    def as_row(self, feed_id: int) -> dict[str, int | str]:
        return {
            "feed_id": int(feed_id),
            "raw_ticker": self.raw_ticker,
            "market": self.market,
            "occurrences": self.occurrences,
        }


@dataclass(frozen=True)
class FilterPolicy:
    """Validated policy for one product discussion section."""

    mode: FilterMode = "exact"
    exclude_tickers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        mode = str(self.mode).strip().lower()
        if mode not in {"exact", "exclude"}:
            raise CommentFilterConfigError(
                f"comment filter mode must be 'exact' or 'exclude', got {self.mode!r}"
            )
        normalized: list[str] = []
        seen: set[str] = set()
        for value in self.exclude_tickers:
            ticker = normalize_source_ticker(value)
            if ticker is None:
                raise CommentFilterConfigError(f"invalid excluded ticker: {value!r}")
            if ticker not in seen:
                seen.add(ticker)
                normalized.append(ticker)
        if mode == "exact" and normalized:
            raise CommentFilterConfigError("exact policy cannot define exclude_tickers")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "exclude_tickers", tuple(sorted(normalized)))


@dataclass(frozen=True)
class CommentFilterConfig:
    """Validated, immutable configuration plus its semantic SHA-256 digest."""

    version: str
    default: FilterPolicy
    products: Mapping[str, FilterPolicy]
    digest: str

    def policy_for(self, code: str) -> FilterPolicy:
        return self.products.get(str(code).strip(), self.default)


def normalize_source_ticker(value: object) -> str | None:
    """Return one canonical ``SYMBOL.MARKET`` ticker, accepting source prefix form.

    Parent text itself remains strict FUTU suffix form.  The prefix form is
    accepted only here because some MarketInsight stock catalogs expose
    ``HK.03037`` while feed cashtags expose ``03037.HK``.
    """

    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    match = _PREFIX_TICKER.fullmatch(candidate)
    if match is not None and match.group("market").upper() in _SOURCE_PREFIX_MARKETS:
        return f"{match.group('symbol')}.{match.group('market')}".upper()
    return candidate.upper() if _TICKER.fullmatch(candidate) else None


def source_ticker_to_code(value: object) -> str | None:
    """Return a Radar product code only for a canonical five-digit HK ticker.

    Numeric symbols from another market must never be mounted into an HK
    discussion section merely because their digits happen to match.
    """

    normalized = normalize_source_ticker(value)
    if normalized is None:
        return None
    symbol, market = normalized.rsplit(".", 1)
    if market != "HK" or len(symbol) != 5 or not symbol.startswith("0"):
        return None
    code = symbol[1:]
    return code if _PRODUCT_CODE.fullmatch(code) else None


def source_ticker_matches_code(value: object, code: object) -> bool:
    """Validate the canonical ticker for a four-digit HK discussion code."""

    normalized_code = str(code).strip()
    return bool(
        _PRODUCT_CODE.fullmatch(normalized_code)
        and source_ticker_to_code(value) == normalized_code
    )


def source_ticker_valid_predicate(feed) -> ColumnElement[bool]:
    """SQL equivalent of :func:`source_ticker_matches_code` for feed rows."""

    source_ticker = feed.c.source_ticker
    expected = literal("0") + feed.c.code + literal(".HK")
    return and_(
        source_ticker.is_not(None),
        func.trim(source_ticker) != "",
        func.upper(source_ticker) == func.upper(expected),
    )


def _ticker_from_cashtag_inner(inner: str) -> str | None:
    value = inner.strip()
    if _TICKER.fullmatch(value):
        return value
    match = _TRAILING_TICKER.search(value)
    return match.group("ticker") if match else None


def extract_feed_mentions(
    title: str | None,
    content: str | None,
) -> tuple[CashtagMention, ...]:
    """Extract strict cashtags from parent ``title + content`` only.

    Tickers retain their first-seen spelling, are matched/deduplicated without
    regard to case, and retain the number of occurrences across both fields. No other payload
    field is accepted, which prevents replies and ``summary.rich_text`` from
    becoming accidental filter evidence.
    """

    # Match the MarketInsight parent-feed representation used by the
    # reference project: title and content form one cashtag parsing stream.
    # This matters when one field contains an unmatched ``$`` delimiter.
    text = f"{title or ''} {content or ''}"
    raw_by_ticker: dict[str, str] = {}
    counts: Counter[str] = Counter()
    for inner in _CASHTAG_CANDIDATE.findall(text):
        raw_ticker = _ticker_from_cashtag_inner(inner)
        ticker = normalize_source_ticker(raw_ticker)
        if raw_ticker is None or ticker is None:
            continue
        raw_by_ticker.setdefault(ticker, raw_ticker)
        counts[ticker] += 1
    return tuple(
        CashtagMention(
            raw_ticker=raw_by_ticker[ticker],
            market=ticker.rsplit(".", 1)[1],
            occurrences=occurrences,
        )
        for ticker, occurrences in counts.items()
    )


def qualifies_feed(
    source_ticker: str | None,
    mentioned_tickers: Iterable[str | CashtagMention],
    policy: FilterPolicy,
    *,
    source_code: str | None = None,
) -> bool:
    """Return whether a parent feed qualifies under ``policy``.

    Missing or malformed ``source_ticker`` always fails closed.  In exclude
    mode an explicit self mention wins even if that ticker also appears in the
    exclude list; with an empty list, every feed with a valid source ticker is
    accepted.
    """

    source = normalize_source_ticker(source_ticker)
    if source is None:
        return False
    if source_code is not None and not source_ticker_matches_code(source, source_code):
        return False
    mentioned: set[str] = set()
    for value in mentioned_tickers:
        ticker = normalize_source_ticker(
            value.raw_ticker if isinstance(value, CashtagMention) else value
        )
        if ticker is not None:
            mentioned.add(ticker)
    if policy.mode == "exact":
        return source in mentioned
    if source in mentioned:
        return True
    return not set(policy.exclude_tickers).intersection(mentioned)


def qualifying_feed_predicate(
    feed,
    policy: FilterPolicy,
    *,
    mention_table=feed_mentions,
) -> ColumnElement[bool]:
    """Build the shared correlated SQL predicate for one product policy.

    ``feed`` may be the ``feeds`` table or an alias.  The returned expression
    is intentionally section-local: it correlates mentions by ``feed_id`` and
    compares self mentions with that row's own ``source_ticker``.  Callers
    remain responsible for their existing ``feeds.code`` and time-window
    conditions.
    """

    source_ticker = feed.c.source_ticker
    has_source = source_ticker_valid_predicate(feed)

    self_mentions = mention_table.alias()
    has_self = exists(
        select(1)
        .select_from(self_mentions)
        .where(
            and_(
                self_mentions.c.feed_id == feed.c.feed_id,
                func.upper(self_mentions.c.raw_ticker) == func.upper(source_ticker),
            )
        )
        .correlate(feed)
    )
    if policy.mode == "exact":
        return and_(has_source, has_self)
    if not policy.exclude_tickers:
        return has_source

    excluded_mentions = mention_table.alias()
    has_excluded = exists(
        select(1)
        .select_from(excluded_mentions)
        .where(
            and_(
                excluded_mentions.c.feed_id == feed.c.feed_id,
                func.upper(excluded_mentions.c.raw_ticker).in_(policy.exclude_tickers),
            )
        )
        .correlate(feed)
    )
    return and_(has_source, or_(has_self, ~has_excluded))


def qualifying_feed_scope_predicate(
    feed,
    config: CommentFilterConfig,
    *,
    codes: Iterable[str] | None = None,
    mention_table=feed_mentions,
) -> ColumnElement[bool]:
    """Build one grouped predicate for a product scope.

    Product codes sharing the same policy are grouped into one ``IN`` arm, so
    callers do not need to copy config dispatch or emit one correlated
    subquery pair per product.  With ``codes=None`` the default policy covers
    every code that has no explicit override.
    """

    clauses: list[ColumnElement[bool]] = []
    if codes is None:
        grouped: dict[FilterPolicy, list[str]] = {}
        for code, policy in config.products.items():
            grouped.setdefault(policy, []).append(code)
        for policy, product_codes in grouped.items():
            clauses.append(
                and_(
                    feed.c.code.in_(tuple(product_codes)),
                    qualifying_feed_predicate(
                        feed,
                        policy,
                        mention_table=mention_table,
                    ),
                )
            )
        default_scope = (
            feed.c.code.notin_(tuple(config.products))
            if config.products
            else True
        )
        clauses.append(
            and_(
                default_scope,
                qualifying_feed_predicate(
                    feed,
                    config.default,
                    mention_table=mention_table,
                ),
            )
        )
    else:
        grouped = {}
        for raw_code in dict.fromkeys(str(code).strip() for code in codes):
            if raw_code:
                grouped.setdefault(config.policy_for(raw_code), []).append(raw_code)
        for policy, product_codes in grouped.items():
            clauses.append(
                and_(
                    feed.c.code.in_(tuple(product_codes)),
                    qualifying_feed_predicate(
                        feed,
                        policy,
                        mention_table=mention_table,
                    ),
                )
            )
    return or_(*clauses) if clauses else false()


def _object_without_duplicate_keys(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise CommentFilterConfigError(f"duplicate JSON key: {key!r}")
        value[key] = item
    return value


def _require_keys(value: Mapping[str, object], allowed: set[str], *, where: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise CommentFilterConfigError(f"unknown {where} fields: {sorted(unknown)}")


def _parse_policy(value: object, *, where: str) -> FilterPolicy:
    if not isinstance(value, Mapping):
        raise CommentFilterConfigError(f"{where} must be an object")
    _require_keys(value, {"mode", "exclude_tickers"}, where=where)
    mode = value.get("mode", "exact")
    excludes = value.get("exclude_tickers", [])
    if not isinstance(mode, str):
        raise CommentFilterConfigError(f"{where}.mode must be a string")
    if not isinstance(excludes, list) or any(not isinstance(item, str) for item in excludes):
        raise CommentFilterConfigError(f"{where}.exclude_tickers must be a string list")
    return FilterPolicy(mode=mode, exclude_tickers=tuple(excludes))


def _semantic_payload(
    version: str,
    default: FilterPolicy,
    products: Mapping[str, FilterPolicy],
) -> dict[str, object]:
    def policy_value(policy: FilterPolicy) -> dict[str, object]:
        return {"mode": policy.mode, "exclude_tickers": list(policy.exclude_tickers)}

    return {
        "version": version,
        "default": policy_value(default),
        "products": {
            code: policy_value(products[code])
            for code in sorted(products)
        },
    }


def load_comment_filter_config(
    path: str | Path | None = None,
) -> CommentFilterConfig:
    """Load, strictly validate, normalize, and hash the filter configuration."""

    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        raw = json.loads(
            config_path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except CommentFilterConfigError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CommentFilterConfigError(
            f"cannot load comment filter config {config_path}: {exc}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise CommentFilterConfigError("comment filter config must be an object")
    _require_keys(raw, {"version", "default", "products"}, where="config")

    version = raw.get("version")
    if not isinstance(version, str) or not version.strip() or len(version.strip()) > 64:
        raise CommentFilterConfigError("config.version must be a non-empty string up to 64 chars")
    version = version.strip()
    default = _parse_policy(raw.get("default"), where="config.default")

    raw_products = raw.get("products")
    if not isinstance(raw_products, Mapping):
        raise CommentFilterConfigError("config.products must be an object")
    products: dict[str, FilterPolicy] = {}
    for raw_code, raw_policy in raw_products.items():
        code = str(raw_code).strip()
        if not _PRODUCT_CODE.fullmatch(code):
            raise CommentFilterConfigError(f"invalid product code: {raw_code!r}")
        if code in products:
            raise CommentFilterConfigError(f"duplicate normalized product code: {code!r}")
        products[code] = _parse_policy(raw_policy, where=f"config.products.{code}")

    payload = _semantic_payload(version, default, products)
    material = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()
    return CommentFilterConfig(
        version=version,
        default=default,
        products=MappingProxyType(products),
        digest=digest,
    )


def load_filter_policy(
    code: str,
    path: str | Path | None = None,
) -> FilterPolicy:
    """Convenience interface for callers that only need one product policy."""

    return load_comment_filter_config(path).policy_for(code)


def filter_readiness_values(config: CommentFilterConfig) -> dict[str, str]:
    """Return the exact ``meta_kv`` values written after a successful backfill."""

    return {
        COMMENT_FILTER_READY_META_KEY: COMMENT_FILTER_READY_VALUE,
        COMMENT_FILTER_VERSION_META_KEY: config.version,
        COMMENT_FILTER_DIGEST_META_KEY: config.digest,
    }


def is_filter_ready(
    metadata_values: Mapping[str, str | None],
    config: CommentFilterConfig,
) -> bool:
    """Verify readiness and bind it to the active config version and digest."""

    expected = filter_readiness_values(config)
    return all(metadata_values.get(key) == value for key, value in expected.items())


def require_filter_ready_on_connection(
    conn,
    config: CommentFilterConfig,
    *,
    lock: bool = False,
) -> None:
    """Fail closed using the caller's transaction.

    Claiming workers use ``lock=True`` so the readiness generation cannot be
    withdrawn between the guard and the queue mutation on databases that
    support ``SELECT .. FOR UPDATE``.  SQLite ignores the locking clause but
    still evaluates the guard and the claim inside one write transaction.
    """

    keys = tuple(filter_readiness_values(config))
    query = select(meta_kv.c.k, meta_kv.c.v).where(meta_kv.c.k.in_(keys))
    if lock:
        query = query.with_for_update()
    values = dict(conn.execute(query).all())
    if not is_filter_ready(values, config):
        raise RuntimeError(
            "parent-feed comment filter is not ready for task mutation; "
            "complete and activate `python -m jobs.backfill_comment_filter --resume --activate`"
        )


def require_filter_ready(engine, config: CommentFilterConfig) -> None:
    """Fail closed before a worker mutates filter-dependent artifacts."""

    with engine.connect() as conn:
        require_filter_ready_on_connection(conn, config)
