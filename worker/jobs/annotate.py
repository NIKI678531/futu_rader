"""AI 标注作业 —— 领取任务、调模型、校验、写库、重试、dead-letter（runbook §11.3）。

## 这个模块的立场

**宁可少写一行标注，不可写一行假的。** 六个具体表现：

1. Schema 不合、id 对不上、证据定位不到 —— 一律不写伪默认值。runbook §11.3 逐字：
   「达到最大次数进入 dead-letter，不写伪默认值」。写一个 `attitude='neutral'` 当兜底，
   在界面上它和真判断长得完全一样，而没有任何一列能把它们分开。
2. 模型自报的 confidence **不落 `calibrated_confidence`**（§11.1 末条）。那一列是给
   Gate 3 的概率校准用的；填模型自报值会让前端 0.7 阈值筛出一批没有意义的「高置信」。
3. 证据定位不到 ⇒ 整条 `needs_review`，但**结论照样落库**。态度判断可能是对的，
   只是证据不合格，交人工复核比整条丢掉省标注预算。
4. 已被人工 approve/correct 的结论，自动重跑**不覆盖**（§11.3 末条）。
5. 一批里有一条坏，先整批重试，再二分，最后只把真正坏的那几条判死 —— 不因为一条
   连坐 29 条。
6. `token_*` 取不到写 NULL，不写 0（铁律 2）。

## 幂等

`annotation_jobs` 上有 `(target_type, target_id, subject_code, task, input_hash)` 唯一键，
`input_hash` 含正文与四个版本号。所以：同样的输入＋同样的版本重复排队会被唯一键挡掉；
正文改了或 Prompt 换了版本，hash 变，是一件**新的**待办。这正是 §17.3 要的
「同一输入和版本重跑不产生冲突重复」。
"""

import json
import logging
import os
import sys
import uuid
from datetime import timedelta

from sqlalchemy import and_, func, insert, select, update

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.isdir(os.path.join(REPO_ROOT, "radar_db")) and REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import clock  # noqa: E402
from ai import config, evidence as ev, redact, schemas  # noqa: E402
from ai.prompts import get as get_prompt  # noqa: E402
from ai.providers import PermanentError, TransientError, build as build_provider  # noqa: E402
from radar_db import make_engine  # noqa: E402
from radar_db.schema import (  # noqa: E402
    NO_SUBJECT,
    annotation_evidence,
    annotation_jobs,
    annotation_runs,
    annotations,
    comments,
    feeds,
)

log = logging.getLogger("worker.annotate")

# 人工已裁决过的状态。自动重跑碰到这些一律让路（§11.3）。
_HUMAN_SETTLED = ("approved", "corrected")

LEASE_MINUTES = 15


class _Abort(Exception):
    """中止整轮，不是中止一批。

    永久错误（401、模型名打错、Schema 被网关拒绝）对**下一批**同样成立。而被放回的任务
    立刻又变回 `pending`，下一次 `claim` 会把它们原样捞出来 —— 不中止的话这个循环会
    一直空转到 `max_items` 耗尽，每一轮都完整地发一次请求。
    """


def prompt_version(prompt, cfg):
    """Prompt 版本以**模块里的 `VERSION` 为准**，不以 `AI_PROMPT_VERSION` 为准。

    runbook §6.2 的环境模板只有一个 `AI_PROMPT_VERSION`，但这里有两个任务、两套 Prompt，
    一个变量盖不住两个版本。模块常量跟 Prompt 正文在同一个文件里，改正文时不改它需要
    刻意视而不见；改环境变量则可以在完全不碰正文的情况下发生。两者不一致时出声 ——
    不一致意味着 `.env` 已经过期，而 `input_hash` 里落的是模块值。
    """
    if cfg.prompt_version and cfg.prompt_version != prompt.VERSION:
        log.warning(
            "AI_PROMPT_VERSION=%s 与 Prompt 模块的 %s 不一致，以模块为准",
            cfg.prompt_version, prompt.VERSION,
        )
    return prompt.VERSION


