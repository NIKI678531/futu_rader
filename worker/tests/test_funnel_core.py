"""ADR-0021 基础件：事件表、按 stage 领取、整批一事务、优先级重排、近重复折叠与传播、训练集切分、迁移 0008。"""

import json
import os
import sys
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, insert, select, update

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from ai import config, neardup, schemas  # noqa: E402
from ai.providers.base import Completion, Usage  # noqa: E402
from jobs import annotate  # noqa: E402
from models import dataset, registry  # noqa: E402
from radar_db import create_all, make_engine  # noqa: E402
from radar_db import events  # noqa: E402
from radar_db.comment_filter import filter_readiness_values, load_comment_filter_config  # noqa: E402
from radar_db.comment_routes import (  # noqa: E402
    COMMENT_ROUTE_VERSION,
    readiness_values as route_readiness_values,
)
from radar_db.revisions import ranges_touching  # noqa: E402
from radar_db.schema import (  # noqa: E402
    annotation_evidence, annotation_jobs, annotation_runs, annotations, comment_product_routes,
    comments, feed_mentions, feeds, meta_kv,
    worker_events,
)

CODE = "3033"
ANCHOR = date(2026, 8, 25)


@pytest.fixture()
def cfg():
    return config.load(model="test-model", micro_batch_size=30, max_retries=3,
                       prompt_version="comment-product-v2", schema_version="v2", taxonomy_version="v2")


def _engine(tmp_path, name="t.db"):
    eng = make_engine("sqlite:///" + (tmp_path / name).as_posix())
    create_all(eng)
    with eng.begin() as conn:
        conn.execute(insert(meta_kv), [
            {"k": key, "v": value}
            for key, value in filter_readiness_values(load_comment_filter_config()).items()
        ])
    return eng


def _seed(eng, texts, *, day=datetime(2026, 8, 22, 10), code=CODE, first=11, feed_id=1):
    with eng.begin() as conn:
        conn.execute(insert(meta_kv).values(k="anchor", v=ANCHOR.isoformat()))
        conn.execute(insert(feeds).values(
            feed_id=feed_id, code=code, source_ticker=f"0{code}.HK", posted_at=day, feed_type=1,
            title="恒科今日走势", content="今日恒科低开高走",
            like_count=0, comment_count=len(texts), image_count=0, raw_json_broken=False))
        conn.execute(insert(feed_mentions).values(
            feed_id=feed_id, raw_ticker=f"0{code}.HK", market="HK", occurrences=1,
        ))
        conn.execute(insert(comments), [
            {"comment_id": first + i, "feed_id": feed_id, "content": t, "author_uid": f"u{i}"} for i, t in enumerate(texts)
        ])


def _activate_routes(conn, comment_ids, *, code=CODE, feed_id=1):
    conn.execute(insert(comment_product_routes), [
        {
            "comment_id": comment_id,
            "subject_code": code,
            "feed_id": feed_id,
            "matched_parent": True,
            "matched_comment": False,
            "rule_version": COMMENT_ROUTE_VERSION,
            "updated_at": datetime(2026, 8, 26),
        }
        for comment_id in comment_ids
    ])
    conn.execute(insert(meta_kv), [
        {"k": key, "v": value}
        for key, value in route_readiness_values().items()
    ])


# ── 事件表 ─────────────────────────────────────────────────────────────


def test_emit_returns_id_and_recent_orders_and_paginates(tmp_path):
    eng = _engine(tmp_path)
    ids = [events.emit(eng, "L1", f"m{i}", code=CODE if i % 2 else None, data={"i": i}) for i in range(5)]
    assert ids == sorted(ids) and all(ids)
    got = events.recent(eng)
    assert [e["id"] for e in got] == ids and got[0]["data"] == {"i": 0} and got[1]["code"] == CODE
    assert set(got[0]) == {"id", "ts", "level", "stage", "code", "scopeId", "runId", "message", "data"}
    assert [e["id"] for e in events.recent(eng, after=ids[2])] == ids[3:]
    assert [e["id"] for e in events.recent(eng, limit=2)] == ids[-2:]  # 最新两条，正序
    assert events.latest_id(eng) == ids[-1]
    # 非法 level / stage 归一化，不抛
    eid = events.emit(eng, "bogus", "x", level="loud")
    assert events.recent(eng, after=ids[-1])[0]["stage"] == "orchestrator"
    assert events.recent(eng, after=ids[-1])[0]["level"] == "info" and eid


