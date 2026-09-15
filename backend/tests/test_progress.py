"""GET /api/v1/progress 与 /progress/events 的契约测试（ADR-0021）。

形状是另一位工程师的侧栏要读的：键名、六个 status、两段 stage、`null` 不写 0、事件按 id 升序、
`after` 增量。demo provider 下 status=unavailable。
"""

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import insert

import providers
from conftest import _client
from radar_db.events import emit
from radar_db.schema import annotation_jobs, meta_kv, synthesis_outputs
from sql_fixture import make_sql_provider

JOB_STATUSES = {"pending", "claimed", "done", "failed", "dead", "superseded"}


def _job(target_id, status, stage, task="comment_product", updated_at=None):
    now = datetime(2026, 9, 1, 10, 0)  # 明确在 5 分钟窗之外
    return {
        "task": task, "target_type": "comment", "target_id": target_id, "subject_code": "3033",
        "input_hash": f"h{target_id}", "status": status, "stage": stage, "priority": 0, "attempts": 0,
        "created_at": now, "updated_at": updated_at or now,
    }


@pytest.fixture
def progress_provider():
    p = make_sql_provider()
    return p


@pytest.fixture
def progress_client(progress_provider):
    providers.reset_provider()
    with _client(progress_provider) as c:
        yield c
    providers.reset_provider()


def test_demo_provider_is_unavailable(client):
    r = client.get("/api/v1/progress")
    assert r.status_code == 200
    assert r.get_json() == {"status": "unavailable", "data": None}
    r2 = client.get("/api/v1/progress/events?after=3")
    assert r2.get_json() == {"status": "unavailable", "data": None}


def test_empty_sql_db_has_full_shape_with_nulls_not_zeros(progress_client):
    r = progress_client.get("/api/v1/progress")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok"
    d = body["data"]
    assert set(d) == {"summary", "queue", "tasks", "synthesis", "throughput", "events", "latestEventId"}
    assert set(d["queue"]) == {"student", "llm"}
    for stage in ("student", "llm"):
        assert set(d["queue"][stage]) == JOB_STATUSES
        assert all(v == 0 for v in d["queue"][stage].values())  # 数出来的零
    assert set(d["tasks"]) == {"post_annotation", "kol_comment_opinion"}
    assert d["synthesis"] == {"dirtyProducts": 0, "productsWithOutputs": 0}
    # 近 5 分钟没有完成的任务 ⇒ 速率未知 ⇒ null，不是 0
    assert d["throughput"] == {"itemsPerSec5m": None, "etaSeconds": None}
    assert d["events"] == [] and d["latestEventId"] is None
    assert d["summary"] is None  # 没有 own_analysis_progress


