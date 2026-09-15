"""ADR-0021 顺序与并行：逐区间就绪判定、Layer B 按 (code, range) 并行、full_own 的学生线程。"""

import json
import os
import sys
import threading
from datetime import date, datetime

import pytest
from sqlalchemy import insert, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "backend"))

from ai import config  # noqa: E402
from ai.providers.base import Completion, TransientError, Usage  # noqa: E402
from jobs import full_own, pipeline, synthesize  # noqa: E402
from radar_db import create_all, make_engine  # noqa: E402
from radar_db.events import recent  # noqa: E402
from radar_db.schema import (  # noqa: E402
    analysis_scope_jobs, analysis_scopes, annotation_jobs, annotations, comments, feeds, meta_kv, synthesis_outputs,
)

A, B = "3033", "7226"
ANCHOR = date(2026, 8, 25)


@pytest.fixture()
def cfg():
    return config.load(model="m", prompt_version="comment-product-v2", schema_version="v2", taxonomy_version="v2")


def _engine(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "s.db").as_posix())
    create_all(eng)
    with eng.begin() as conn:
        conn.execute(insert(meta_kv).values(k="anchor", v=ANCHOR.isoformat()))
    return eng


def _seed_units(conn, code, feed_id, first_cid, day, n_pos=8, n_neg=4):
    conn.execute(insert(feeds).values(
        feed_id=feed_id, code=code, posted_at=datetime.combine(day, datetime.min.time()).replace(hour=10),
        feed_type=1, title="t", content="c", like_count=0, comment_count=0, image_count=0, raw_json_broken=False))
    rows, anns = [], []
    for i in range(n_pos + n_neg):
        cid = first_cid + i
        pos = i < n_pos
        rows.append({"comment_id": cid, "feed_id": feed_id, "content": f"费率係同類最低第{i}條" if pos else f"點差太大第{i}條",
                     "author_uid": f"u{code}{i}"})
        for kind, value in (("relevance", "relevant"), ("attitude", "positive" if pos else "negative"),
                            ("aspect", ["fee"] if pos else ["spread"]), ("compliance", {"tags": [], "rationale": None})):
            anns.append({"target_type": "comment", "target_id": cid, "subject_code": code, "kind": kind,
                         "value_json": json.dumps(value, ensure_ascii=False), "run_id": "r1",
                         "input_hash": f"h{cid}", "review_state": "pending", "created_at": datetime(2026, 8, 26)})
    conn.execute(insert(comments), rows)
    conn.execute(insert(annotations), anns)


class SynthFake:
    """合规输出；`fail_codes` 里的产品每次都抛 TransientError（模拟网关对某只产品持续失败）。"""

    def __init__(self, fail_codes=()):
        self.fail_codes = set(fail_codes)
        self.calls = []
        self.threads = set()
        self._lock = threading.Lock()

    def complete_json(self, system, user, schema, schema_name):
        kind = schema_name.replace("synth_", "")
        payload = json.loads(user.split("\n\n", 1)[1])
        with self._lock:
            self.calls.append((payload["product"]["code"], payload["range"]["key"], kind))
            self.threads.add(threading.current_thread().name)
        if payload["product"]["code"] in self.fail_codes:
            raise TransientError("HTTP 503")
        ids = [e["id"] for e in payload["evidence"]]
        f = payload["facts"]
        if kind == "hot_summary":
            data = {"text": "費率獲認可", "evidence_ids": ids[:1], "needs_review": False}
        elif kind == "summary":
            data = {"points": [{"text": "多條評論認可費率", "evidence_ids": ids[:1]}], "needs_review": False}
        elif kind in ("theme_label", "neg_category"):
            data = {"results": [{"key": b["key"], "title": "費率同類最低", "summary": "如此。",
                                 "evidence_ids": b["evidence_ids"][:1]} for b in f["buckets"]]}
        elif kind == "stage_unit":
            data = {"results": [{"key": u["key"], "category": "add_opportunity", "digest": "加倉為主",
                                 "evidence_ids": u["evidence_ids"][:1], "needs_review": False} for u in f["units"]]}
        elif kind == "stage_summary":
            data = {"results": [{"key": s["key"], "summary": "連續看多", "evidence_ids": s["evidence_ids"][:1]}
                                for s in f["stages"]]}
        elif kind == "topic_label":
            data = {"title": "大市方向", "summary": "看空為主", "evidence_ids": ids[:1]}
        else:
            data = {"results": [{"code": c["code"], "like_reasons": [], "dislike_reasons": [], "evidence_ids": [],
                                 "needs_review": True} for c in f["competitors"]]}
        return Completion(data=data, model="m", usage=Usage(10, 5, 0, 0), raw_text=json.dumps(data), response_id="x")