def test_emit_never_raises(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "empty.db").as_posix())  # 没建表
    assert events.emit(eng, "L1", "no table") is None
    assert events.recent(eng) == [] and events.latest_id(eng) is None

    class Dead:
        def begin(self):
            raise RuntimeError("db down")

        def connect(self):
            raise RuntimeError("db down")

    assert events.emit(Dead(), "L1", "x") is None
    assert events.recent(Dead()) == []


def test_emit_trims_to_keep_rows(tmp_path):
    eng = _engine(tmp_path)
    now = datetime(2026, 9, 15)
    with eng.begin() as conn:
        conn.execute(insert(worker_events), [
            {"ts": now, "level": "info", "stage": "L1", "message": f"m{i}"} for i in range(events.MAX_ROWS)
        ])
    with eng.connect() as conn:
        assert conn.execute(select(func.count()).select_from(worker_events)).scalar_one() == events.MAX_ROWS
    events.emit(eng, "L1", "one more")  # 6001 > 6000 ⇒ 修剪
    with eng.connect() as conn:
        n = conn.execute(select(func.count()).select_from(worker_events)).scalar_one()
        oldest = conn.execute(select(func.min(worker_events.c.event_id))).scalar_one()
    assert n == events.KEEP_ROWS
    assert oldest == events.MAX_ROWS + 1 - events.KEEP_ROWS + 1
    assert events.recent(eng, limit=1)[0]["message"] == "one more"


# ── 按 stage 领取 ──────────────────────────────────────────────────────


def test_claim_by_stage_and_default_stage_per_task(tmp_path, cfg):
    eng = _engine(tmp_path)
    _seed(eng, ["评论一号这只不错", "评论二号点差太大", "评论三号费率低"])
    annotate.enqueue_comments(eng, cfg, codes=[CODE])
    with eng.begin() as conn:
        assert {r[0] for r in conn.execute(select(annotation_jobs.c.stage))} == {"student"}
        conn.execute(update(annotation_jobs).where(annotation_jobs.c.target_id == 12).values(stage="llm"))
    llm = annotate.claim(eng, "comment_product", 10, stage="llm")
    assert [j["target_id"] for j in llm] == [12]
    student = annotate.claim(eng, "comment_product", 10, stage="student")
    assert sorted(j["target_id"] for j in student) == [11, 13]
    assert annotate.claim(eng, "comment_product", 10, stage="student") == []
    # 不分段：过期租约后三条都能领
    with eng.begin() as conn:
        conn.execute(update(annotation_jobs).values(lease_until=datetime(2026, 1, 1)))
    assert len(annotate.claim(eng, "comment_product", 10)) == 3
    assert annotate.pending_count(eng, "comment_product", stage="llm") == 1
    assert annotate.default_stage("comment_product") == "student"
    assert annotate.default_stage("post_annotation") == "llm"
    assert annotate.default_stage("kol_comment_opinion") == "llm"


# ── 整批一事务 ─────────────────────────────────────────────────────────


def _v2_item(item_id, **over):
    base = {"item_id": item_id, "relevance": "relevant", "attitude": "negative", "aspects": ["spread"],
            "evidence": "点差太大", "market_direction": None, "compliance_tags": [], "compliance_rationale": None,
            "compliance_evidence": None, "needs_review": False, "uncertainty_reasons": []}
    base.update(over)
    return base


