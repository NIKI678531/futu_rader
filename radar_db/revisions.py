import uuid

from sqlalchemy import insert, update

from .schema import meta_kv


def bump_revision(conn, domain):
    key = f"{domain}_revision"
    revision = uuid.uuid4().hex
    result = conn.execute(update(meta_kv).where(meta_kv.c.k == key).values(v=revision))
    if not result.rowcount:
        conn.execute(insert(meta_kv).values(k=key, v=revision))
    return revision


def mark_synthesis(conn, codes, dirty, ranges=("d1", "d2", "d7", "d14", "d30", "mtd")):
    for code in set(codes):
        if not code:
            continue
        for range_key in ranges:
            key = f"synth_dirty_{code}_{range_key}"
            value = "1" if dirty else "0"
            if not conn.execute(update(meta_kv).where(meta_kv.c.k == key).values(v=value)).rowcount:
                conn.execute(insert(meta_kv).values(k=key, v=value))