# ── 逐区间就绪 ─────────────────────────────────────────────────────────


def _scope_with_pending(conn, scope_id, code, cid, feed_id, day, status="pending"):
    """`day=None` ⇒ 评论所属的帖子行不存在（ETL 半途的库会有），日期取不到。"""
    conn.execute(insert(analysis_scopes).values(
        scope_id=scope_id, task="both", codes_json=json.dumps([code]), date_from=datetime(2026, 6, 27),
        date_to=datetime(2026, 8, 25), time_basis="feed_posted_at", with_baseline=False, prompt_version="p",
        taxonomy_version="v2", schema_version="v2", created_at=datetime(2026, 8, 26)))
    if day is not None:
        conn.execute(insert(feeds).values(
            feed_id=feed_id, code=code, posted_at=datetime.combine(day, datetime.min.time()),
            feed_type=1, title="t", content="c", like_count=0, comment_count=0, image_count=0, raw_json_broken=False))
    conn.execute(insert(comments).values(comment_id=cid, feed_id=feed_id, content="x", author_uid="u"))
    conn.execute(insert(annotation_jobs).values(
        task="comment_product", target_type="comment", target_id=cid, subject_code=code, input_hash="h",
        status=status, stage="student", priority=0, attempts=0, created_at=datetime(2026, 8, 26),
        updated_at=datetime(2026, 8, 26), scope_id=scope_id))
    job_id = conn.execute(select(annotation_jobs.c.job_id).where(annotation_jobs.c.target_id == cid)).scalar_one()
    conn.execute(insert(analysis_scope_jobs).values(scope_id=scope_id, job_id=job_id))


def test_ready_pairs_blocks_only_ranges_covering_pending_days(tmp_path):
    eng = _engine(tmp_path)
    with eng.begin() as conn:
        # 8 月 3 日还有一条待判（学生段）：只挡住窗口覆盖 8/3 的区间。
        _scope_with_pending(conn, "s1", A, 900, 90, date(2026, 8, 3))
    ranges = list(synthesize.DEFAULT_RANGES)
    ready = {rk for _, rk in pipeline.ready_pairs(eng, "s1", [A], ranges, ANCHOR)}
    # d1 当前窗 8/25、基准窗 8/24；d2 8/24–25 与 8/22–23；d7 8/19–25 与 8/12–18；d14 8/12–25 与 7/29–8/11
    assert ready == {"d1", "d2", "d7"}

    # 判完了 ⇒ 全部就绪
    with eng.begin() as conn:
        conn.execute(annotation_jobs.update().values(status="done"))
    assert len(pipeline.ready_pairs(eng, "s1", [A], ranges, ANCHOR)) == 6

    # claimed 也算没判完
    with eng.begin() as conn:
        conn.execute(annotation_jobs.update().values(status="claimed"))
    assert {rk for _, rk in pipeline.ready_pairs(eng, "s1", [A], ranges, ANCHOR)} == {"d1", "d2", "d7"}


def test_ready_pairs_null_date_blocks_everything(tmp_path):
    eng = _engine(tmp_path)
    with eng.begin() as conn:
        _scope_with_pending(conn, "s1", A, 900, 90, None)
    assert pipeline.ready_pairs(eng, "s1", [A], list(synthesize.DEFAULT_RANGES), ANCHOR) == []
    # 别的产品不受影响
    assert len(pipeline.ready_pairs(eng, "s1", [B], list(synthesize.DEFAULT_RANGES), ANCHOR)) == 6


# ── Layer B 并行 ───────────────────────────────────────────────────────


