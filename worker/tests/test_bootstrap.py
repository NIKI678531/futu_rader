import os
import sys
from datetime import date
from types import SimpleNamespace

from sqlalchemy import create_engine, insert
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jobs import bootstrap  # noqa: E402
from radar_db.schema import meta_kv, metadata  # noqa: E402


def _engine():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    return engine


def test_existing_anchor_skips_the_source_sync():
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(insert(meta_kv).values(k="anchor", v="2026-10-13"))

    result = bootstrap.ensure_anchor(
        engine,
        today=date(2026, 10, 14),
        sync=lambda _today: (_ for _ in ()).throw(AssertionError("sync must not run")),
    )

    assert result == {"status": "ready", "anchor": "2026-10-13", "sync": "skipped"}


def test_empty_database_runs_the_real_sync_seam_until_it_publishes_an_anchor():
    engine = _engine()

    def sync(today):
        assert today == date(2026, 10, 14)
        with engine.begin() as conn:
            conn.execute(insert(meta_kv).values(k="anchor", v="2026-10-13"))
        return {"status": "succeeded", "completeThrough": "2026-10-13"}

    result = bootstrap.ensure_anchor(engine, today=date(2026, 10, 14), sync=sync)

    assert result == {
        "status": "ready",
        "anchor": "2026-10-13",
        "sync": {"status": "succeeded", "completeThrough": "2026-10-13"},
    }


def test_bootstrap_fails_closed_when_sync_publishes_no_anchor():
    result = bootstrap.ensure_anchor(
        _engine(),
        today=date(2026, 10, 14),
        sync=lambda _today: {"status": "succeeded", "completeThrough": None},
    )

    assert result == {
        "status": "blocked",
        "anchor": None,
        "sync": {"status": "succeeded", "completeThrough": None},
        "reason": "anchor_missing_after_sync",
    }


def test_bootstrap_cli_returns_nonzero_until_anchor_exists(monkeypatch, capsys):
    monkeypatch.setattr(
        bootstrap,
        "ensure_anchor",
        lambda _engine: {
            "status": "blocked",
            "anchor": None,
            "sync": {"status": "noop"},
            "reason": "anchor_missing_after_sync",
        },
    )
    monkeypatch.setattr(
        bootstrap,
        "make_engine",
        lambda: SimpleNamespace(dispose=lambda: None),
    )

    assert bootstrap.main() == 2
    assert '"reason": "anchor_missing_after_sync"' in capsys.readouterr().out


def test_bootstrap_error_output_redacts_database_passwords():
    message = bootstrap._safe_error(
        RuntimeError("mysql+pymysql://reader:top-secret@db/source refused")
    )

    assert "top-secret" not in message
    assert "<REDACTED>" in message