def new_run_id():
    """时间前缀＋随机尾巴。前缀让 run_id 按时间可排序（查「最近一次跑」不用 join 时间列），
    随机尾巴避免同一秒起两个 worker 撞 id。"""
    return clock.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]


# ── 排队 ───────────────────────────────────────────────────────────────


def enqueue_comments(engine, cfg, *, codes=None, limit=None, since=None, priority=0):
    """把「评论 × 产品」组合排进待办。

    判定单元是 `(comment_id, subject_code)`（§10.1），所以同一条评论评价两只 ETF
    会排成**两条**待办 —— 它们可以得出相反的态度，这正是单表方案做不到的事。

    产品用帖子的**挂载标的**（`mentions.source='anchor'`）。正文提及（`body`）先不排：
    一篇提到八只 ETF 的帖子，它下面的每条评论都排八条待办，成本是八倍，而其中大多数
    评论并没有在评价那八只。这条口径写在这里是有意的 —— 扩大范围是一个要单独决策的事。

    指纹用的是**将来真正会发出去的那个 payload**（含父评论与帖子标题）。§11.3 把
    「上下文」列进了缓存键，这不是形式要求：同一条「有」，挂在不同的父评论下就是不同
    的输入，应当各判一次。指纹只取正文的话，两者会被判成同一件待办，先来的那个结论
    会被沿用到另一个语境上。
    """
    task = "comment_product"
    prompt = get_prompt(task)
    now = clock.now()
    rows = []

    with engine.connect() as conn:
        parent = comments.alias("parent")
        q = (
            select(
                comments.c.comment_id,
                comments.c.content,
                feeds.c.code,
                feeds.c.title,
                parent.c.content.label("parent_content"),
            )
            .select_from(
                comments
                .join(feeds, feeds.c.feed_id == comments.c.feed_id)
                # 父评论是可选的，外连接 —— 内连接会把所有非回复的评论排除在队列外。
                .outerjoin(parent,
                           parent.c.comment_id == comments.c.reply_to_comment_id)
            )
            .where(comments.c.content.isnot(None), comments.c.content != "")
        )
        if codes:
            q = q.where(feeds.c.code.in_(list(codes)))
        if since:
            q = q.where(feeds.c.posted_at >= since)
        # 稳定顺序：同样的参数每次取到同一批，重跑可复现。
        q = q.order_by(comments.c.comment_id)
        if limit:
            q = q.limit(limit)

        for comment_id, content, code, title, parent_content in conn.execute(q):
            payload = _build_payload(
                task,
                {"target_id": comment_id, "subject_code": code},
                {"text": content, "title": title, "parent": parent_content},
            )
            rows.append(
                {
                    "target_type": "comment",
                    "target_id": comment_id,
                    "subject_code": code,
                    "task": task,
                    "input_hash": schemas.input_hash(
                        payload,
                        model=cfg.model,
                        prompt_version=prompt_version(prompt, cfg),
                        taxonomy_version=cfg.taxonomy_version,
                        schema_version=cfg.schema_version,
                    ),
                    "status": "pending",
                    "priority": priority,
                    "attempts": 0,
                    "created_at": now,
                    "updated_at": now,
                }
            )
    return _insert_jobs(engine, rows)


def _insert_jobs(engine, rows):
    """逐条插入并吞掉唯一键冲突 —— 冲突**就是**幂等生效，不是错误。

    没有用方言相关的 `INSERT OR IGNORE` / `ON DUPLICATE KEY`：这份代码要在 SQLite 和
    MySQL 8 上跑同一份（ADR-0016），而两边的写法不通用。排队是一次性动作，不在热路径上。
    """
    inserted = 0
    with engine.begin() as conn:
        for row in rows:
            try:
                with conn.begin_nested():
                    conn.execute(insert(annotation_jobs).values(**row))
                inserted += 1
            except Exception:  # noqa: BLE001  唯一键冲突＝已排过队
                continue
    log.info("排队：新增 %d 条，跳过 %d 条（已存在）", inserted, len(rows) - inserted)
    return inserted


