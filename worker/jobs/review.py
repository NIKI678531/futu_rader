"""人工复核 —— 把 `annotations` 里的结论过一遍人眼，结果写进 `review_decisions`。

**这一步是可选的**（[ADR-0019](../../docs/adr/0019-ai-auto-publish-no-human-gate.md)）。
它曾经不是：ADR-0017 §4 把 `review_state ∈ {approved, corrected}` 定为发布条件，于是
这支 CLI 是标注结果到页面之间唯一的那道门。ADR-0019 取消了那道门槛 —— 模型写下即
发布，没有人看过也照样上界面，页面另有一句「未经人工验证」如实说明这件事。

所以现在它是一件工具，不是一道闸口，但仍然有两个用处，都不可替代：

1. **`--reject` 是把一条错结论从页面上拿下来的唯一通道。** 发布规则的第二条就是
   「不是 `rejected`」（`backend/providers/sql.py` 的 `_current_annotations`）。除此
   之外没有第二种下线方式 —— 除了改库。
2. **`--correct` 留下的是 Gate 3 训练金标时唯一的人工对照。** 模型判了什么、人改成
   了什么，两行都在库里。

`--approve` 在本 ADR 下不改变任何显示：批过的那一条和没批过的长得一模一样，页面
**不**因此多一枚「已核验」（PRD §3.9 没有这枚徽章）。它只在库里留痕。

它同时是 ADR-0017 §4 的落点。那一条废掉了「置信度低于 0.7 标待确认」：模型自报的
「我有 0.85 的把握」不是概率，拿它画线等于给随机数画线。「待确认」于是改由
`review_state` 驱动 —— 但驱动的是**徽章文案**而不是可见性（ADR-0019 §2）：页面上那句
「AI 生成 · 待确认」的唯一触发是 `needs_review`，也就是**模型自己举的手**，不是
「还没有人看过」。

    python -m jobs.review --queue                       # 看待复核队列
    python -m jobs.review --next                        # 看队首那条的详情与证据
    python -m jobs.review --id 42 --approve  --reviewer alice
    python -m jobs.review --id 42 --reject   --reviewer alice --reason hallucinated_evidence
    python -m jobs.review --id 42 --correct '"negative"' --reviewer alice

三条刻意的设计：

**裁决只追加，不改写。** `review_decisions` 每次都插一行新的，`annotations` 那边只
更新 `review_state`。谁在什么时候改了什么，必须能一路查回去 —— 覆盖式更新会把
「先批准后否决」和「一直是否决」变成同一个样子。

**`--correct` 不改旧行的 `value_json`，而是插一条新的 annotation 指向它。**
和模型重跑用的是同一套 `supersedes_id` 链路。人改过的值与模型给的值在库里都在，
只是前者在链条末端。原地改会让「模型当初判的是什么」永久消失，而那正是
Gate 3 训练金标时最需要的对照。

**`--reason` 只在 `--reject` 时有意义，且是受控词表。** 自由文本的否决理由攒三个月
之后没法统计，而「模型编引文」和「口径理解错」需要的是两种完全不同的修复。
"""

import json
import logging
import os
import sys

from sqlalchemy import func, insert, select, update

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

import clock  # noqa: E402
from radar_db import make_engine  # noqa: E402
from radar_db.schema import (  # noqa: E402
    annotation_evidence,
    annotations,
    review_decisions,
)

log = logging.getLogger("worker.review")

# 未裁决的两种状态。`needs_review` 是管线自己举的手（模型说存疑、或证据定位不到），
# 优先级高于普通 `pending` —— 它已经有一条具体的怀疑理由了。
_OPEN = ("needs_review", "pending")

# 否决理由词表。自由文本攒三个月后没法统计，而这几类各自对应完全不同的修复动作。
REASONS = {
    "hallucinated_evidence": "引文不在原文里",
    "wrong_attitude": "态度判反了",
    "wrong_subject": "判到了别的产品头上",
    "market_not_product": "把市场看法当成了产品态度（§11.1）",
    "caliber": "口径理解错（PRD 第 3 章）",
    "unusable_source": "原文本身无法判断",
    "other": "其他（请在提交信息里说明）",
}

_DECISIONS = ("approve", "reject", "correct")

# 裁决 → annotations.review_state。`correct` 落在被取代的**旧行**上，新行另写。
_STATE = {"approve": "approved", "reject": "rejected", "correct": "corrected"}


class ReviewError(ValueError):
    pass


# ── 队列 ───────────────────────────────────────────────────────────────


def _superseded():
    """子查询：被别的行取代过的 annotation_id。

    旧行被新一轮重跑取代后就不再是当前值了，让人去裁决它没有意义。

    ADR-0019 之前这里还有一个更糟的后果：`--approve` 会把一个已经作废的值标成
    「人工通过」，而当时的 `SqlProvider` 恰好只读 approved —— 于是一个过期结论被
    人工背书着送上了界面。现在发布看的是链末，那条路已经堵上了；但反过来的坑还在：
    对旧行 `--reject` 不会让链末那条下线，队列只列链末就是为了不让人误以为它会。
    """
    return select(annotations.c.supersedes_id).where(
        annotations.c.supersedes_id.isnot(None)
    )