def _snapshot(eng):
    with eng.connect() as conn:
        anns = [(r.target_id, r.kind, r.value_json, r.review_state, r.supersedes_id is not None)
                for r in conn.execute(select(annotations).order_by(annotations.c.target_id, annotations.c.kind))]
        ev = [(r.source_target_id, r.start_offset, r.end_offset, r.quote_text)
              for r in conn.execute(select(annotation_evidence).order_by(annotation_evidence.c.annotation_id))]
        jobs = sorted((r.target_id, r.status, r.lease_until) for r in conn.execute(select(annotation_jobs)))
    return anns, ev, jobs


def test_write_batch_matches_per_item_path(tmp_path, cfg):
    texts = ["这只ETF点差太大，来回一趟就蚀掉不少", "费率比同行高了不少，不太划算", "恒指要崩了"]
    results = []
    for mode in ("batch", "single"):
        eng = _engine(tmp_path, f"{mode}.db")
        _seed(eng, texts)
        annotate.enqueue_comments(eng, cfg, codes=[CODE])
        jobs = annotate.claim(eng, "comment_product", 10)
        src = annotate._load_sources(eng, "comment_product", jobs)
        ids = [annotate._item_id("comment_product", j) for j in jobs]
        raw = {"results": [
            _v2_item(ids[0]), _v2_item(ids[1], evidence="费率比同行高了不少"),
            _v2_item(ids[2], relevance="irrelevant", attitude=None, aspects=[], evidence=None, market_direction="bearish"),
        ]}
        by_id = schemas.parse_batch("comment_product", raw, ids, "v2")
        with eng.begin() as conn:
            conn.execute(insert(annotation_runs).values(
                run_id="r1", task="comment_product", provider="openai_compatible", model_id="m", prompt_version="comment-product-v3",
                taxonomy_version="v2", schema_version="v2", started_at=datetime(2026, 8, 26), status="running",
                input_count=0, success_count=0, error_count=0))
        items = [(j, src[("comment", j["target_id"])], by_id[annotate._item_id("comment_product", j)]) for j in jobs]
        if mode == "batch":
            ok, bad = annotate._write_batch(eng, "comment_product", items, "r1", "v2")
            assert (ok, bad) == (3, 0)
        else:
            for j, s, it in items:
                annotate._write(eng, "comment_product", j, s, it, "r1", "v2")
                annotate._done(eng, j)
        results.append(_snapshot(eng))
    assert results[0] == results[1]
    anns, ev, jobs = results[0]
    assert all(status == "done" and lease is None for _, status, lease in jobs)
    assert len(ev) == 2  # 两条相关各定位到一段证据


def test_write_batch_isolates_the_bad_row(tmp_path, cfg, monkeypatch):
    eng = _engine(tmp_path)
    _seed(eng, ["这只ETF点差太大", "费率比同行高"])
    annotate.enqueue_comments(eng, cfg, codes=[CODE])
    jobs = annotate.claim(eng, "comment_product", 10)
    src = annotate._load_sources(eng, "comment_product", jobs)
    ids = [annotate._item_id("comment_product", j) for j in jobs]
    by_id = schemas.parse_batch("comment_product", {"results": [_v2_item(i, evidence=None, attitude="neutral")
                                                                for i in ids]}, ids, "v2")
    with eng.begin() as conn:
        conn.execute(insert(annotation_runs).values(
            run_id="r1", task="comment_product", provider="openai_compatible", model_id="m", prompt_version="comment-product-v3",
            taxonomy_version="v2", schema_version="v2", started_at=datetime(2026, 8, 26), status="running",
            input_count=0, success_count=0, error_count=0))
    real_write = annotate._write

    def flaky(engine, task, job, s, item, run_id, schema_version="v1", conn=None):
        if job["target_id"] == 12:
            raise RuntimeError("列超长")
        return real_write(engine, task, job, s, item, run_id, schema_version, conn)

    monkeypatch.setattr(annotate, "_write", flaky)
    items = [(j, src[("comment", j["target_id"])], by_id[annotate._item_id("comment_product", j)]) for j in jobs]
    ok, bad = annotate._write_batch(eng, "comment_product", items, "r1", "v2")
    assert (ok, bad) == (1, 1)
    with eng.connect() as conn:
        st = dict(conn.execute(select(annotation_jobs.c.target_id, annotation_jobs.c.status)).all())
        n_ann = conn.execute(select(func.count()).select_from(annotations)).scalar_one()
    assert st == {11: "done", 12: "failed"}
    with eng.connect() as conn:
        targets = {r[0] for r in conn.execute(select(annotations.c.target_id))}
    assert targets == {11} and n_ann == 4  # 11 的 relevance／attitude／aspect／compliance；12 一行都没留下


