import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, update

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from radar_db import make_engine
from radar_db.schema import feeds, comments, meta_kv
from radar_db.source_facts import prepare_source_state


@pytest.fixture()
def direct_source(tmp_path, monkeypatch):
    monkeypatch.delenv("RADAR_FACT_SOURCE", raising=False)
    monkeypatch.delenv("RADAR_DB_URL", raising=False)
    monkeypatch.delenv("MARKET_INSIGHT_DATABASE_URL", raising=False)
    source_url = "sqlite:///" + (tmp_path / "market_insight.db").as_posix()
    state_url = "sqlite:///" + (tmp_path / "state.db").as_posix()
    source = create_engine(source_url)
    with source.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE futu_comments_feeds (feed_id INTEGER PRIMARY KEY,stock_id INTEGER,"
                             "feed_type INTEGER,posted_at TEXT,author_uid TEXT,author_name TEXT,feed_title TEXT,"
                             "content_text TEXT,like_count INTEGER,comment_count INTEGER,image_count INTEGER,"
                             "raw_json TEXT,scraped_at TEXT,detail_updated_at TEXT)")
        payload = json.dumps({"share_count": "0", "browse_count": "45", "comment": {
            "has_more": False, "comment_items": [
                {"comment_id": 101, "content": "source reply", "author_uid": "u2", "author_name": "source kol", "like_count": 2},
                {"comment_id": 102, "content": "nested reply", "reply_to_comment_id": 101},
            ],
        }})
        conn.exec_driver_sql("INSERT INTO futu_comments_feeds VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            1, 900, 1, "2026-08-25 01:00:00", "u1", "source author", "$03033.HK$", "source body",
            4, 2, 0, payload, "2026-08-26 01:00:00", None,
        ))
    report = prepare_source_state(state_url, source_url, stocks={900: "03033.HK"}, complete_through="2026-08-25")
    assert report["copiedBodies"] == 0
    monkeypatch.setenv("MARKET_INSIGHT_DATABASE_URL", source_url)
    monkeypatch.setenv("RADAR_DB_URL", state_url)
    monkeypatch.setenv("RADAR_FACT_SOURCE", "market_insight")
    yield source, state_url
    source.dispose()


def test_direct_relations_and_ai_context_without_copied_bodies(direct_source):
    from jobs.annotate import _load_sources, _comment_candidates
    source, state_url = direct_source
    state = create_engine(state_url)
    with state.connect() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM feeds").scalar_one() == 0
        assert conn.exec_driver_sql("SELECT count(*) FROM comments").scalar_one() == 0
    state.dispose()
    engine = make_engine()
    with engine.connect() as conn:
        feed = conn.execute(select(feeds)).mappings().one()
        assert feed["content"] == "source body"
        assert feed["posted_at"] == datetime(2026, 8, 25, 1)
        assert feed["share_count"] == 0
        assert conn.execute(select(comments.c.content).where(comments.c.comment_id == 101)).scalar_one() == "source reply"
        with pytest.raises(Exception):
            conn.execute(update(feeds).values(content="must not write"))
    contexts = _load_sources(engine, "comment_product", [{"target_id": 102}])
    assert contexts[("comment", 102)]["parent"] == "source reply"
    assert contexts[("comment", 102)]["post_content"] == "source body"
    candidates = list(_comment_candidates(engine, codes=["3033"], comment_routes=True))
    assert [row.comment_id for row in candidates] == [101, 102]
    engine.dispose()


def test_default_mode_keeps_existing_facts_and_does_not_open_source(tmp_path, monkeypatch):
    monkeypatch.delenv("RADAR_FACT_SOURCE", raising=False)
    monkeypatch.setenv("MARKET_INSIGHT_DATABASE_URL", "sqlite:///missing-market-insight.db")
    engine = make_engine("sqlite:///" + (tmp_path / "legacy.db").as_posix())
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE sentinel (value TEXT)")
        conn.exec_driver_sql("INSERT INTO sentinel VALUES ('unchanged')")
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT value FROM sentinel").scalar_one() == "unchanged"
    engine.dispose()


