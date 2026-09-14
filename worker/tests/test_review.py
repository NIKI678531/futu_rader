"""`jobs/review.py` —— 人工复核。

这支 CLI 曾经是标注结果通向界面的唯一一道门。ADR-0019 取消了那道门槛（模型写下即
发布），它于是降级成一件可选工具 —— 但下面这些断言一条都没作废，因为它们盯的不是
「门放不放东西过去」，而是**这支 CLI 写进库里的东西对不对**：

- 批准了的结论，自动重跑不许再覆盖 —— 这条保证 `annotate.py` 早就断言了，
  但在 review.py 之前没有任何东西能写出 `approved`，那条断言一直悬空。
- 改正不许原地改旧值：模型当初判的是什么，是 Gate 3 训练金标时的对照。
- 已被新一轮取代的旧行不许被批准 —— 那等于给一个过期结论盖人工章。
- 否决必须带受控词表里的理由，裁决必须追得到人。
- 参数不合法时**一列都不许写**，不能写一半。
"""

import json
import os
import sys
from datetime import datetime

import pytest
from sqlalchemy import insert, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from jobs import review  # noqa: E402
from radar_db import create_all, make_engine  # noqa: E402
from radar_db.schema import (  # noqa: E402
    NO_SUBJECT,
    annotation_evidence,
    annotations,
    review_decisions,
)

CODE = "3033"


def _ann(engine, **over):
    """插一条标注，返回 annotation_id。默认是「模型判了 negative，等人看」。"""
    vals = {
        "target_type": "comment",
        "target_id": 11,
        "subject_code": CODE,
        "kind": "attitude",
        "value_json": json.dumps("negative"),
        "calibrated_confidence": None,
        "run_id": "run-a",
        "input_hash": "h1",
        "review_state": "pending",
        "created_at": datetime(2026, 8, 20, 9, 0),
        "supersedes_id": None,
    }
    vals.update(over)
    with engine.begin() as conn:
        return conn.execute(insert(annotations).values(**vals)).inserted_primary_key[0]


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "t.db").as_posix())
    create_all(eng)
    return eng


def _rows(engine, table):
    with engine.connect() as conn:
        return conn.execute(select(table)).mappings().all()


# ── 队列 ───────────────────────────────────────────────────────────────


def test_queue_puts_needs_review_before_pending(engine):
    """管线自己举手的那些先给人看 —— 它们已经带着一条具体的怀疑理由了。"""
    # 故意让 pending 的 id 更小：如果只按 id 排，它会排在前面。
    p = _ann(engine, target_id=11, review_state="pending")
    n = _ann(engine, target_id=12, review_state="needs_review")
    assert [r["annotation_id"] for r in review.queue(engine)] == [n, p]


def test_settled_annotations_leave_the_queue(engine):
    _ann(engine, target_id=11, review_state="approved")
    _ann(engine, target_id=12, review_state="rejected")
    open_one = _ann(engine, target_id=13, review_state="pending")
    assert [r["annotation_id"] for r in review.queue(engine)] == [open_one]
    assert review.open_count(engine) == {"pending": 1}


def test_superseded_rows_are_not_offered_for_review(engine):
    """旧行已被新一轮取代，再让人裁决它就是给过期结论盖章。"""
    old = _ann(engine, review_state="pending")
    new = _ann(engine, run_id="run-b", review_state="pending", supersedes_id=old)
    assert [r["annotation_id"] for r in review.queue(engine)] == [new]
    # 计数必须和队列用同一套过滤：对不上就永远走不到零。
    assert review.open_count(engine) == {"pending": 1}


def test_queue_can_be_narrowed_to_one_kind(engine):
    a = _ann(engine, kind="attitude")
    _ann(engine, kind="summary", target_id=12)
    assert [r["annotation_id"] for r in review.queue(engine, kind="attitude")] == [a]
    assert review.open_count(engine, kind="attitude") == {"pending": 1}


def test_detail_carries_the_located_evidence_not_the_models_quote(engine):
    """人要判的是原文切片。`annotation_evidence` 里存的就是程序定位出来的那一段。"""
    aid = _ann(engine)
    with engine.begin() as conn:
        conn.execute(
            insert(annotation_evidence).values(
                annotation_id=aid, source_target_type="comment", source_target_id=11,
                start_offset=3, end_offset=8, quote_text="点差太大", quote_hash="q1",
            )
        )
    row, ev = review.detail(engine, aid)
    assert row["value_json"] == json.dumps("negative")
    assert [e["quote_text"] for e in ev] == ["点差太大"]


# ── 批准 ───────────────────────────────────────────────────────────────