# ── 优先级 ─────────────────────────────────────────────────────────────


def test_recency_tier_and_job_priority():
    a = ANCHOR
    d = lambda n: datetime.combine(a - timedelta(days=n), datetime.min.time())  # noqa: E731
    assert annotate.recency_tier(d(0), a) == 30 and annotate.recency_tier(d(7), a) == 30
    assert annotate.recency_tier(d(8), a) == 20 and annotate.recency_tier(d(14), a) == 20
    assert annotate.recency_tier(d(15), a) == 10 and annotate.recency_tier(d(30), a) == 10
    assert annotate.recency_tier(d(31), a) == 0
    # mtd 窗：31 天外但同月 ⇒ 仍 +10；跨月 ⇒ 0
    late = date(2026, 10, 31)
    assert annotate.recency_tier(datetime(2026, 10, 1), late) == 10   # 30 天 → ≤30 档
    assert annotate.recency_tier(datetime(2026, 9, 30), late) == 0    # 31 天、跨月
    assert annotate.recency_tier(datetime(2026, 7, 1), date(2026, 7, 31)) == 10  # 30 天
    assert annotate.recency_tier(datetime(2026, 12, 1), date(2026, 12, 31)) == 10  # 30 天
    assert annotate.recency_tier(datetime(2026, 12, 1), date(2027, 1, 5)) == 0
    assert annotate.recency_tier(None, a) == 0 and annotate.recency_tier(d(3), None) == 0
    assert annotate.job_priority(d(3), a, own=True, current=True) == 33
    assert annotate.job_priority(d(40), a, own=False, current=False) == 0


def test_reprioritize_is_idempotent_and_reports_changes(tmp_path, cfg):
    eng = _engine(tmp_path)
    _seed(eng, ["这只不错", "点差太大"], day=datetime(2026, 8, 24, 9))            # 距锚点 1 天 → 30
    with eng.begin() as conn:
        conn.execute(insert(feeds).values(
            feed_id=2, code=CODE, posted_at=datetime(2026, 7, 10, 9), feed_type=1, title="旧帖", content="c",
            like_count=0, comment_count=1, image_count=0, raw_json_broken=False))
        conn.execute(insert(comments).values(comment_id=20, feed_id=2, content="很久以前的评论", author_uid="u9"))
    annotate.enqueue_comments(eng, cfg, codes=[CODE])
    with eng.begin() as conn:
        conn.execute(update(annotation_jobs).values(priority=0))
    n = annotate.reprioritize(eng, ownership={CODE: "own"})
    assert n == 3
    with eng.connect() as conn:
        prio = dict(conn.execute(select(annotation_jobs.c.target_id, annotation_jobs.c.priority)).all())
    assert prio[11] == prio[12] == 33 and prio[20] == 3   # 无 scope ⇒ 全算当前期：own +2 current +1
    assert annotate.reprioritize(eng, ownership={CODE: "own"}) == 0
    assert annotate.reprioritize(eng, ownership={CODE: "own"}, dry_run=True) == 0
    # 锚点前移 20 天：8/24 落到 ≤30 档（两条改）；7/10 的仍是 0 档（不改）
    assert annotate.reprioritize(eng, anchor=ANCHOR + timedelta(days=20), ownership={CODE: "own"}) == 2
    with eng.connect() as conn:
        assert dict(conn.execute(select(annotation_jobs.c.target_id, annotation_jobs.c.priority)).all())[11] == 13


