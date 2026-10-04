import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import insert, select, update

from .schema import meta_kv


_RANGE_DAYS = (("d1", 1), ("d2", 2), ("d7", 7), ("d14", 14), ("d30", 30))
SYNTHESIS_GENERATION_PREFIX = "synth_current"


def ranges_touching(anchor, changed_from, changed_to):
    """Return analysis ranges whose current or benchmark window changed.

    Synthesis compares every current window with the immediately preceding
    equal-length benchmark, so a source correction in either window makes the
    corresponding output stale.  ``mtd`` follows the same rule using the
    anchor's elapsed month length.
    """

    def as_date(value):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value)[:10])

    anchor = as_date(anchor)
    changed_from = as_date(changed_from)
    changed_to = as_date(changed_to)
    if changed_from > changed_to:
        changed_from, changed_to = changed_to, changed_from

    touched = []
    for key, days in (*_RANGE_DAYS, ("mtd", anchor.day)):
        current_from = anchor - timedelta(days=days - 1)
        benchmark_from = current_from - timedelta(days=days)
        # Current and benchmark windows are adjacent, so their union is one
        # continuous inclusive interval ending at the anchor.
        if changed_from <= anchor and changed_to >= benchmark_from:
            touched.append(key)
    return touched


def bump_revision(conn, domain):
    key = f"{domain}_revision"
    revision = uuid.uuid4().hex
    result = conn.execute(update(meta_kv).where(meta_kv.c.k == key).values(v=revision))
    if not result.rowcount:
        conn.execute(insert(meta_kv).values(k=key, v=revision))
    return revision


def ensure_ai_revision(conn):
    """Freeze the current fact revision as the initial semantic baseline."""

    existing = conn.execute(select(meta_kv.c.v).where(
        meta_kv.c.k == "ai_input_revision"
    )).scalar()
    if existing is not None:
        return existing
    baseline = conn.execute(select(meta_kv.c.v).where(
        meta_kv.c.k == "data_revision"
    )).scalar() or uuid.uuid4().hex
    if not conn.execute(update(meta_kv).where(
        meta_kv.c.k == "ai_input_revision"
    ).values(v=baseline)).rowcount:
        conn.execute(insert(meta_kv).values(k="ai_input_revision", v=baseline))
    return baseline


def ai_source_version(values):
    """Return only state that can change model inputs.

    Older databases have no ``ai_input_revision``. Falling back to
    ``data_revision`` keeps their existing results valid until the online
    collector establishes the dedicated semantic revision.
    """

    result = {}
    semantic = values.get("ai_input_revision") or values.get("data_revision")
    if semantic:
        # Keep the historical public key so existing progress/scope JSON remains
        # reusable when a migrated database first enables the split revision.
        result["data_revision"] = semantic
    if values.get("etl_generation"):
        result["etl_generation"] = values["etl_generation"]
    # Comment routing changes the model input universe without changing the
    # collected facts.  Carry the activated generation in scopes/progress so
    # an old completion cannot be reused after a route-rule or pool change.
    if values.get("comment_route_version"):
        result["comment_route_version"] = values["comment_route_version"]
    if values.get("comment_route_pool_digest"):
        result["comment_route_pool_digest"] = values["comment_route_pool_digest"]
    return result


def mark_synthesis(conn, codes, dirty, ranges=("d1", "d2", "d7", "d14", "d30", "mtd")):
    for code in set(codes):
        if not code:
            continue
        for range_key in ranges:
            key = f"synth_dirty_{code}_{range_key}"
            value = "1" if dirty else "0"
            if not conn.execute(update(meta_kv).where(meta_kv.c.k == key).values(v=value)).rowcount:
                conn.execute(insert(meta_kv).values(k=key, v=value))


def synthesis_generation_key(code, range_key, kind):
    """Stable metadata key selecting one published Layer-B generation."""

    return f"{SYNTHESIS_GENERATION_PREFIX}_{code}_{range_key}_{kind}"


def synthesis_generation_value(anchor, fingerprint):
    return f"{str(anchor)[:10]}|{fingerprint}"


def set_synthesis_generation(conn, code, range_key, anchor, kind, fingerprint):
    """Publish exactly one input fingerprint for a synthesis kind."""

    key = synthesis_generation_key(code, range_key, kind)
    value = synthesis_generation_value(anchor, fingerprint)
    if not conn.execute(update(meta_kv).where(meta_kv.c.k == key).values(v=value)).rowcount:
        conn.execute(insert(meta_kv).values(k=key, v=value))
    return value