# ── 领取 ───────────────────────────────────────────────────────────────


def claim(engine, task, n, *, now=None):
    """领取至多 n 条待办，打上租约。

    可领取 = `pending`，或 `claimed` 但租约已过期。后者是 worker 崩溃后的回收路径 ——
    没有它，进程被 Ctrl-C 掉的那一刻正在处理的 30 条会永远卡在 claimed。
    """
    now = now or clock.now()
    lease_until = now + timedelta(minutes=LEASE_MINUTES)
    claimed = []
    with engine.begin() as conn:
        q = (
            select(annotation_jobs)
            .where(
                annotation_jobs.c.task == task,
                (annotation_jobs.c.status == "pending")
                | and_(
                    annotation_jobs.c.status == "claimed",
                    annotation_jobs.c.lease_until < now,
                ),
            )
            # 同一批尽量属于同一产品（§11.3）：按 subject_code 排，固定上下文能被
            # 供应商的 prompt cache 命中，也让模型少切换产品语境。
            .order_by(
                annotation_jobs.c.priority.desc(),
                annotation_jobs.c.subject_code,
                annotation_jobs.c.job_id,
            )
            .limit(n)
        )
        for row in conn.execute(q).mappings():
            conn.execute(
                update(annotation_jobs)
                .where(annotation_jobs.c.job_id == row["job_id"])
                .values(status="claimed", claimed_at=now, lease_until=lease_until)
            )
            claimed.append(dict(row))
    return claimed


# ── 跑一批 ─────────────────────────────────────────────────────────────


def run(engine, cfg=None, *, task="comment_product", max_items=None, provider=None):
    """跑一轮标注。返回 run 统计。"""
    cfg = cfg or config.load()
    provider = provider or build_provider(cfg)
    prompt = get_prompt(task)

    batch_size = cfg.micro_batch_size
    budget = max_items if max_items is not None else batch_size
    run_id = new_run_id()
    started = clock.now()

    stats = {
        "run_id": run_id, "input": 0, "success": 0, "error": 0,
        "tok_in": 0, "tok_out": 0, "tok_reason": 0, "usage_known": True,
        "model": cfg.model, "aborted": None,
    }

    with engine.begin() as conn:
        conn.execute(
            insert(annotation_runs).values(
                run_id=run_id, task=task, provider=cfg.provider,
                model_id=cfg.model, model_revision=None,
                prompt_version=prompt_version(prompt, cfg),
                taxonomy_version=cfg.taxonomy_version,
                schema_version=cfg.schema_version,
                started_at=started, status="running",
                input_count=0, success_count=0, error_count=0,
            )
        )

    try:
        while budget > 0:
            jobs = claim(engine, task, min(batch_size, budget))
            if not jobs:
                break
            budget -= len(jobs)
            stats["input"] += len(jobs)
            _process(engine, cfg, provider, prompt, task, run_id, jobs, stats)
    except _Abort as exc:
        stats["aborted"] = str(exc)
        log.error("整轮中止：%s", exc)
    finally:
        _close_run(engine, run_id, stats)

    return stats


def _close_run(engine, run_id, stats):
    with engine.begin() as conn:
        conn.execute(
            update(annotation_runs)
            .where(annotation_runs.c.run_id == run_id)
            .values(
                finished_at=clock.now(),
                status=(
                    "failed" if stats["aborted"]
                    else "done" if stats["error"] == 0
                    else "partial"
                ),
                # schema 里写明这一列记的是**供应商返回的**模型名：网关会做别名转发，
                # 请求 `gpt-5.6-luna` 实际跑的可能是另一个快照。开跑时只能先落请求值，
                # 收工时用观测到的真实值覆盖。
                model_id=stats["model"],
                input_count=stats["input"],
                success_count=stats["success"],
                error_count=stats["error"],
                # 用量任何一批取不到 ⇒ 整个 run 的合计是**未知**，写 NULL。
                # 写部分和会让成本报表少算而看不出来（铁律 2、`_add_all` 同一个道理）。
                token_input=stats["tok_in"] if stats["usage_known"] else None,
                token_output=stats["tok_out"] if stats["usage_known"] else None,
                token_reasoning=stats["tok_reason"] if stats["usage_known"] else None,
            )
        )


