from datetime import date

import pytest
from sqlalchemy import insert

from ai import config
from jobs.full_own import prepare, product_codes, queue_status
from radar_db import create_all, make_engine
from radar_db.leases import WorkerLease
from radar_db.schema import meta_kv


def test_product_codes_own_is_61_and_all_is_120():
    import json
    from pathlib import Path

    master = json.loads((Path(__file__).resolve().parents[2] / "backend/fixtures/demo/master.json")
                        .read_text(encoding="utf-8"))
    own, scope = product_codes(master)
    assert scope == "own" and len(own) == 61 and len(set(own)) == 61
    everything, scope_all = product_codes(master, all_products=True)
    assert scope_all == "all" and len(everything) == 120
    assert set(own) < set(everything)
    with pytest.raises(SystemExit):
        product_codes({"products": master["products"][:100]}, all_products=True)
    with pytest.raises(SystemExit):
        product_codes({"products": [p for p in master["products"] if p["ownership"] == "own"][:60]})


def test_prepare_reuses_scopes_and_leases_exclude_other_workers(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "db.sqlite").as_posix())
    create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(meta_kv).values(k="anchor", v="2026-08-25"))
    cfg = config.load(model="test", prompt_version="comment-product-v2", schema_version="v2", taxonomy_version="v2")
    scopes, start = prepare(engine, cfg, date(2026, 8, 25), ["3033"])
    repeated, _ = prepare(engine, cfg, date(2026, 8, 25), ["3033"])
    assert scopes == repeated
    assert start == date(2026, 6, 27)
    assert queue_status(engine, scopes["3033"]) == {}
    from radar_db.revisions import bump_revision
    with engine.begin() as conn:
        bump_revision(conn, "data")
    changed, _ = prepare(engine, cfg, date(2026, 8, 25), ["3033"])
    assert changed != scopes
    with WorkerLease(engine, "test"):
        with pytest.raises(RuntimeError, match="already holds"):
            with WorkerLease(engine, "test"):
                pytest.fail("Second worker acquired the lease")