def test_approve_flips_state_and_leaves_a_named_trail(engine):
    aid = _ann(engine, review_state="needs_review")
    assert review.decide(engine, aid, "approve", "alice") is None

    rows = _rows(engine, annotations)
    assert len(rows) == 1, "批准不该新增行，它只是给现有这条盖个章"
    assert rows[0]["review_state"] == "approved"

    d = _rows(engine, review_decisions)
    assert len(d) == 1
    assert (d[0]["annotation_id"], d[0]["decision"], d[0]["reviewer"]) == (
        aid, "approve", "alice",
    )
    assert d[0]["corrected_value_json"] is None
    assert d[0]["reviewed_at"] is not None


def test_approved_state_is_what_annotate_refuses_to_overwrite(engine):
    """接缝检查：review.py 写出来的状态，正是 annotate.py 拿来挡重跑的那两个。

    `annotate._HUMAN_SETTLED` 早就断言「人工确认过的不被覆盖」，但在这支 CLI
    之前没有任何代码路径能产生 `approved` —— 那条保证一直是悬空的。
    """
    from jobs import annotate

    aid = _ann(engine)
    review.decide(engine, aid, "approve", "alice")
    with engine.connect() as conn:
        state = conn.execute(
            select(annotations.c.review_state).where(annotations.c.annotation_id == aid)
        ).scalar()
    assert state in annotate._HUMAN_SETTLED


def test_reviewer_is_mandatory(engine):
    aid = _ann(engine)
    with pytest.raises(review.ReviewError, match="追到人"):
        review.decide(engine, aid, "approve", "")
    assert _rows(engine, review_decisions) == []


def test_deciding_a_superseded_row_is_refused(engine):
    """`--id` 绕过了队列，所以这条守卫得在 decide 里，不能只在 queue 里。"""
    old = _ann(engine)
    new = _ann(engine, run_id="run-b", supersedes_id=old)
    with pytest.raises(review.ReviewError, match="已被"):
        review.decide(engine, old, "approve", "alice")
    assert _rows(engine, review_decisions) == []
    # 新的那条照样能裁决。
    review.decide(engine, new, "approve", "alice")


def test_unknown_annotation_writes_nothing(engine):
    with pytest.raises(review.ReviewError, match="不存在"):
        review.decide(engine, 999, "approve", "alice")
    assert _rows(engine, review_decisions) == []


# ── 否决 ───────────────────────────────────────────────────────────────


def test_reject_requires_a_reason_from_the_vocabulary(engine):
    aid = _ann(engine)
    with pytest.raises(review.ReviewError, match="--reason"):
        review.decide(engine, aid, "reject", "alice")
    with pytest.raises(review.ReviewError, match="词表"):
        review.decide(engine, aid, "reject", "alice", reason="感觉不对")
    assert _rows(engine, review_decisions) == [], "校验失败时一列都不许写"
    with engine.connect() as conn:
        assert conn.execute(
            select(annotations.c.review_state).where(annotations.c.annotation_id == aid)
        ).scalar() == "pending"

    review.decide(engine, aid, "reject", "alice", reason="hallucinated_evidence")
    assert _rows(engine, review_decisions)[0]["reason_code"] == "hallucinated_evidence"


def test_rejected_annotation_stays_in_the_table(engine):
    """否决不是删除。否决过什么，是 Gate 3 训练时最有用的那一半样本。"""
    aid = _ann(engine)
    review.decide(engine, aid, "reject", "alice", reason="wrong_attitude")
    rows = _rows(engine, annotations)
    assert len(rows) == 1
    assert rows[0]["review_state"] == "rejected"
    assert rows[0]["value_json"] == json.dumps("negative"), "值原样留着"


# ── 改正 ───────────────────────────────────────────────────────────────


def test_correction_adds_a_row_and_never_edits_the_models_value(engine):
    old = _ann(engine)
    new = review.decide(engine, old, "correct", "alice", value="positive")
    assert new is not None and new != old

    rows = {r["annotation_id"]: r for r in _rows(engine, annotations)}
    assert len(rows) == 2
    # 模型当初判的是什么，一个字没动 —— 那是训练金标时的对照。
    assert rows[old]["value_json"] == json.dumps("negative")
    assert rows[old]["review_state"] == "corrected"
    # 新行是人给的值，且链回旧行。
    assert rows[new]["value_json"] == json.dumps("positive")
    assert rows[new]["supersedes_id"] == old
    assert rows[new]["review_state"] == "approved"