def _process(engine, cfg, provider, prompt, task, run_id, jobs, stats, depth=0):
    """处理一批。失败时按 §11.3 二分，而不是整批判死。"""
    sources = _load_sources(engine, task, jobs)
    payloads, usable = [], []
    for job in jobs:
        src = sources.get((job["target_type"], job["target_id"]))
        if src is None or not (src.get("text") or "").strip():
            # 正文取不到 —— 规则层就该拦下（§6.5「空文本」不调 GPT）。判死不重试。
            _fail(engine, job, "源文本为空或不存在，不应进队列", dead=True)
            stats["error"] += 1
            continue
        payloads.append(_build_payload(task, job, src))
        usable.append((job, src))

    if not payloads:
        return

    try:
        comp = provider.complete_json(
            prompt.SYSTEM,
            prompt.user_message(payloads),
            schemas.batch_json_schema(task),
            f"{task}_batch",
        )
    except PermanentError as exc:
        # 401/400 这类：重试无意义，而且多半是配置问题，整批放回 pending 等人改配置。
        # **不判死** —— 把 30 条因为一个 Key 打错而判死，改完配置后还得手动复活。
        for job, _ in usable:
            _release(engine, job, f"永久错误：{exc}")
        stats["error"] += len(usable)
        raise _Abort(f"永久错误，{len(usable)} 条已放回待办：{exc}") from exc
    except TransientError as exc:
        # 供应商层已经退避重试过 max_retries 次了，到这里说明确实不通。
        _retry_or_dead(engine, cfg, usable, stats, f"传输失败：{exc}")
        return

    _record_usage(comp, stats)
    stats["model"] = comp.model

    try:
        by_id = schemas.parse_batch(task, comp.data, [p["item_id"] for p in payloads])
    except schemas.SchemaError as exc:
        # §11.3：JSON/Schema 失败先重试；连续失败后将批次二分。
        if len(usable) > 1 and depth < 6:
            mid = len(usable) // 2
            log.warning("批输出不合格（%s），二分为 %d + %d", exc, mid, len(usable) - mid)
            _process(engine, cfg, provider, prompt, task, run_id,
                     [j for j, _ in usable[:mid]], stats, depth + 1)
            _process(engine, cfg, provider, prompt, task, run_id,
                     [j for j, _ in usable[mid:]], stats, depth + 1)
        else:
            _retry_or_dead(engine, cfg, usable, stats, f"schema 失败：{exc}")
        return

    for job, src in usable:
        item = by_id[_item_id(task, job)]
        try:
            _write(engine, task, job, src, item, run_id)
            _done(engine, job)
            stats["success"] += 1
        except Exception as exc:  # noqa: BLE001
            log.exception("写库失败 job=%s", job["job_id"])
            _fail(engine, job, f"写库失败：{exc}")
            stats["error"] += 1


def _record_usage(comp, stats):
    u = comp.usage
    if u.input_tokens is None or u.output_tokens is None:
        stats["usage_known"] = False
        return
    stats["tok_in"] += u.input_tokens
    stats["tok_out"] += u.output_tokens
    stats["tok_reason"] += u.reasoning_tokens or 0


# ── 取源文本 ───────────────────────────────────────────────────────────