def test_ranges_touching_current_and_bench_windows():
    assert ranges_touching(ANCHOR, date(2026, 8, 25), date(2026, 8, 25)) == ["d1", "d2", "d7", "d14", "d30", "mtd"]
    assert ranges_touching(ANCHOR, date(2026, 8, 24), date(2026, 8, 24)) == ["d1", "d2", "d7", "d14", "d30", "mtd"]  # d1 基准窗
    assert ranges_touching(ANCHOR, date(2026, 8, 3), date(2026, 8, 3)) == ["d14", "d30", "mtd"]
    assert ranges_touching(ANCHOR, date(2026, 6, 1), date(2026, 6, 2)) == []


# ── 近重复 ─────────────────────────────────────────────────────────────


def test_normalize_and_simhash_distance():
    a = "跌下来正好继续加这只！！[笑哭] $03033.HK$"
    b = "跌下來正好繼續加這隻"  # 繁体不同字符，距离会大；用相同字面比
    c = "跌下来正好继续加这只。。"
    assert neardup.normalize(a) == neardup.normalize(c) == "跌下来正好继续加这只"
    assert neardup.hamming(neardup.simhash64(neardup.normalize(a)), neardup.simhash64(neardup.normalize(c))) == 0
    d = "跌下来正好继续加这只，别慌"
    dist = neardup.hamming(neardup.simhash64(neardup.normalize(a)), neardup.simhash64(neardup.normalize(d)))
    assert 0 < dist <= 12
    far = neardup.hamming(neardup.simhash64(neardup.normalize(a)), neardup.simhash64(neardup.normalize("恒指今天要崩了")))
    assert far > neardup.HAMMING_MAX
    assert neardup.normalize(b) != neardup.normalize(a)


def test_fold_clusters_within_key_only():
    rows = [
        {"id": 1, "key": ("3033", date(2026, 8, 20)), "text": "跌下来正好继续加这只"},
        {"id": 2, "key": ("3033", date(2026, 8, 20)), "text": "跌下来正好继续加这只！"},
        {"id": 3, "key": ("3033", date(2026, 8, 20)), "text": "跌下来 正好继续加这只[笑]"},
        {"id": 4, "key": ("3033", date(2026, 8, 21)), "text": "跌下来正好继续加这只"},   # 另一天：不折
        {"id": 5, "key": ("7226", date(2026, 8, 20)), "text": "跌下来正好继续加这只"},   # 另一产品：不折
        {"id": 6, "key": ("3033", date(2026, 8, 20)), "text": "恒指今天要崩了"},
        {"id": 7, "key": ("3033", date(2026, 8, 20)), "text": "有"},                    # 太短：不折
        {"id": 8, "key": ("3033", date(2026, 8, 20)), "text": "有"},
    ]
    reps, members = neardup.fold(rows, key_of=lambda r: r["key"], id_of=lambda r: r["id"], text_of=lambda r: r["text"])
    assert sorted(r["id"] for r in reps) == [1, 4, 5, 6, 7, 8]
    assert [(m["id"], rep) for m, rep, _d in members] == [(2, 1), (3, 1)]