def queue(engine, *, kind=None, limit=20):
    """待复核的标注，`needs_review` 排在 `pending` 前面，只列链条末端的行。"""
    with engine.connect() as conn:
        q = (
            select(annotations)
            .where(
                annotations.c.review_state.in_(_OPEN),
                annotations.c.annotation_id.notin_(_superseded()),
            )
            # needs_review 先出：它已经带着一条具体的怀疑理由了。
            .order_by(
                (annotations.c.review_state == "pending").asc(),
                annotations.c.annotation_id.asc(),
            )
        )
        if kind:
            q = q.where(annotations.c.kind == kind)
        return conn.execute(q.limit(limit)).mappings().all()


def open_count(engine, *, kind=None):
    """按 review_state 数一下还欠多少。

    过滤条件必须和 `queue()` 完全一致，否则「待复核 340 条」下面跟着一份
    永远走不到 340 的列表 —— 复核是个要干到零的活，计数和队列对不上就没法收尾。
    """
    with engine.connect() as conn:
        q = (
            select(annotations.c.review_state, func.count())
            .where(
                annotations.c.review_state.in_(_OPEN),
                annotations.c.annotation_id.notin_(_superseded()),
            )
            .group_by(annotations.c.review_state)
        )
        if kind:
            q = q.where(annotations.c.kind == kind)
        rows = conn.execute(q).all()
    return {state: n for state, n in rows}


def detail(engine, annotation_id):
    """一条标注连同它的证据，供人眼判断用。"""
    with engine.connect() as conn:
        row = conn.execute(
            select(annotations).where(annotations.c.annotation_id == annotation_id)
        ).mappings().first()
        if row is None:
            raise ReviewError(f"annotation {annotation_id} 不存在")
        ev = conn.execute(
            select(annotation_evidence).where(
                annotation_evidence.c.annotation_id == annotation_id
            )
        ).mappings().all()
    return dict(row), [dict(e) for e in ev]


# ── 裁决 ───────────────────────────────────────────────────────────────


def decide(engine, annotation_id, decision, reviewer, *, value=None, reason=None):
    """记一次人工裁决。返回 `corrected` 时新建的 annotation_id，否则 None。

    参数校验放在动数据库之前，而且是全部校验完再动：复核是人在敲命令，
    敲错一个词就写进去一半，比直接报错难收拾得多。
    """
    if decision not in _DECISIONS:
        raise ReviewError(f"decision 只能是 {'/'.join(_DECISIONS)}，收到 {decision!r}")
    if not reviewer:
        raise ReviewError("reviewer 不能为空 —— 裁决必须能追到人")
    if decision == "correct" and value is None:
        raise ReviewError("--correct 必须给出改正后的值")
    if decision != "correct" and value is not None:
        raise ReviewError("只有 --correct 才带值")
    if reason is not None and reason not in REASONS:
        raise ReviewError(
            f"reason 不在词表里：{reason!r}。可选：{'、'.join(REASONS)}"
        )
    if decision == "reject" and reason is None:
        raise ReviewError("--reject 必须给 --reason —— 否决理由攒不起来就没法改模型")

    now = clock.now()
    with engine.begin() as conn:
        from radar_db.revisions import bump_revision
        bump_revision(conn, "annotation")
        row = conn.execute(
            select(annotations).where(annotations.c.annotation_id == annotation_id)
        ).mappings().first()
        if row is None:
            raise ReviewError(f"annotation {annotation_id} 不存在")
        newer = conn.execute(
            select(annotations.c.annotation_id)
            .where(annotations.c.supersedes_id == annotation_id)
            .limit(1)
        ).scalar()
        if newer is not None:
            raise ReviewError(
                f"annotation {annotation_id} 已被 #{newer} 取代，请裁决新的那条"
            )

        if decision in ("reject", "correct"):
            from radar_db.revisions import mark_synthesis
            mark_synthesis(conn, [row["subject_code"]], True)

        res = conn.execute(
            insert(review_decisions).values(
                annotation_id=annotation_id,
                reviewer=reviewer,
                decision=decision,
                corrected_value_json=(
                    json.dumps(value, ensure_ascii=False) if decision == "correct" else None
                ),
                reason_code=reason,
                reviewed_at=now,
            )
        )
        decision_id = res.inserted_primary_key[0]

        conn.execute(
            update(annotations)
            .where(annotations.c.annotation_id == annotation_id)
            .values(review_state=_STATE[decision])
        )

        if decision != "correct":
            return None

        # 改正另起一行，指向被它取代的那条。旧行的 value_json 原样留着 ——
        # 「模型当初判的是什么」是 Gate 3 训练金标时的对照，原地改会让它永久消失。
        res = conn.execute(
            insert(annotations).values(
                target_type=row["target_type"],
                target_id=row["target_id"],
                subject_code=row["subject_code"],
                kind=row["kind"],
                value_json=json.dumps(value, ensure_ascii=False),
                # 人改的值没有概率可言。写 1.0 会让它冒充一个校准过的高置信结论。
                calibrated_confidence=None,
                # `uq_annotations_unit` 是 (target,subject,kind,input_hash,run_id)：
                # 沿用旧行的 run_id 会和旧行**逐列相同**，直接撞唯一键。所以改正自带
                # 一个 run_id，指回产生它的那次裁决 —— 它有意不在 `annotation_runs`
                # 里，因为人改一个值不是一次模型运行，硬写一行 runs 就得编 provider
                # 和 model_id。
                run_id=f"review:{decision_id}",
                # 指纹沿用：判定的**输入**没变，变的是结论。改指纹会让下一轮重跑
                # 把它当成一件新待办，再花一次钱去覆盖人刚改过的值。
                input_hash=row["input_hash"],
                # 新行是 `approved` 而不是 `corrected`：`corrected` 的意思是「这一行被
                # 人改过」，那说的是**旧**行。新行的值本来就出自人手、也已被人认可，
                # 它的状态就是「人工通过」。两者都在 `_HUMAN_SETTLED` 里，重跑保护不变。
                review_state="approved",
                created_at=now,
                supersedes_id=annotation_id,
            )
        )
        return res.inserted_primary_key[0]