def test_parallel_synthesize_matches_sequential_and_emits_per_pair(tmp_path, cfg):
    eng = _engine(tmp_path)
    with eng.begin() as conn:
        _seed_units(conn, A, 1, 100, date(2026, 8, 22))
        _seed_units(conn, B, 2, 200, date(2026, 8, 22))
    seq = SynthFake()
    st1 = synthesize.run(eng, cfg, codes=[A, B], ranges=["d7", "d14"], provider=seq, workers=1)
    with eng.connect() as conn:
        rows_seq = sorted((r.code, r.range_key, r.kind, r.subkey, r.value_json)
                          for r in conn.execute(select(synthesis_outputs)))

    (tmp_path / "p").mkdir()
    eng2 = _engine(tmp_path / "p")
    with eng2.begin() as conn:
        _seed_units(conn, A, 1, 100, date(2026, 8, 22))
        _seed_units(conn, B, 2, 200, date(2026, 8, 22))
    par = SynthFake()
    st2 = synthesize.run(eng2, cfg, codes=[A, B], ranges=["d7", "d14"], provider=par, workers=4)
    with eng2.connect() as conn:
        rows_par = sorted((r.code, r.range_key, r.kind, r.subkey, r.value_json)
                          for r in conn.execute(select(synthesis_outputs)))
    assert rows_par == rows_seq
    assert (st2["calls"], st2["written"], st2["errors"]) == (st1["calls"], st1["written"], 0)
    assert st2["pairs"] == 4 and st2["pairs_clean"] == 4
    assert len(par.threads) > 1  # 确实并行了
    # 每对内部顺序不变：同一 (code, range) 的调用序列与顺序版一致
    def per_pair(calls):
        out = {}
        for code, rk, kind in calls:
            out.setdefault((code, rk), []).append(kind)
        return out
    assert per_pair(par.calls) == per_pair(seq.calls)

    evs = [e for e in recent(eng2) if e["stage"] == "L3"]
    assert sorted((e["code"], e["data"]["range"]) for e in evs) == [(A, "d14"), (A, "d7"), (B, "d14"), (B, "d7")]
    assert all("汇总写入" in e["message"] and e["data"]["written"] > 0 for e in evs)


def test_failed_pair_keeps_dirty_flag_and_clean_pair_clears_it(tmp_path, cfg):
    eng = _engine(tmp_path)
    with eng.begin() as conn:
        _seed_units(conn, A, 1, 100, date(2026, 8, 22))
        _seed_units(conn, B, 2, 200, date(2026, 8, 22))
        from radar_db.revisions import mark_synthesis
        mark_synthesis(conn, [A, B], True, ["d7"])
    st = synthesize.run(eng, cfg, pairs=[(A, "d7"), (B, "d7")], provider=SynthFake(fail_codes={B}), workers=2)
    assert st["errors"] > 0 and st["pairs_clean"] == 1
    with eng.connect() as conn:
        flags = dict(conn.execute(select(meta_kv.c.k, meta_kv.c.v).where(meta_kv.c.k.like("synth_dirty_%"))).all())
    assert flags[f"synth_dirty_{A}_d7"] == "0"
    assert flags[f"synth_dirty_{B}_d7"] == "1"
    evs = {e["code"]: e for e in recent(eng) if e["stage"] == "L3"}
    assert evs[B]["level"] == "warn" and evs[A]["level"] == "info"


# ── full_own 学生线程 ──────────────────────────────────────────────────


def test_student_channel_rotates_scopes_and_stops(monkeypatch, tmp_path, cfg):
    eng = _engine(tmp_path)
    seen = []
    budget = {"n": 2}

    def fake_classify(engine, c, *, scope_id, model_dir=None, **kw):
        seen.append(scope_id)
        n = budget["n"]
        budget["n"] = max(0, n - 1)
        return {"input": n, "routed": 0}

    monkeypatch.setattr(full_own.classify, "run", fake_classify)
    state = {"scopes": {A: "sA", B: "sB"}}
    ch = full_own.StudentChannel(eng, cfg, state, idle_seconds=0.05)
    ch.start()
    import time
    deadline = time.time() + 5
    while ch.rounds < 3 and time.time() < deadline:
        time.sleep(0.02)
    state["scopes"] = {A: "sA2"}  # tick 重排了 scope，线程下一轮跟上
    while "sA2" not in seen and time.time() < deadline:
        time.sleep(0.02)
    ch.stop()
    ch.join(timeout=5)
    assert not ch.is_alive()
    assert seen[:2] == ["sA", "sB"] and "sA2" in seen