def test_cluster_rows_and_propagation(tmp_path, cfg):
    eng = _engine(tmp_path)
    _seed(eng, ["跌下来正好继续加这只", "跌下来正好继续加这只！", "跌下来正好继续加这只~"])
    now = datetime(2026, 8, 26)
    with eng.begin() as conn:
        for rid, prov in (("rule-1", "rule"), ("luna-1", "openai_compatible")):
            conn.execute(insert(annotation_runs).values(
                run_id=rid, task="comment_product", provider=prov, model_id="m", prompt_version="comment-product-v3",
                taxonomy_version="v2", schema_version="v2", started_at=now, status="running",
                input_count=0, success_count=0, error_count=0))
    assert neardup.write_cluster_rows(eng, "rule-1", [(12, CODE, 11, 1), (13, CODE, 11, 2)], now) == 2
    assert neardup.write_cluster_rows(eng, "rule-1", [(12, CODE, 11, 1)], now) == 0  # 幂等
    with eng.connect() as conn:
        kinds = sorted((r.target_id, r.kind) for r in conn.execute(select(annotations)))
        tq = [json.loads(r[0]) for r in conn.execute(select(annotations.c.value_json).where(
            annotations.c.kind == "text_quality", annotations.c.target_id == 13))]
        assert neardup.members_of(conn, 11, CODE) == [12, 13]
    assert kinds == [(12, "duplicate_cluster"), (12, "text_quality"), (13, "duplicate_cluster"), (13, "text_quality")]
    assert tq == [{"rule": "near_duplicate", "near_duplicate_of": 11, "distance": 2}]

    # 代表写下结论 ⇒ 成员各得一份副本，provider=propagated
    with eng.begin() as conn:
        rows = [("relevance", json.dumps("relevant"), 0.93, "pending", 1001),
                ("attitude", json.dumps("positive"), 0.9, "pending", 1002),
                ("compliance", json.dumps({"tags": []}), None, "pending", 1003)]  # 不传播
        assert neardup.propagate(conn, 11, CODE, rows, "luna-1", now) == 4
        assert neardup.propagate(conn, 11, CODE, rows, "luna-1", now) == 0  # 同一版不重写
    with eng.connect() as conn:
        prop = conn.execute(select(annotations).where(annotations.c.target_id == 12,
                                                      annotations.c.kind.in_(("relevance", "attitude")))).mappings().all()
        run = conn.execute(select(annotation_runs).where(annotation_runs.c.run_id == prop[0]["run_id"])).mappings().one()
    assert len(prop) == 2 and run["provider"] == "propagated" and run["run_id"] == "prop-luna-1"
    assert run["model_revision"] == "luna-1"
    assert run["prompt_version"] == "comment-product-v3"
    assert {json.loads(p["value_json"]) for p in prop} == {"relevant", "positive"}
    assert {p["calibrated_confidence"] for p in prop} == {0.93, 0.9}

    # 人工改过成员 13 的 relevance ⇒ 新一版代表结论不 supersede 它
    with eng.begin() as conn:
        conn.execute(update(annotations).where(annotations.c.target_id == 13, annotations.c.kind == "relevance")
                     .values(review_state="corrected"))
        rows2 = [("relevance", json.dumps("irrelevant"), 0.8, "needs_review", 2001)]
        assert neardup.propagate(conn, 11, CODE, rows2, "luna-2", now) == 2
    with eng.connect() as conn:
        r12 = conn.execute(select(annotations).where(annotations.c.target_id == 12, annotations.c.kind == "relevance")
                           .order_by(annotations.c.annotation_id)).mappings().all()
        r13 = conn.execute(select(annotations).where(annotations.c.target_id == 13, annotations.c.kind == "relevance")
                           .order_by(annotations.c.annotation_id)).mappings().all()
    assert r12[1]["supersedes_id"] == r12[0]["annotation_id"] and r12[1]["review_state"] == "needs_review"
    assert r13[1]["supersedes_id"] is None  # 人工裁决的不被顶掉


# ── 训练集 ─────────────────────────────────────────────────────────────


def test_split_has_no_group_leakage_and_is_deterministic():
    units = []
    for code in ("3033", "7226", "2800"):
        for week in range(1, 40):
            for i in range(3):
                units.append({"unit": [week * 10 + i, code], "group": f"{code}|2026-W{week:02d}"})
    train, hold = dataset.split(units, 0.10)
    assert {u["group"] for u in train}.isdisjoint({u["group"] for u in hold})
    assert 0.04 < len(hold) / len(units) < 0.18
    train2, hold2 = dataset.split(units, 0.10)
    assert [u["unit"] for u in hold] == [u["unit"] for u in hold2]
    assert dataset.group_key("3033", datetime(2026, 8, 25)) == "3033|2026-W35"
    assert dataset.group_key("3033", None) == "3033|unknown"