def _load_sources(engine, task, jobs):
    """一次查回整批的源文本与上下文。逐条查会让 30 条变成 30 次往返。

    评论还带两样上下文，都是 §11.4 明确许可外发的：**父评论**与**帖子标题**。
    带它们不是锦上添花 —— 首轮 100 条影子运行里 40% 判成 `needs_context`，样本是
    「有」「劲」「是股息」这种一两个字的回复。那个判断是**对的**：光看这三个字确实
    判不出在夸哪只 ETF。缺的不是模型能力，是我们没把它该看的东西发过去。

    帖子**正文**没有带（虽然 §11.4 也允许）。一批 30 条常常来自同一篇帖子，正文会被
    重复发 30 次；要不要带、带多长是一个有成本的决策，且 Prompt 要相应交代它的地位，
    不适合顺手塞进来。
    """
    ids = [j["target_id"] for j in jobs]
    out = {}
    if not ids:
        return out
    with engine.connect() as conn:
        if task == "comment_product":
            parent = comments.alias("parent")
            q = (
                select(
                    comments.c.comment_id,
                    comments.c.content,
                    feeds.c.title,
                    parent.c.content.label("parent_content"),
                )
                .select_from(
                    comments
                    # 外连接：取不到帖子或父评论时，这条评论**仍然要出现**在结果里。
                    # 内连接会让它悄悄消失，然后被 `_process` 当成「源文本不存在」判死。
                    .outerjoin(feeds, feeds.c.feed_id == comments.c.feed_id)
                    .outerjoin(parent,
                               parent.c.comment_id == comments.c.reply_to_comment_id)
                )
                .where(comments.c.comment_id.in_(ids))
            )
            for cid, content, title, parent_content in conn.execute(q):
                out[("comment", cid)] = {
                    "text": content,
                    "title": title,
                    "parent": parent_content,
                }
        else:
            q = select(feeds.c.feed_id, feeds.c.title, feeds.c.content).where(
                feeds.c.feed_id.in_(ids)
            )
            for fid, title, content in conn.execute(q):
                out[("feed", fid)] = {"text": content, "title": title}
    return out


def _item_id(task, job):
    if task == "comment_product":
        return f"comment:{job['target_id']}|product:{job['subject_code']}"
    return f"feed:{job['target_id']}"


def _build_payload(task, job, src):
    if task == "comment_product":
        return redact.comment_payload(
            _item_id(task, job),
            {"code": job["subject_code"]},
            src["text"],
            post_title=src.get("title"),
            parent_comment=src.get("parent"),
        )
    return redact.post_payload(_item_id(task, job), src["text"], title=src.get("title"))


# ── 写结论 ─────────────────────────────────────────────────────────────