@pytest.mark.parametrize("posted_at", ["2026-08-25 01:00:00", "2026-08-25T09:00:00+08:00",
                                      "2026-08-25T01:00:00.123456Z"])
def test_direct_fact_fields_match_normalizer_and_timestamp_boundaries(direct_source, posted_at):
    from radar_db.source_normalization import normalize_feed

    source, state_url = direct_source
    payload = json.dumps({"user_info": {"user_id": "fallback-uid", "nick_name": "fallback-name"},
                          "common": {"share_count": 0, "browse_count": 42, "original_lang": 1},
                          "comment": {"has_more": False, "comment_items": [
                              {"comment_id": 101, "content": "reply", "timestamp": "2026-08-25T09:00:00+08:00",
                               "author": {"user_id": "u2", "nick_name": "reply author"}, "like_count": 0},
                              {"comment_id": 102, "content": None, "reply_to_comment_id": 101},
                          ]}})
    with source.begin() as conn:
        conn.exec_driver_sql("UPDATE futu_comments_feeds SET posted_at=?,author_uid=NULL,author_name=NULL,raw_json=?",
                             (posted_at, payload))
        raw = dict(conn.exec_driver_sql("SELECT * FROM futu_comments_feeds").mappings().one())
    expected = normalize_feed(raw, "3033", raw["scraped_at"], source_ticker="03033.HK")
    prepare_source_state(state_url, str(source.url), stocks={900: "03033.HK"}, complete_through="2026-08-25")
    engine = make_engine()
    with engine.connect() as conn:
        assert dict(conn.execute(select(feeds)).mappings().one()) == expected.feed
        assert [dict(row) for row in conn.execute(select(comments).order_by(comments.c.comment_id)).mappings()] == list(expected.comments)
        assert conn.execute(select(comments.c.comment_id).where(
            comments.c.posted_at >= datetime(2026, 8, 25, 1),
            comments.c.posted_at <= datetime(2026, 8, 25, 1),
        )).scalars().all() == [101]
    engine.dispose()


def test_direct_users_preserve_source_profiles_and_comment_authors(tmp_path, monkeypatch):
    from radar_db.schema import users

    monkeypatch.delenv("MARKET_INSIGHT_DATABASE_URL", raising=False)
    source_url = "sqlite:///" + (tmp_path / "source.db").as_posix()
    state_url = "sqlite:///" + (tmp_path / "state.db").as_posix()
    source = create_engine(source_url)
    with source.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE futu_comments_feeds (feed_id INTEGER PRIMARY KEY,stock_id INTEGER,"
                             "feed_type INTEGER,posted_at TEXT,author_uid TEXT,author_name TEXT,feed_title TEXT,"
                             "content_text TEXT,like_count INTEGER,comment_count INTEGER,image_count INTEGER,"
                             "raw_json TEXT,scraped_at TEXT,detail_updated_at TEXT)")
        conn.exec_driver_sql("CREATE TABLE futu_comments_users (user_id TEXT PRIMARY KEY,nick_name TEXT,"
                             "follower_num INTEGER,following_num INTEGER,ip_region TEXT,self_description TEXT)")
        conn.exec_driver_sql("INSERT INTO futu_comments_users VALUES ('u1','profile name',123,7,'HK','profile bio')")
        payload = json.dumps({"comment": {"has_more": False, "comment_items": [
            {"comment_id": 101, "content": "reply", "author_uid": "u2", "author_name": "comment author"},
        ]}})
        conn.exec_driver_sql("INSERT INTO futu_comments_feeds VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            1, 900, 1, "2026-08-25 01:00:00", "u1", "post name", "$03033.HK$", "body",
            4, 1, 0, payload, "2026-08-26 01:00:00", None,
        ))
    prepare_source_state(state_url, source_url, stocks={900: "03033.HK"})
    monkeypatch.setenv("RADAR_FACT_SOURCE", "market_insight")
    monkeypatch.setenv("MARKET_INSIGHT_DATABASE_URL", source_url)
    monkeypatch.setenv("RADAR_DB_URL", state_url)
    engine = make_engine()
    with engine.connect() as conn:
        profiles = {row["user_id"]: dict(row) for row in conn.execute(select(users)).mappings()}
        assert profiles["u1"] == {"user_id": "u1", "nick_name": "profile name", "follower_num": 123,
                                  "following_num": 7, "ip_region": "HK", "self_description": "profile bio"}
        assert profiles["u2"]["nick_name"] == "comment author"
        assert profiles["u2"]["follower_num"] is None
        assert conn.exec_driver_sql("SELECT count(*) FROM main.users").scalar_one() == 0
    engine.dispose()
    source.dispose()