def test_collect_units_takes_only_llm_current_rows_with_luna_payload(tmp_path, cfg):
    eng = _engine(tmp_path)
    _seed(eng, ["这只ETF点差太大", "费率比同行高", "恒指要崩了", "抄来的一条"])
    now = datetime(2026, 8, 26)
    with eng.begin() as conn:
        for rid, prov in (("luna-1", "openai_compatible"), ("rule-1", "rule"), ("stu-1", "local_model"), ("prop-1", "propagated")):
            conn.execute(insert(annotation_runs).values(
                run_id=rid, task="comment_product", provider=prov, model_id="m", prompt_version="comment-product-v3",
                taxonomy_version="v2", schema_version="v2", started_at=now, status="done",
                input_count=0, success_count=0, error_count=0))

        def ann(tid, kind, value, run, sup=None):
            return {"target_type": "comment", "target_id": tid, "subject_code": CODE, "kind": kind,
                    "value_json": json.dumps(value), "run_id": run, "input_hash": f"h{tid}{kind}{run}",
                    "review_state": "pending", "created_at": now, "supersedes_id": sup}
        conn.execute(insert(annotations), [
            ann(11, "relevance", "relevant", "luna-1"), ann(11, "attitude", "negative", "luna-1"),
            ann(11, "aspect", ["spread"], "luna-1"),
            ann(12, "relevance", "relevant", "stu-1"),            # 学生自己的：剔
            ann(13, "relevance", "irrelevant", "rule-1"),         # 规则：剔
            ann(14, "relevance", "relevant", "prop-1"),           # 抄来的：剔
        ])
        # 12 后来被 Luna supersede 了 ⇒ 现行是 Luna 的，要收
        res = conn.execute(select(annotations.c.annotation_id).where(annotations.c.target_id == 12)).scalar_one()
        conn.execute(insert(annotations).values(**ann(12, "relevance", "irrelevant", "luna-1", sup=res)))
        _activate_routes(conn, [11, 12, 13, 14])
    units = dataset.collect_units(eng)
    assert [u["unit"] for u in units] == [[11, CODE], [12, CODE]]
    u11 = units[0]
    assert u11["relevance"] == "relevant" and u11["attitude"] == "negative" and u11["aspects"] == ["spread"]
    assert units[1]["relevance"] == "irrelevant" and units[1]["attitude"] is None
    assert u11["text"].startswith("评论：这只ETF点差太大\n产品：3033")
    assert "标题：恒科今日走势" in u11["text"] and "帖子：今日恒科低开高走" in u11["text"]
    assert u11["group"] == "3033|2026-W34"
    meta = dataset.build(eng, tmp_path / "ds")
    assert meta["n_units"] == 2 and (tmp_path / "ds" / "train.jsonl").exists()
    assert meta["aspect_head"] is False


def test_training_units_exclude_unqualified_and_cross_product_comments(tmp_path, cfg):
    eng = _engine(tmp_path)
    _seed(eng, ["合格评论"], first=11, feed_id=1)
    now = datetime(2026, 8, 26)
    with eng.begin() as conn:
        conn.execute(insert(feeds), [
            {"feed_id": 2, "code": CODE, "source_ticker": "03033.HK", "posted_at": now,
             "feed_type": 1, "title": "无 cashtag", "content": "无 cashtag", "like_count": 0,
             "comment_count": 1, "image_count": 0, "raw_json_broken": False},
            {"feed_id": 3, "code": CODE, "source_ticker": "03033.HK", "posted_at": now,
             "feed_type": 1, "title": "$03033.HK$", "content": "合格父帖", "like_count": 0,
             "comment_count": 1, "image_count": 0, "raw_json_broken": False},
        ])
        conn.execute(insert(feed_mentions).values(
            feed_id=3, raw_ticker="03033.HK", market="HK", occurrences=1,
        ))
        conn.execute(insert(comments), [
            {"comment_id": 12, "feed_id": 2, "content": "不合格父帖下评论", "author_uid": "u2"},
            {"comment_id": 13, "feed_id": 3, "content": "跨产品旧结论", "author_uid": "u3"},
        ])
        conn.execute(insert(annotation_runs).values(
            run_id="scope-luna", task="comment_product", provider="openai_compatible", model_id="m",
            prompt_version="comment-product-v3", taxonomy_version="v2", schema_version="v2", started_at=now,
            status="done", input_count=0, success_count=0, error_count=0,
        ))
        conn.execute(insert(annotations), [
            {"target_type": "comment", "target_id": 11, "subject_code": CODE, "kind": "relevance",
             "value_json": '"relevant"', "run_id": "scope-luna", "input_hash": "valid",
             "review_state": "pending", "created_at": now},
            {"target_type": "comment", "target_id": 12, "subject_code": CODE, "kind": "relevance",
             "value_json": '"relevant"', "run_id": "scope-luna", "input_hash": "unqualified",
             "review_state": "pending", "created_at": now},
            {"target_type": "comment", "target_id": 13, "subject_code": "2800", "kind": "relevance",
             "value_json": '"relevant"', "run_id": "scope-luna", "input_hash": "cross",
             "review_state": "pending", "created_at": now},
        ])
        _activate_routes(conn, [11])

    assert [unit["unit"] for unit in dataset.collect_units(eng)] == [[11, CODE]]