def _write(engine, task, job, src, item, run_id):
    """把一条标注写进库，连同通过校验的证据。

    一条模型输出会拆成**多行** `annotations`（每个 kind 一行），因为
    `relevance` / `attitude` / `aspect` 在 §9 里是不同的原子任务，
    SqlProvider 与 `backend/core/` 按 kind 读，混在一行会逼每个读取方自己拆 JSON。
    """
    now = clock.now()
    source_text = src["text"] or ""
    if task == "post_annotation":
        source_text = "\n".join(filter(None, [src.get("title"), src.get("text")]))

    if task == "comment_product":
        spans = [item.evidence] if item.evidence else []
        kinds = [("relevance", item.relevance), ("attitude", item.attitude)]
        if item.aspects:
            kinds.append(("aspect", item.aspects))
        # 相关但没给出可定位证据 ⇒ 存疑。无关/需上下文本来就没有证据可给，不算问题。
        expect_evidence = item.relevance == "relevant"
    else:
        spans = list(item.evidence_spans or [])
        kinds = [("post_type", item.post_type)]
        if item.summary is not None:
            kinds.append(("summary", item.summary))
        if item.direction is not None or item.direction_pending:
            kinds.append(
                ("direction", item.direction if not item.direction_pending else "pending")
            )
        expect_evidence = True

    located = [(s, ev.locate(s, source_text)) for s in spans]
    verified = [(s, loc) for s, loc in located if loc.found]
    # 模型给了证据但一条都定位不到 ⇒ 证据是编的（Gate 0 复现过）。结论仍落库，
    # 但打 needs_review 交人工 —— 判断可能是对的，编造的只是引文。
    bad_evidence = bool(spans) and not verified
    missing_evidence = expect_evidence and not spans
    needs_review = bool(item.needs_review) or bad_evidence or missing_evidence

    with engine.begin() as conn:
        first_id = None
        for kind, value in kinds:
            if value is None:
                continue
            prev = conn.execute(
                select(annotations.c.annotation_id, annotations.c.review_state)
                .where(
                    annotations.c.target_type == job["target_type"],
                    annotations.c.target_id == job["target_id"],
                    annotations.c.subject_code == job["subject_code"],
                    annotations.c.kind == kind,
                )
                .order_by(annotations.c.annotation_id.desc())
                .limit(1)
            ).first()
            # §11.3 末条：模型失败不得覆盖旧的已确认结果。人工裁决过的旧行不被 supersede，
            # 新行照写但保持 pending，由人再看一次。
            supersedes = (
                prev.annotation_id
                if prev is not None and prev.review_state not in _HUMAN_SETTLED
                else None
            )
            res = conn.execute(
                insert(annotations).values(
                    target_type=job["target_type"],
                    target_id=job["target_id"],
                    subject_code=job["subject_code"] or NO_SUBJECT,
                    kind=kind,
                    value_json=json.dumps(value, ensure_ascii=False),
                    # 模型自报的 confidence 不是概率，这一列留给 Gate 3 的校准（§11.1）。
                    calibrated_confidence=None,
                    run_id=run_id,
                    input_hash=job["input_hash"],
                    review_state="needs_review" if needs_review else "pending",
                    created_at=now,
                    supersedes_id=supersedes,
                )
            )
            first_id = first_id or res.inserted_primary_key[0]

        for quote, loc in verified:
            conn.execute(
                insert(annotation_evidence).values(
                    annotation_id=first_id,
                    source_target_type=job["target_type"],
                    source_target_id=job["target_id"],
                    # 偏移是**程序定位**出来的，不采信模型自报的位置。
                    start_offset=loc.start,
                    end_offset=loc.end,
                    # 落原文切片，不落模型给的那个串（可能差一个全角标点）。
                    quote_text=loc.quote,
                    quote_hash=ev.quote_hash(loc.quote),
                )
            )


# ── 任务状态流转 ───────────────────────────────────────────────────────


def _done(engine, job):
    with engine.begin() as conn:
        conn.execute(
            update(annotation_jobs)
            .where(annotation_jobs.c.job_id == job["job_id"])
            .values(status="done", updated_at=clock.now(), last_error=None,
                    lease_until=None)
        )


def _fail(engine, job, err, dead=False):
    with engine.begin() as conn:
        conn.execute(
            update(annotation_jobs)
            .where(annotation_jobs.c.job_id == job["job_id"])
            .values(
                status="dead" if dead else "failed",
                attempts=annotation_jobs.c.attempts + 1,
                last_error=str(err)[:2000],
                updated_at=clock.now(),
                lease_until=None,
            )
        )


def _release(engine, job, err):
    """放回 pending，不计 attempts —— 这次失败不是这条任务的错（配置错、Key 错）。
    计进 attempts 会让一次配置事故把整队任务推向 dead-letter。"""
    with engine.begin() as conn:
        conn.execute(
            update(annotation_jobs)
            .where(annotation_jobs.c.job_id == job["job_id"])
            .values(status="pending", last_error=str(err)[:2000],
                    updated_at=clock.now(), lease_until=None)
        )


def _retry_or_dead(engine, cfg, usable, stats, err):
    """超过最大次数进 dead-letter，否则放回重试（§11.3）。"""
    now = clock.now()
    with engine.begin() as conn:
        for job, _ in usable:
            attempts = job["attempts"] + 1
            dead = attempts >= cfg.max_retries
            conn.execute(
                update(annotation_jobs)
                .where(annotation_jobs.c.job_id == job["job_id"])
                .values(
                    status="dead" if dead else "pending",
                    attempts=attempts,
                    last_error=str(err)[:2000],
                    updated_at=now,
                    lease_until=None,
                )
            )
            stats["error"] += 1
    log.warning("批失败（%s），%d 条已按重试策略处理", err, len(usable))