# ── CLI ────────────────────────────────────────────────────────────────


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description="AI 标注人工复核")
    ap.add_argument("--queue", action="store_true", help="列出待复核的标注")
    ap.add_argument("--next", action="store_true", help="只看队首那一条的详情")
    ap.add_argument("--id", type=int, help="要裁决的 annotation_id")
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--reject", action="store_true")
    ap.add_argument("--correct", metavar="JSON", help="改正后的值，JSON 字面量")
    ap.add_argument("--reviewer", help="复核人，写进 review_decisions")
    ap.add_argument("--reason", help=f"否决理由，取值：{'、'.join(REASONS)}")
    ap.add_argument("--kind", help="只看某一类（attitude / post_type / summary …）")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(message)s")
    engine = make_engine()

    picked = [d for d, on in
              (("approve", args.approve), ("reject", args.reject),
               ("correct", args.correct is not None)) if on]
    if len(picked) > 1:
        ap.error("--approve / --reject / --correct 只能选一个")

    if picked:
        if args.id is None:
            ap.error("裁决要带 --id")
        value = None
        if picked[0] == "correct":
            try:
                value = json.loads(args.correct)
            except json.JSONDecodeError as e:
                # 提醒引号：attitude 这种字符串值要写成 '"negative"'，shell 会吃掉一层。
                ap.error(f"--correct 的值不是合法 JSON（{e}）；字符串要写成 '\"negative\"'")
        try:
            new_id = decide(engine, args.id, picked[0], args.reviewer,
                            value=value, reason=args.reason)
        except ReviewError as e:
            ap.error(str(e))
        print(f"已记录：annotation {args.id} → {_STATE[picked[0]]}"
              + (f"，改正写入新行 {new_id}（approved）" if new_id else ""))
        return 0

    if args.next:
        rows = queue(engine, kind=args.kind, limit=1)
        if not rows:
            print("队列空了。")
            return 0
        _print_detail(engine, rows[0]["annotation_id"])
        return 0

    _print_queue(engine, args.kind, args.limit)
    return 0


def _print_queue(engine, kind, limit):
    counts = open_count(engine, kind=kind)
    total = sum(counts.values())
    print("待复核：" + ("、".join(f"{k} {v}" for k, v in sorted(counts.items())) or "空"))
    for r in queue(engine, kind=kind, limit=limit):
        print(
            f"  #{r['annotation_id']:<7} {r['review_state']:<13} {r['kind']:<14}"
            f" {r['target_type']}:{r['target_id']}"
            f" {r['subject_code'] or '—':<8} {r['value_json'][:40]}"
        )
    if total > limit:
        # 截断必须说出来：只印 20 行而不吭声，看起来就像只剩 20 条。
        print(f"  …… 还有 {total - limit} 条未显示（--limit 调整）")


def _print_detail(engine, annotation_id):
    row, ev = detail(engine, annotation_id)
    print(f"#{row['annotation_id']}  {row['kind']}  {row['target_type']}:{row['target_id']}"
          f"  subject={row['subject_code'] or '—'}")
    print(f"  值      {row['value_json']}")
    print(f"  状态    {row['review_state']}   run={row['run_id']}")
    # 校准概率为 NULL 印「未校准」，不印 0 也不印「低」—— 它是「没有这个数」（铁律 2）。
    cc = row["calibrated_confidence"]
    print(f"  校准概率 {'未校准' if cc is None else f'{cc:.3f}'}")
    if ev:
        for e in ev:
            print(f"  证据    [{e['start_offset']}:{e['end_offset']}] {e['quote_text']}")
    else:
        print("  证据    无 —— 无法定位或模型没给")


if __name__ == "__main__":
    raise SystemExit(main())