def test_direct_provider_reads_source_while_state_facts_are_empty(direct_source):
    from providers.sql import SqlProvider
    provider = SqlProvider()
    assert provider.updated_at == "2026-08-25 09:00"
    assert provider._sources("comment", [101])
    assert provider.collection_metadata()["factSource"] == "market_insight"
    assert provider._qualified_feed_ids(datetime(2026, 8, 25, 1), datetime(2026, 8, 26)) == {"3033": {1}}
    assert provider.pool("d1") is not None
    provider._meta["source_window_from"] = "2026-08-25 08:00:00+08:00"
    assert not provider._filter_window_ready("2026-08-25", "2026-08-25")
    assert provider._filter_window_ready("2026-08-26", "2026-08-26")
    provider._engine.dispose()


def test_direct_mode_daily_refresh_never_materializes(direct_source, monkeypatch):
    from jobs import daily, refresh
    monkeypatch.setattr(refresh, "_refresh", lambda **kwargs: pytest.fail("must not synchronize facts"))
    assert daily.sync_step(datetime(2026, 8, 26).date())["copiedBodies"] == 0
    with pytest.raises(ValueError, match="must not materialize"):
        refresh.sync_once()


def test_source_change_fails_closed(direct_source):
    source, _state_url = direct_source
    engine = make_engine()
    with engine.connect() as conn:
        assert conn.execute(select(feeds.c.feed_id)).scalar_one() == 1
    with source.begin() as conn:
        conn.exec_driver_sql("UPDATE futu_comments_feeds SET content_text='changed'")
    with pytest.raises(ValueError, match="Source snapshot changed"):
        engine.connect()
    engine.dispose()


def test_source_change_during_checked_out_connection_fails_closed(direct_source):
    source, _state_url = direct_source
    engine = make_engine()
    with engine.connect() as conn:
        with source.begin() as source_conn:
            source_conn.exec_driver_sql("UPDATE futu_comments_feeds SET content_text='changed'")
        with pytest.raises(ValueError, match="Source snapshot changed"):
            conn.execute(select(feeds.c.content))
    engine.dispose()


def test_direct_mode_cannot_default_to_current_radar(monkeypatch):
    monkeypatch.setenv("RADAR_FACT_SOURCE", "market_insight")
    monkeypatch.delenv("RADAR_DB_URL", raising=False)
    with pytest.raises(ValueError, match="explicit dedicated"):
        make_engine()


def test_ai_writes_and_synthesis_reads_source_without_fact_copies(direct_source, tmp_path):
    from ai import config
    from jobs import annotate, extract, synthesize
    from test_annotate_v2 import FakeProvider, v2_item
    from test_synthesize import FakeSynthProvider
    from radar_db.annotations_read import current_annotations
    from radar_db.schema import annotation_jobs, synthesis_outputs

    engine = make_engine()
    cfg = config.load(model="direct-test", micro_batch_size=2, max_retries=1,
                      prompt_version="comment-product-v2", schema_version="v2",
                      taxonomy_version="v2", concurrency=1)
    extract.run(engine, cfg, codes=["3033"], date_from=datetime(2026, 8, 25),
                date_to=datetime(2026, 8, 25), drop_offpool=False, report_dir=tmp_path)
    annotate.enqueue_comments(engine, cfg, codes=["3033"])

    def response(ids):
        return {"results": [v2_item(item_id, evidence="source reply" if ":101|" in item_id else "nested reply")
                            for item_id in ids]}

    provider = FakeProvider([response, response])
    stats = annotate.run(engine, cfg, max_items=2, provider=provider)
    assert stats["success"] == 2
    assert provider.messages and all("source body" in message for message in provider.messages)
    current = current_annotations(engine, "attitude", "comment", ids=[101, 102])
    assert set(current) == {(101, "3033"), (102, "3033")}
    result = synthesize.run(engine, cfg, codes=["3033"], ranges=["d1"],
                            kinds=["hot_summary"], provider=FakeSynthProvider())
    assert result["errors"] == 0
    with engine.connect() as conn:
        assert set(conn.execute(select(annotation_jobs.c.status)).scalars()) == {"done"}
        assert conn.execute(select(synthesis_outputs.c.synthesis_id)).first() is not None
        assert conn.exec_driver_sql("SELECT count(*) FROM main.comments").scalar_one() == 0
        assert conn.exec_driver_sql("SELECT count(*) FROM main.feeds").scalar_one() == 0
    engine.dispose()


