"""Canonical public links for Futu community posts."""

import re
from urllib.parse import urlsplit


_FEED_ID = re.compile(r"[1-9][0-9]*")
_LEGACY_FEED_PATH = re.compile(r"/(?:post|feed)/([1-9][0-9]*)/?")
_FUTU_FEED_HOSTS = frozenset({"www.futunn.com", "q.futunn.com"})


def futu_feed_url(feed_id):
    """Return the public 牛牛圈 URL for one Futu feed id."""
    if isinstance(feed_id, bool):
        raise ValueError("Futu feed id must be a positive integer")
    value = str(feed_id).strip()
    if not _FEED_ID.fullmatch(value):
        raise ValueError(f"Invalid Futu feed id: {feed_id!r}")
    return f"https://q.futunn.com/feed/{value}?lang=zh-cn"


def canonical_futu_feed_url(url):
    """Upgrade a legacy Futu ``/post/<id>`` URL; leave other URLs untouched."""
    if not isinstance(url, str) or not url.strip():
        return url
    value = url.strip()
    if "futunn.com/" not in value:
        return value
    parsed = urlsplit(value)
    match = _LEGACY_FEED_PATH.fullmatch(parsed.path)
    if parsed.hostname in _FUTU_FEED_HOSTS and match:
        return futu_feed_url(match.group(1))
    return value