def test_training_units_fail_closed_without_route_readiness(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "not-ready.db").as_posix())
    create_all(eng)
    with pytest.raises(RuntimeError, match="comment AI routes are not ready"):
        dataset.collect_units(eng)


def test_payload_text_puts_comment_first_and_caps_aliases():
    p = {"comment": "有", "product": {"code": "3033", "name": "恒生科技指数ETF", "aliases": list("abcdefg")},
         "parent_comment": "这只怎么看", "post_title": "标题", "post_context": None}
    t = dataset.payload_text(p)
    assert t.splitlines()[0] == "评论：有" and "a／b／c／d" in t and "e" not in t.split("\n")[1]
    assert "父评论：这只怎么看" in t and "帖子：" not in t


def test_registry_thresholds_and_model_id(monkeypatch):
    assert registry.model_id_string("rbt3") == "hfl/rbt3@0aa0527ff4170f29e1dfd3eb6ef60dc67e1bf75c"
    assert registry.model_id_string().startswith("Langboat/mengzi-bert-base-fin@")
    monkeypatch.setenv("STUDENT_ROUTE_THRESHOLD", "0.9")
    monkeypatch.delenv("STUDENT_REVIEW_THRESHOLD", raising=False)
    assert registry.route_threshold() == 0.9 and registry.review_threshold() == 0.9
    monkeypatch.setenv("STUDENT_MODEL_DIR", "/tmp/x/student")
    assert str(registry.model_dir()).endswith("student")
    with pytest.raises(KeyError):
        registry.spec("nope")


# ── 迁移 0008 的数据步 ─────────────────────────────────────────────────


def test_migration_0008_moves_non_comment_tasks_to_llm(tmp_path, monkeypatch):
    pytest.importorskip("alembic")
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    url = "sqlite:///" + (tmp_path / "m.db").as_posix()
    monkeypatch.setenv("RADAR_DB_URL", url)
    cfg = Config(os.path.join(REPO, "radar_db", "alembic.ini"))
    command.upgrade(cfg, "0007")
    eng = make_engine(url)
    with eng.begin() as conn:
        for tid, task in ((1, "comment_product"), (2, "post_annotation"), (3, "kol_comment_opinion")):
            conn.execute(text(
                "INSERT INTO annotation_jobs (task, target_type, target_id, subject_code, input_hash, status, "
                "priority, attempts, created_at, updated_at) VALUES (:task, 'comment', :tid, '3033', :h, 'pending', "
                "0, 0, '2026-08-26 00:00:00', '2026-08-26 00:00:00')"), {"task": task, "tid": tid, "h": f"h{tid}"})
    command.upgrade(cfg, "head")
    with eng.connect() as conn:
        stages = dict(conn.execute(text("SELECT task, stage FROM annotation_jobs")).all())
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
    assert stages == {"comment_product": "student", "post_annotation": "llm", "kol_comment_opinion": "llm"}
    assert "worker_events" in tables
    # 迁移后的库能直接 emit
    assert events.emit(eng, "L0", "迁移后第一条") is not None