def test_five_page_api_contracts_in_direct_mode(direct_source, monkeypatch):
    pytest.importorskip("flask", reason="API checks run with the backend interpreter")
    from app import create_app
    from providers import reset_provider, get_provider
    monkeypatch.setenv("DATA_PROVIDER", "sql")
    reset_provider()
    client = create_app().test_client()
    endpoints = ["meta", "version", "ranges/d1", "pool?range=d1", "ranks?range=d1",
                 "hot-summaries?range=d1", "officials/posts?range=d1", "kol/impact?range=d1"]
    endpoints += [f"products/3033/{name}?range=d1" for name in (
        "benchmark", "heat-series", "daily", "candles", "stages", "summary", "market-outlook",
        "themes", "negative-categories", "competitors", "compliance", "topics", "kol-mentions",
    )]
    provider = get_provider()
    endpoints += [f"kol/{provider._kols[0]['name']}/opinions?range=d1",
                  f"officials/{provider._officials[0]['short']}/etf-mentions?range=d1",
                  "products/3033/evidence?ctx=d1%7Csum&polarity=neutral&n=3"]
    try:
        for endpoint in endpoints:
            response = client.get("/api/v1/" + endpoint)
            assert response.status_code == 200, (endpoint, response.get_data(as_text=True)[:250])
    finally:
        provider._engine.dispose()
        reset_provider()


def test_post_and_kol_tasks_use_direct_source(direct_source):
    from ai import config
    from ai.providers.base import Completion, Usage
    from jobs import annotate
    from test_kol_opinion import ProvV2
    from radar_db.annotations_read import current_annotations

    class PostProvider:
        def complete_json(self, system, user, schema, name):
            assert "source body" in user
            ids = [line.split('"')[3] for line in user.splitlines() if '"item_id"' in line]
            data = {"results": [{"item_id": value, "post_type": "other", "summary": "source body",
                                 "direction": None, "direction_pending": False,
                                 "evidence_spans": ["source body"], "needs_review": False}
                                for value in ids]}
            return Completion(data=data, model="test", usage=Usage(10, 5, 0, 0), raw_text="", response_id="test")

    engine = make_engine()
    cfg = config.load(model="test", prompt_version="comment-product-v2", schema_version="v2",
                      taxonomy_version="v2", concurrency=1, max_retries=1)
    assert annotate.enqueue_posts(engine, cfg, codes=["3033"]) == 1
    assert annotate.run(engine, cfg, task="post_annotation", max_items=1,
                        provider=PostProvider())["success"] == 1
    assert current_annotations(engine, "summary", "feed", ids=[1])
    assert annotate.enqueue_kol_comments(engine, cfg, ["source kol"]) == 1
    provider = ProvV2(summary="source reply", action="未提及操作", evidence="source reply", post_type="other")
    assert annotate.run(engine, cfg, task="kol_comment_opinion", max_items=1, provider=provider)["success"] == 1
    assert current_annotations(engine, "kol_summary", "comment", ids=[101])
    engine.dispose()