def pending_count(engine, task):
    with engine.connect() as conn:
        return conn.execute(
            select(func.count())
            .select_from(annotation_jobs)
            .where(annotation_jobs.c.task == task,
                   annotation_jobs.c.status.in_(("pending", "claimed")))
        ).scalar_one()


# ── CLI ────────────────────────────────────────────────────────────────


def main(argv=None):
    """影子运行入口。

        python -m jobs.annotate --enqueue --codes 3033,2822 --limit 100
        python -m jobs.annotate --run --max-items 100

    `--enqueue` 与 `--run` **分开两步**，不是一个命令里顺次做完：排队不花钱，跑标注花钱。
    分开之后可以先排队、看一眼 `--status` 的数量对不对，再决定要不要发出去。
    """
    import argparse

    ap = argparse.ArgumentParser(description="AI 标注作业")
    ap.add_argument("--enqueue", action="store_true", help="把评论×产品排进待办")
    ap.add_argument("--run", action="store_true", help="领取待办并调模型")
    ap.add_argument("--status", action="store_true", help="只看队列状态")
    ap.add_argument("--task", default="comment_product")
    ap.add_argument("--codes", help="逗号分隔的产品代码，留空＝全部")
    ap.add_argument("--limit", type=int, help="排队条数上限")
    ap.add_argument("--max-items", type=int, help="本轮最多处理多少条")
    ap.add_argument("--priority", type=int, default=0)
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s %(message)s"
    )
    engine = make_engine()
    cfg = config.load()
    # 配置里唯一敏感的是 Key，`redacted()` 只留尾四位 —— 够分辨「换过 Key 没有」，
    # 又不会把它写进任何一份可能被贴出去的日志（runbook §0）。
    log.info("配置：%s", json.dumps(cfg.redacted(), ensure_ascii=False))

    if args.enqueue:
        codes = [c.strip() for c in args.codes.split(",")] if args.codes else None
        annotate_n = enqueue_comments(engine, cfg, codes=codes, limit=args.limit,
                                      priority=args.priority)
        log.info("已排队 %d 条", annotate_n)

    if args.run:
        stats = run(engine, cfg, task=args.task, max_items=args.max_items)
        log.info("本轮：%s", json.dumps(stats, ensure_ascii=False))

    if args.status or not (args.enqueue or args.run):
        _print_status(engine, args.task)
    return 0


def _print_status(engine, task):
    with engine.connect() as conn:
        counts = conn.execute(
            select(annotation_jobs.c.status, func.count())
            .where(annotation_jobs.c.task == task)
            .group_by(annotation_jobs.c.status)
        ).all()
        runs = conn.execute(
            select(annotation_runs)
            .where(annotation_runs.c.task == task)
            .order_by(annotation_runs.c.started_at.desc())
            .limit(5)
        ).mappings().all()

    print(f"[{task}] 队列：" + ("、".join(f"{s} {n}" for s, n in counts) or "空"))
    for r in runs:
        print(
            f"  {r['run_id']}  {r['status']:<8} "
            f"in={r['input_count']} ok={r['success_count']} err={r['error_count']}  "
            f"model={r['model_id']} prompt={r['prompt_version']} "
            f"tax={r['taxonomy_version']} schema={r['schema_version']}  "
            # 用量为 NULL 显示「未知」，不显示 0 —— 它们是两件事（铁律 2）。
            f"tok={_fmt(r['token_input'])}/{_fmt(r['token_output'])}"
            f"(+{_fmt(r['token_reasoning'])} 推理)"
        )


def _fmt(v):
    return "未知" if v is None else f"{v:,}"


if __name__ == "__main__":
    raise SystemExit(main())