def test_correction_does_not_collide_with_the_unique_key(engine):
    """`uq_annotations_unit` 含 run_id。沿用旧行的 run_id 会和旧行逐列相同。

    这条是对着那个真实的坑写的：改正行的 target/subject/kind/input_hash 必须和
    旧行一致（它就是同一个判定单元），唯一能拉开的只有 run_id。
    """
    old = _ann(engine)
    new = review.decide(engine, old, "correct", "alice", value="positive")
    rows = {r["annotation_id"]: r for r in _rows(engine, annotations)}
    for col in ("target_type", "target_id", "subject_code", "kind", "input_hash"):
        assert rows[new][col] == rows[old][col], f"{col} 必须沿用：还是同一个判定单元"
    assert rows[new]["run_id"] != rows[old]["run_id"]
    # run_id 指回产生它的那次裁决，追得回去。
    did = _rows(engine, review_decisions)[0]["decision_id"]
    assert rows[new]["run_id"] == f"review:{did}"


def test_correcting_the_same_unit_twice_still_works(engine):
    """第二次改正落在第一次的产物上。run_id 若按人名生成，这里就会撞键。"""
    old = _ann(engine)
    mid = review.decide(engine, old, "correct", "alice", value="positive")
    new = review.decide(engine, mid, "correct", "alice", value="neutral")
    rows = {r["annotation_id"]: r for r in _rows(engine, annotations)}
    assert len(rows) == 3
    assert rows[new]["supersedes_id"] == mid
    assert rows[new]["value_json"] == json.dumps("neutral")
    assert len({r["run_id"] for r in rows.values()}) == 3


def test_correction_never_fabricates_a_confidence(engine):
    """人改的值没有概率可言。写 1.0 会让它冒充一个校准过的高置信结论（铁律 2）。"""
    old = _ann(engine, calibrated_confidence=None)
    new = review.decide(engine, old, "correct", "alice", value="positive")
    rows = {r["annotation_id"]: r for r in _rows(engine, annotations)}
    assert rows[new]["calibrated_confidence"] is None


def test_correction_records_the_new_value_in_the_decision_row(engine):
    old = _ann(engine)
    review.decide(engine, old, "correct", "alice", value=["fee", "spread"])
    d = _rows(engine, review_decisions)[0]
    assert d["decision"] == "correct"
    assert json.loads(d["corrected_value_json"]) == ["fee", "spread"]


def test_correct_without_a_value_is_refused(engine):
    aid = _ann(engine)
    with pytest.raises(review.ReviewError, match="改正后的值"):
        review.decide(engine, aid, "correct", "alice")
    assert len(_rows(engine, annotations)) == 1
    assert _rows(engine, review_decisions) == []


def test_value_without_correct_is_refused(engine):
    """`--approve --correct x` 这种矛盾组合宁可报错，不猜用户想干什么。"""
    aid = _ann(engine)
    with pytest.raises(review.ReviewError, match="只有 --correct"):
        review.decide(engine, aid, "approve", "alice", value="positive")


def test_unknown_decision_word_is_refused(engine):
    aid = _ann(engine)
    with pytest.raises(review.ReviewError, match="approve/reject/correct"):
        review.decide(engine, aid, "looks_fine", "alice")


# ── 审计 ───────────────────────────────────────────────────────────────


def test_decisions_accumulate_instead_of_overwriting(engine):
    """先批准后否决，和一直是否决，在库里必须长得不一样。"""
    aid = _ann(engine)
    review.decide(engine, aid, "approve", "alice")
    review.decide(engine, aid, "reject", "bob", reason="caliber")

    d = _rows(engine, review_decisions)
    assert [(x["decision"], x["reviewer"]) for x in d] == [
        ("approve", "alice"), ("reject", "bob"),
    ]
    with engine.connect() as conn:
        assert conn.execute(
            select(annotations.c.review_state).where(annotations.c.annotation_id == aid)
        ).scalar() == "rejected", "annotations 上只留最新裁决，过程在 review_decisions 里"


def test_post_level_annotations_are_reviewable_too(engine):
    """帖子级标注的 subject_code 是 NO_SUBJECT，不能被当成「没填」挡在门外。"""
    aid = _ann(engine, target_type="feed", target_id=7, subject_code=NO_SUBJECT,
               kind="post_type", value_json=json.dumps("research"))
    assert [r["annotation_id"] for r in review.queue(engine)] == [aid]
    new = review.decide(engine, aid, "correct", "alice", value="marketing")
    rows = {r["annotation_id"]: r for r in _rows(engine, annotations)}
    assert rows[new]["subject_code"] == NO_SUBJECT
    assert rows[new]["target_type"] == "feed"