def test_full_alignment_audit_detects_fields_missing_users_and_preserves_data(direct_source, tmp_path):
    from radar_db import schema
    from radar_db.source_facts import audit_source_alignment
    from radar_db.source_normalization import normalize_feed

    source, _state_url = direct_source
    legacy_url = "sqlite:///" + (tmp_path / "legacy.db").as_posix()
    legacy = create_engine(legacy_url)
    schema.metadata.create_all(legacy)
    with source.connect() as conn:
        row = dict(conn.exec_driver_sql("SELECT * FROM futu_comments_feeds").mappings().one())
    observation = normalize_feed(row, "3033", row["scraped_at"], source_ticker="03033.HK")
    with legacy.begin() as conn:
        conn.execute(schema.feeds.insert(), [observation.feed])
        conn.execute(schema.comments.insert(), list(observation.comments))
        conn.execute(schema.mentions.insert(), [{**item, "in_pool": True} for item in observation.mentions])
        conn.execute(schema.feed_mentions.insert(), list(observation.feed_mentions))
        conn.execute(schema.users.insert(), [{"user_id": "u1", "nick_name": "name", "follower_num": 10}])
    report = audit_source_alignment(legacy_url, str(source.url), stocks={900: "03033.HK"}, page_size=1)
    assert report["status"] == "blocked"
    assert report["blockers"] == ["missing_source_table:futu_comments_users"]
    assert report["missingUserFields"]["follower_num"] == 1
    assert report["tables"]["feeds"] == {"checked": 1, "missing": 0, "extra": 0, "different": 0, "fields": {}}
    with source.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE futu_comments_users (user_id TEXT PRIMARY KEY,nick_name TEXT,"
                             "follower_num INTEGER,following_num INTEGER,ip_region TEXT,self_description TEXT)")
        conn.exec_driver_sql("INSERT INTO futu_comments_users VALUES ('u1','name',10,NULL,NULL,NULL)")
    report = audit_source_alignment(legacy_url, str(source.url), stocks={900: "03033.HK"}, page_size=1)
    assert report["status"] == "legacy_facts_matched"
    assert report["productionReady"] is False
    with source.begin() as conn:
        conn.exec_driver_sql("UPDATE futu_comments_feeds SET like_count=99,content_text='changed'")
    report = audit_source_alignment(legacy_url, str(source.url), stocks={900: "03033.HK"})
    assert report["blockers"] == ["fact_mismatch:feeds"]
    assert report["tables"]["feeds"]["fields"] == {"content": 1, "like_count": 1}
    assert "changed" not in json.dumps(report)
    with legacy.connect() as conn:
        assert conn.execute(select(schema.feeds.c.like_count)).scalar_one() == 4
    legacy.dispose()


def test_alignment_audit_uses_one_legacy_read_snapshot(direct_source, tmp_path, monkeypatch):
    from radar_db import schema, source_facts
    from radar_db.source_normalization import normalize_feed

    source, _state_url = direct_source
    legacy_url = "sqlite:///" + (tmp_path / "legacy.db").as_posix()
    legacy = create_engine(legacy_url)
    schema.metadata.create_all(legacy)
    with legacy.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    with source.connect() as conn:
        raw = dict(conn.exec_driver_sql("SELECT * FROM futu_comments_feeds").mappings().one())
    expected = normalize_feed(raw, "3033", raw["scraped_at"], source_ticker="03033.HK")
    with legacy.begin() as conn:
        conn.execute(schema.feeds.insert(), [expected.feed])
        conn.execute(schema.comments.insert(), list(expected.comments))
        conn.execute(schema.mentions.insert(), [{**item, "in_pool": True} for item in expected.mentions])
        conn.execute(schema.feed_mentions.insert(), list(expected.feed_mentions))
    original = source_facts.normalize_feed

    def mutate_after_old_feed_read(*args, **kwargs):
        with legacy.begin() as conn:
            conn.execute(schema.comments.update().values(content="concurrent edit"))
        return original(*args, **kwargs)

    monkeypatch.setattr(source_facts, "normalize_feed", mutate_after_old_feed_read)
    report = source_facts.audit_source_alignment(legacy_url, str(source.url), stocks={900: "03033.HK"})
    assert report["tables"]["comments"]["different"] == 0
    assert report["blockers"] == ["missing_source_table:futu_comments_users"]
    with legacy.connect() as conn:
        assert conn.execute(select(schema.comments.c.content).limit(1)).scalar_one() == "concurrent edit"
    legacy.dispose()