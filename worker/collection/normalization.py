"""Pure source-payload normalization shared by dump and MarketInsight DB input."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from radar_db.comment_filter import extract_feed_mentions, normalize_source_ticker

from .exact import extract_futu_cashtags, normalize_futu_ticker
from .models import FeedObservation


def rich_text(items):
    if not items:
        return None
    out = []
    for segment in items:
        kind = segment.get("type")
        if kind == 0:
            out.append(segment.get("text") or "")
        elif kind == 1:
            out.append(f"[{(segment.get('emotion') or {}).get('text', '')}]")
        elif kind == 2:
            out.append("@" + ((segment.get("user") or {}).get("nick_name") or ""))
        elif kind == 3:
            stock = segment.get("stock") or {}
            code = stock.get("stock_code") or ""
            market = (stock.get("market_type_label") or "").upper()
            out.append(f"${code}.{market}$" if code else "")
        elif kind == 6:
            out.append("[表情]")
        elif kind == 7:
            out.append((segment.get("text_link") or {}).get("text") or "")
    text = "".join(out).strip()
    return text or None


def first_not_none(*values):
    for value in values:
        if value is not None:
            return value
    return None


def timestamp_from_epoch(value):
    if value in (None, "", "0", 0):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return None


def timestamp_from_source(value):
    """Normalize epoch or ISO timestamps supplied by Futu input adapters."""
    if isinstance(value, datetime):
        return (
            value.astimezone(timezone.utc).replace(tzinfo=None)
            if value.tzinfo is not None else value
        )
    epoch = timestamp_from_epoch(value)
    if epoch is not None:
        return epoch
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (
        parsed.astimezone(timezone.utc).replace(tzinfo=None)
        if parsed.tzinfo is not None else parsed
    )


def normalize_comment(item: dict, feed_id: int) -> dict | None:
    """Normalize one embedded or reply-endpoint comment without inference."""
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    value = {**raw, **item}
    raw_id = first_not_none(
        value.get("comment_id"), value.get("reply_id"), value.get("id")
    )
    if raw_id in (None, "") or isinstance(raw_id, bool):
        return None
    try:
        comment_id = int(raw_id)
    except (TypeError, ValueError):
        return None

    author = value.get("author") or value.get("user_info") or {}
    reply_to = first_not_none(
        value.get("reply_to_comment_id"), value.get("reply_to_reply_id")
    )
    try:
        reply_to = int(reply_to) if reply_to not in (None, "", "0", 0) else None
    except (TypeError, ValueError):
        reply_to = None
    like = value.get("like") or {}
    like_count = first_not_none(like.get("liked_num"), value.get("like_count"))
    try:
        like_count = int(like_count) if like_count is not None else None
    except (TypeError, ValueError):
        like_count = None

    return {
        "comment_id": comment_id,
        "feed_id": int(feed_id),
        "posted_at": timestamp_from_source(first_not_none(
            value.get("timestamp"), value.get("posted_at"), value.get("create_time")
        )),
        "author_uid": first_not_none(
            author.get("user_id"), value.get("author_uid"),
            value.get("author_id"), value.get("user_id"),
        ),
        "author_name": first_not_none(
            author.get("nick_name"), value.get("author_name"), value.get("user_name")
        ),
        "content": first_not_none(
            rich_text(value.get("rich_text_items")),
            value.get("content"), value.get("content_text"),
        ),
        "like_count": like_count,
        "reply_to_comment_id": reply_to,
    }


def mention_code(stock):
    code = stock.get("stock_code")
    if not code:
        return None
    market = stock.get("market_type_label")
    return normalize_futu_ticker(f"{code}.{market}") if market else str(code).strip()


def _raw_json(value):
    if isinstance(value, dict):
        return value, False
    try:
        parsed = json.loads(value)
        return (parsed, False) if isinstance(parsed, dict) else (None, True)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, True


def normalize_feed(
    row: dict,
    code: str,
    observed_at: datetime,
    *,
    source_ticker: str | None = None,
) -> FeedObservation:
    """Turn one MarketInsight feed row into target facts without calculating metrics."""

    payload, broken = _raw_json(row.get("raw_json"))
    common = (payload or {}).get("common") or {}
    feed_common = (payload or {}).get("feed_comm") or {}
    raw_comment = (payload or {}).get("comment")
    comment = raw_comment if isinstance(raw_comment, dict) else {}
    user_info = (payload or {}).get("user_info") or {}
    raw_items = comment.get("comment_items")
    items = raw_items if isinstance(raw_items, list) else []

    normalized_comments = []
    seen = set()
    for item in items:
        normalized = normalize_comment(item, int(row["feed_id"]))
        if normalized is None:
            continue
        comment_id = normalized["comment_id"]
        if comment_id in seen:
            continue
        seen.add(comment_id)
        normalized_comments.append(normalized)

    mentions = [{"feed_id": int(row["feed_id"]), "code": code, "source": "anchor", "in_pool": True}]
    body_codes = set()
    for segment in ((payload or {}).get("summary") or {}).get("rich_text") or []:
        stock = segment.get("stock")
        if stock:
            value = mention_code(stock)
            if value:
                body_codes.add(value)
    body_codes.update(
        extract_futu_cashtags(f"{row.get('feed_title') or ''} {row.get('content_text') or ''}")
    )
    mentions.extend(
        {"feed_id": int(row["feed_id"]), "code": value, "source": "body", "in_pool": False}
        for value in sorted(body_codes)
    )

    platform_count = row.get("comment_count")
    if broken:
        coverage = "unknown"
        parsed = None
        truncated = None
    elif not isinstance(raw_comment, dict) or not isinstance(raw_items, list):
        # A missing/malformed comment block is not proof that there are no
        # comments, even when the platform counter happens to be zero.
        parsed = len(normalized_comments)
        truncated = None
        coverage = "retryable_incomplete"
    else:
        parsed = len(normalized_comments)
        truncated = bool(comment.get("has_more"))
        if truncated or (platform_count is not None and parsed < platform_count):
            coverage = "partial"
        elif platform_count is None:
            coverage = "unknown"
        elif parsed == platform_count:
            coverage = "complete"
        else:
            # More parsed bodies than the platform total is an inconsistent
            # response, not an authoritative deletion snapshot.
            coverage = "retryable_incomplete"

    feed = {
        "feed_id": int(row["feed_id"]),
        "code": code,
        "source_ticker": (
            normalize_source_ticker(source_ticker or row.get("source_ticker") or row.get("ticker"))
        ),
        "posted_at": row["posted_at"],
        "feed_type": int(row["feed_type"]),
        "author_uid": row.get("author_uid") or user_info.get("user_id"),
        "author_name": row.get("author_name") or user_info.get("nick_name"),
        "title": row.get("feed_title"),
        "content": row.get("content_text"),
        "like_count": row.get("like_count"),
        "comment_count": platform_count,
        "image_count": row.get("image_count"),
        "share_count": first_not_none(
            (payload or {}).get("share_count"), common.get("share_count"), feed_common.get("share_count")
        ),
        "browse_count": first_not_none((payload or {}).get("browse_count"), common.get("browse_count")),
        "comments_parsed": parsed,
        "comments_truncated": truncated,
        "original_lang": common.get("original_lang") if not broken else None,
        "raw_json_broken": broken,
        "source_observed_at": observed_at,
        "comment_coverage_status": coverage,
    }
    raw_mentions = extract_feed_mentions(feed.get("title"), feed.get("content"))
    feed_mentions = tuple(
        {
            "feed_id": feed["feed_id"],
            "raw_ticker": mention.raw_ticker,
            "market": mention.market,
            "occurrences": mention.occurrences,
        }
        for mention in raw_mentions
    )
    return FeedObservation(
        feed,
        tuple(normalized_comments),
        tuple(mentions),
        feed_mentions,
        observed_at,
        coverage,
    )