def test_counts_events_and_throughput(progress_provider, progress_client):
    eng = progress_provider._engine
    recent_done = datetime.now() - timedelta(seconds=60)
    with eng.begin() as conn:
        conn.execute(insert(annotation_jobs), [
            _job(1, "pending", "student"), _job(2, "pending", "student"), _job(3, "done", "student"),
            _job(4, "pending", "llm"), _job(5, "claimed", "llm"),
            _job(6, "done", "llm", updated_at=recent_done), _job(7, "done", "llm", updated_at=recent_done),
            _job(8, "dead", "llm"),
            _job(9, "pending", "llm", task="post_annotation"), _job(10, "done", "llm", task="kol_comment_opinion"),
        ])
        conn.execute(insert(meta_kv), [
            {"k": "synth_dirty_3033_d7", "v": "1"}, {"k": "synth_dirty_3033_d30", "v": "1"},
            {"k": "synth_dirty_7226_d7", "v": "0"}, {"k": "synth_dirty_2800_mtd", "v": "1"},
        ])
        conn.execute(insert(synthesis_outputs), [
            {"code": "3033", "range_key": "d7", "anchor": "2026-08-25", "kind": "summary", "subkey": "-",
             "input_fingerprint": "f1", "value_json": "{}", "evidence_ids_json": "[]", "run_id": "s1",
             "review_state": "pending", "created_at": datetime(2026, 9, 15)},
            {"code": "3033", "range_key": "d30", "anchor": "2026-08-25", "kind": "summary", "subkey": "-",
             "input_fingerprint": "f2", "value_json": "{}", "evidence_ids_json": "[]", "run_id": "s1",
             "review_state": "pending", "created_at": datetime(2026, 9, 15)},
        ])
    ids = [
        emit(eng, "L1", "3033 批 2,000 → 相关 812 / 无关 1,050 / 需上下文 138 → 路由 Luna 421", code="3033",
             scope_id="sc1", run_id="stu-1", data={"n": 2000, "routed": 421}),
        emit(eng, "L2", "3033 Luna 评论批 30 → 写入 30", code="3033", run_id="ann-1"),
        emit(eng, "L3", "3033 d7 汇总写入 5（调用 6）", code="3033", data={"range": "d7", "written": 5}),
        emit(eng, "orchestrator", "全部完成", level="warn"),
    ]
    assert all(ids)

    d = progress_client.get("/api/v1/progress").get_json()["data"]
    assert d["queue"]["student"] == {"pending": 2, "claimed": 0, "done": 1, "failed": 0, "dead": 0, "superseded": 0}
    assert d["queue"]["llm"] == {"pending": 1, "claimed": 1, "done": 2, "failed": 0, "dead": 1, "superseded": 0}
    assert d["tasks"]["post_annotation"]["pending"] == 1 and d["tasks"]["kol_comment_opinion"]["done"] == 1
    assert d["synthesis"] == {"dirtyProducts": 2, "productsWithOutputs": 1}
    # 近 5 分钟 done 2 条 ⇒ 2/300；待办 pending+claimed 两段合计 4 ⇒ eta = 4 / (2/300) = 600
    assert d["throughput"]["itemsPerSec5m"] == pytest.approx(2 / 300, abs=1e-4)
    assert d["throughput"]["etaSeconds"] == 600

    evs = d["events"]
    assert [e["id"] for e in evs] == ids  # 按 id 升序
    assert d["latestEventId"] == ids[-1]
    first = evs[0]
    assert set(first) == {"id", "ts", "level", "stage", "code", "scopeId", "runId", "message", "data"}
    assert first["stage"] == "L1" and first["code"] == "3033" and first["scopeId"] == "sc1" and first["runId"] == "stu-1"
    assert first["data"] == {"n": 2000, "routed": 421}
    assert first["message"].startswith("3033 批 2,000")
    datetime.strptime(first["ts"], "%Y-%m-%d %H:%M:%S")
    assert evs[3]["level"] == "warn" and evs[3]["code"] is None and evs[3]["data"] is None

    # 增量：after=第二条 ⇒ 只回后两条
    inc = progress_client.get(f"/api/v1/progress/events?after={ids[1]}").get_json()
    assert inc["status"] == "ok"
    assert [e["id"] for e in inc["data"]["events"]] == ids[2:]
    assert inc["data"]["latestEventId"] == ids[-1]
    # after=最新 ⇒ 空列表，但 latestEventId 仍在（客户端据此知道没落后）
    tail = progress_client.get(f"/api/v1/progress/events?after={ids[-1]}").get_json()["data"]
    assert tail == {"events": [], "latestEventId": ids[-1]}
    # limit 上限 500，非法值回默认
    lim = progress_client.get("/api/v1/progress/events?limit=1").get_json()["data"]
    assert len(lim["events"]) == 1 and lim["events"][0]["id"] == ids[-1]
    assert len(progress_client.get("/api/v1/progress/events?limit=abc").get_json()["data"]["events"]) == 4


def test_summary_mirrors_meta_analysis_progress(progress_provider, progress_client):
    with progress_provider._engine.begin() as conn:
        conn.execute(insert(meta_kv).values(k="own_analysis_progress", v=json.dumps({
            "anchor": "2026-08-25", "status": "running",
            "products": {"3033": {"complete": True}, "7226": {"complete": False}},
        })))
    d = progress_client.get("/api/v1/progress").get_json()["data"]
    meta = progress_client.get("/api/v1/version").get_json()["data"]["analysisProgress"]
    assert d["summary"] == meta
    assert d["summary"]["completed"] == 1 and d["summary"]["total"] == 2


def test_db_without_migration_0008_is_unavailable(progress_client, progress_provider):
    from sqlalchemy import text
    with progress_provider._engine.begin() as conn:
        conn.execute(text("DROP TABLE worker_events"))
        conn.execute(text("DROP INDEX ix_jobs_task_stage_status"))
        conn.execute(text("ALTER TABLE annotation_jobs DROP COLUMN stage"))
    assert progress_client.get("/api/v1/progress").get_json() == {"status": "unavailable", "data": None}
