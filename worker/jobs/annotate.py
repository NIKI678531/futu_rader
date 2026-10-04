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

## 并发（ADR-0020）

`AI_CONCURRENCY` 路线程各自处理一批。领取（`claim`）在进程内用一把锁串行化 —— SQLite 下
两个事务同时 SELECT 到同一批再各自 UPDATE，会把同一条任务发两遍。锁是进程内的：这版
只支持**单进程多线程**；要跨进程就得换 `SELECT … FOR UPDATE SKIP LOCKED`（MySQL）。
统计按批各记一份，回到主线程再合并，不在线程里碰共享的 dict。
"""

import json
import logging
import math
import os
import sys
import threading
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import and_, func, insert, or_, select, update, tuple_, union
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.isdir(os.path.join(REPO_ROOT, "radar_db")) and REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import clock  # noqa: E402
from ai import config, evidence as ev, neardup, redact, schemas  # noqa: E402
from ai.batching import BatchPolicy, pack_items
from ai.providers.base import TruncatedOutput
from ai.lexicon import product_aliases  # noqa: E402
from ai.prompts import (  # noqa: E402
    SCHEMA_OF,
    get as get_prompt,
    requires_full_reannotation,
)
from ai.providers import PermanentError, TransientError, build as build_provider  # noqa: E402
from collection.exact import EXACT_RULE_VERSION  # noqa: E402
from jobs.scope_policy import load_current_exact_scope  # noqa: E402
from radar_db import make_engine  # noqa: E402
from radar_db.comment_filter import (  # noqa: E402
    load_comment_filter_config,
    qualifying_feed_scope_predicate,
    require_filter_ready,
    require_filter_ready_on_connection,
)
from radar_db.comment_routes import (  # noqa: E402
    COMMENT_ROUTE_VERSION,
    readiness_on_connection as comment_routes_ready_on_connection,
    route_exists_predicate,
    supersede_ineligible_jobs as supersede_ineligible_route_jobs,
)
from radar_db.events import emit  # noqa: E402
from radar_db.schema import (  # noqa: E402
    NO_SUBJECT,
    annotation_evidence,
    annotation_jobs,
    annotation_runs,
    annotations,
    comments,
    comment_product_routes,
    feeds,
    mentions,
)
from radar_db.time_windows import hkt_range_utc_naive, utc_naive_to_hkt  # noqa: E402

log = logging.getLogger("worker.annotate")

# 人工已裁决过的状态。自动重跑碰到这些一律让路（§11.3）。
_HUMAN_SETTLED = ("approved", "corrected")

LEASE_MINUTES = 15
FILL_MISSING_STAGE = "llm_fill"  # annotation_jobs.stage is VARCHAR(10)
STAGE_STUDENT = "student"
STAGE_LLM = "llm"


def default_stage(task):
    """Return the first processing stage for a newly queued task."""
    return STAGE_STUDENT if task == "comment_product" else STAGE_LLM

# 帖子正文作为评论上下文时只带开头这么多字。一批 30 条常来自同一篇帖子，正文按 feed_id
# 只放一次（见 `_user_message_with_context`），但仍要有上限 —— 长文会把系统提示挤出缓存窗口。
POST_CONTEXT_CHARS = 200

# 用量估算常数：Gate 2 实测 129 条 ⇒ 14,491 输入 / 9,711 输出 token（含 2,206 推理）。
# v2 Prompt 更长、输出多三个字段，按 1.4 倍放大。只用于 `--dry-run`，不进任何报表。
EST_IN_PER_ITEM = 112 * 1.4
EST_OUT_PER_ITEM = 75 * 1.4

_CLAIM_LOCK = threading.Lock()
_LEXICON = None


def _lexicon():
    global _LEXICON  # noqa: PLW0603  进程内单例，生产词表构造一次即可
    if _LEXICON is None:
        _LEXICON = product_aliases.ProductLexicon()
    return _LEXICON


class _Abort(Exception):
    """中止整轮，不是中止一批。

    永久错误（401、模型名打错、Schema 被网关拒绝）对**下一批**同样成立。而被放回的任务
    立刻又变回 `pending`，下一次 `claim` 会把它们原样捞出来 —— 不中止的话这个循环会
    一直空转到 `max_items` 耗尽，每一轮都完整地发一次请求。
    """


# ── 版本解析 ────────────────────────────────────────────────────────────


def resolve(task, cfg):
    """取该任务要用的 Prompt 模块与 schema 版本。

    一个全局 `AI_PROMPT_VERSION` 会被多个任务共享：当它属于别的任务时，按 schema 选择
    本任务的配对 Prompt；当它明确属于本任务却与 schema 不匹配时直接报配置错误。
    绝不让 v2 Prompt 套 v1 strict schema 后悄悄丢字段。
    """
    prompt = get_prompt(task, cfg.prompt_version, schema_version=cfg.schema_version)
    paired = SCHEMA_OF.get(prompt.VERSION)
    if paired != cfg.schema_version:
        raise config.ConfigError(
            f"Prompt {prompt.VERSION} 配对 schema {paired}，"
            f"但 AI_SCHEMA_VERSION={cfg.schema_version}；请使用匹配版本"
        )
    return prompt, paired


def prompt_version(prompt, cfg):
    """Prompt 版本以**模块里的 `VERSION` 为准**，不以 `AI_PROMPT_VERSION` 为准。

    runbook §6.2 的环境模板只有一个 `AI_PROMPT_VERSION`，但这里有两个任务、两套 Prompt，
    一个变量盖不住两个版本。模块常量跟 Prompt 正文在同一个文件里，改正文时不改它需要
    刻意视而不见；改环境变量则可以在完全不碰正文的情况下发生。
    """
    return prompt.VERSION


def new_run_id():
    """时间前缀＋随机尾巴。前缀让 run_id 按时间可排序（查「最近一次跑」不用 join 时间列），
    随机尾巴避免同一秒起两个 worker 撞 id。"""
    return clock.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]


# ── 排队 ───────────────────────────────────────────────────────────────


def recency_tier(posted_at, anchor):
    if posted_at is None or anchor is None:
        return 0
    day = utc_naive_to_hkt(posted_at).date() if isinstance(posted_at, datetime) else posted_at
    anchor_day = anchor.date() if isinstance(anchor, datetime) else anchor
    age = max(0, (anchor_day - day).days)
    for max_days, bonus in ((7, 30), (14, 20), (30, 10)):
        if age <= max_days:
            return bonus
    if (anchor_day.year, anchor_day.month) == (day.year, day.month):
        return 10
    return 0


def job_priority(posted_at, anchor, *, own, current):
    return recency_tier(posted_at, anchor) + (2 if own else 0) + (1 if current else 0)


def _comment_candidates(
    engine,
    *,
    codes=None,
    limit=None,
    since=None,
    until=None,
    authors=None,
    page_size=1000,
    include_body_targets=False,
    include_empty=False,
    parent_filter_config=None,
    comment_routes=False,
):
    """Read comment/product candidates with a half-open ``until`` bound.

    ``comment_routes=True`` is the active production path and emits one row per
    persisted content-tag route. The default, parent-filter and
    ``include_body_targets`` paths remain only for migration/audit compatibility.
    """
    parent = comments.alias("parent")
    grandparent = comments.alias("grandparent")
    great_grandparent = comments.alias("great_grandparent")
    source = (
        comments
        .join(feeds, feeds.c.feed_id == comments.c.feed_id)
        # 父评论是可选的，外连接 —— 内连接会把所有非回复的评论排除在队列外。
        .outerjoin(parent, parent.c.comment_id == comments.c.reply_to_comment_id)
        .outerjoin(grandparent, grandparent.c.comment_id == parent.c.reply_to_comment_id)
        .outerjoin(
            great_grandparent,
            great_grandparent.c.comment_id == grandparent.c.reply_to_comment_id,
        )
    )
    code_column = feeds.c.code
    extra_columns = []
    route_ordered = False
    if comment_routes:
        source = source.join(
            comment_product_routes,
            and_(
                comment_product_routes.c.comment_id == comments.c.comment_id,
                comment_product_routes.c.rule_version == COMMENT_ROUTE_VERSION,
            ),
        )
        code_column = comment_product_routes.c.subject_code
        extra_columns = [feeds.c.code.label("anchor_code")]
        route_ordered = True
    elif include_body_targets:
        targets = union(
            select(
                feeds.c.feed_id.label("feed_id"),
                feeds.c.code.label("target_code"),
            ),
            select(
                mentions.c.feed_id.label("feed_id"),
                mentions.c.code.label("target_code"),
            ).where(
                mentions.c.source == "body",
                mentions.c.in_pool.is_(True),
            ),
        ).subquery("comment_product_targets")
        source = source.join(targets, targets.c.feed_id == feeds.c.feed_id)
        code_column = targets.c.target_code
        extra_columns = [feeds.c.code.label("anchor_code")]
    q = (
        select(
            comments.c.comment_id,
            comments.c.content,
            comments.c.author_uid,
            comments.c.feed_id,
            code_column.label("code"),
            *extra_columns,
            feeds.c.posted_at,
            feeds.c.title,
            feeds.c.content.label("post_content"),
            parent.c.content.label("parent_content"),
            grandparent.c.content.label("grandparent_content"),
            great_grandparent.c.content.label("great_grandparent_content"),
        )
        .select_from(source)
    )
    # The legacy direct-enqueue path cannot classify empty text and therefore
    # keeps its historical guard.  Exact extraction opts in so every stored
    # platform comment reaches the deterministic audit chain, where ``empty``
    # becomes an explicit rule exclusion instead of an eternal funnel pending.
    if not include_empty:
        q = q.where(comments.c.content.isnot(None), comments.c.content != "")
    if codes:
        q = q.where(code_column.in_(list(codes)))
    if parent_filter_config is not None and not comment_routes:
        if include_body_targets:
            raise ValueError("parent-feed filtering cannot use legacy body targets")
        q = q.where(
            qualifying_feed_scope_predicate(
                feeds,
                parent_filter_config,
                codes=codes,
            )
        )
    if authors:
        q = q.where(comments.c.author_name.in_(list(authors)))
    if since:
        q = q.where(feeds.c.posted_at >= since)
    if until:
        q = q.where(feeds.c.posted_at < until)
    # 稳定顺序：同样的参数每次取到同一批，重跑可复现。Exact 模式下一条评论
    # 可能对应多个 body 产品，游标必须包含 code，否则页边界会漏掉同 comment_id
    # 的第二个产品。
    q = q.order_by(comments.c.comment_id, code_column) if (include_body_targets or route_ordered) else q.order_by(
        comments.c.comment_id
    )
    if page_size < 1:
        raise ValueError("page_size must be positive")
    cursor, remaining = None, limit
    while remaining is None or remaining > 0:
        if cursor is None:
            page = q
        elif include_body_targets or route_ordered:
            page = q.where(
                or_(
                    comments.c.comment_id > cursor[0],
                    and_(
                        comments.c.comment_id == cursor[0],
                        code_column > cursor[1],
                    ),
                )
            )
        else:
            page = q.where(comments.c.comment_id > cursor)
        count = page_size if remaining is None else min(page_size, remaining)
        with engine.connect() as conn:
            records = conn.execute(page.limit(count)).all()
        if not records:
            break
        yield from records
        cursor = (
            (records[-1].comment_id, records[-1].code)
            if include_body_targets or route_ordered
            else records[-1].comment_id
        )
        if remaining is not None:
            remaining -= len(records)
        if len(records) < count:
            break


def job_row_for_comment(cfg, prompt, schema_version, row, *, priority=0, scope_id=None, now=None,
                        task="comment_product"):
    """一行候选 → 一条待办。指纹用的是**将来真正会发出去的那个 payload**。"""
    now = now or clock.now()
    parents = [
        value for value in (
            getattr(row, "parent_content", None),
            getattr(row, "grandparent_content", None),
            getattr(row, "great_grandparent_content", None),
        ) if value
    ]
    payload = _build_payload(
        task,
        {"target_id": row.comment_id, "subject_code": row.code},
        {"text": row.content, "title": row.title,
         "parent": parents[0] if parents else None, "parents": parents,
         "post_content": getattr(row, "post_content", None)},
    )
    # v3 is released against direct LLM gold labels.  Letting the older local
    # student finish these jobs would bypass that quality gate and make the
    # public v3 funnel disagree with the annotations it displays.
    stage = (
        STAGE_LLM
        if task == "comment_product" and getattr(prompt, "REQUIRES_FULL_REANNOTATION", False)
        else default_stage(task)
    )
    return {
        "target_type": "comment",
        "target_id": row.comment_id,
        "subject_code": row.code,
        "task": task,
        "input_hash": schemas.input_hash(
            payload,
            model=cfg.model,
            prompt_version=prompt_version(prompt, cfg),
            taxonomy_version=cfg.taxonomy_version,
            schema_version=schema_version,
        ),
        "status": "pending",
        "priority": priority,
        "attempts": 0,
        "scope_id": scope_id,
        "stage": stage,
        "created_at": now,
        "updated_at": now,
    }


def enqueue_comments(engine, cfg, *, codes=None, limit=None, since=None, until=None,
                     priority=0, scope_id=None, fill_missing=None):
    """把「评论 × 产品」组合排进待办。

    判定单元是 `(comment_id, subject_code)`（§10.1），所以同一条评论评价两只 ETF
    会排成**两条**待办 —— 它们可以得出相反的态度，这正是单表方案做不到的事。

    路由表激活后，产品来自父帖标题／正文与该评论正文中的严格 cashtag 并集；迁移前数据库
    才保留挂载标的兼容行为。

    **不做规则预过滤。** 按 ETF × 时间段抽取并过滤的生产入口是 `jobs/extract.py`；
    这里只保留为测试／迁移兼容函数，CLI 禁止用它创建 `comment_product` 任务。
    """
    prompt, schema_version = resolve("comment_product", cfg)
    now = clock.now()
    with engine.connect() as conn:
        route_active = comment_routes_ready_on_connection(conn)
    rows = [
        job_row_for_comment(cfg, prompt, schema_version, r, priority=priority,
                            scope_id=scope_id, now=now)
        for r in _comment_candidates(
            engine,
            codes=codes,
            limit=limit,
            since=since,
            until=until,
            comment_routes=route_active,
        )
    ]
    return _insert_jobs(engine, _prepare_fill_missing(
        engine, rows, "comment_product", schema_version, cfg, fill_missing
    ))


def enqueue_posts(engine, cfg, *, codes=None, limit=None, since=None, until=None,
                  authors=None, priority=0, scope_id=None, priority_of=None,
                  fill_missing=None):
    """把帖子排进待办（§11.2：类型／操作方向／摘要）。

    与评论任务有三处结构性不同，不是参数差异：

    **判定单元只有帖子本身，没有 subject。** 帖子类型是「这篇文章是什么」，
    不是「这篇文章对哪只 ETF 什么态度」—— 一篇同时挂着三只标的的行情解读，
    它仍然只是一篇行情解读。所以 `subject_code` 写 `NO_SUBJECT` 而不是逐标的排三条。

    **正文可能为空，标题不能。** 富途社区有大量只有标题的帖子。所以过滤条件落在
    「标题与正文至少有一个非空」上，而不是像评论那样只看正文。

    **指纹覆盖标题＋正文＋挂载产品。** `_build_payload` 发出去的就是这些。

    `authors`：只排这些作者名的帖子（KOL 32 位＋官号 20 个）。全量 38 万篇帖子里页面只用
    得上这两类作者的，其余不排 —— 那是一笔没有消费方的开销。
    """
    task = "post_annotation"
    prompt, schema_version = resolve(task, cfg)
    now = clock.now()
    rows = []

    with engine.connect() as conn:
        q = select(feeds.c.feed_id, feeds.c.title, feeds.c.content, feeds.c.code, feeds.c.posted_at).where(
            or_(
                and_(feeds.c.content.isnot(None), feeds.c.content != ""),
                and_(feeds.c.title.isnot(None), feeds.c.title != ""),
            )
        )
        if codes:
            q = q.where(feeds.c.code.in_(list(codes)))
        if since:
            q = q.where(feeds.c.posted_at >= since)
        if until:
            q = q.where(feeds.c.posted_at < until)
        if authors:
            q = q.where(feeds.c.author_name.in_(list(authors)))
        q = q.order_by(feeds.c.feed_id)
        if limit:
            q = q.limit(limit)

        for feed_id, title, content, code, posted_at in conn.execute(q):
            job = {"target_id": feed_id, "subject_code": None}
            payload = _build_payload(task, job, {"text": content, "title": title, "code": code})
            rows.append(
                {
                    "target_type": "feed",
                    "target_id": feed_id,
                    "subject_code": NO_SUBJECT,
                    "task": task,
                    "input_hash": schemas.input_hash(
                        payload,
                        model=cfg.model,
                        prompt_version=prompt_version(prompt, cfg),
                        taxonomy_version=cfg.taxonomy_version,
                        schema_version=schema_version,
                    ),
                    "status": "pending",
                    "priority": priority_of(posted_at, code) if priority_of else priority,
                    "attempts": 0,
                    "scope_id": scope_id,
                    "stage": default_stage(task),
                    "created_at": now,
                    "updated_at": now,
                }
            )
    return _insert_jobs(engine, _prepare_fill_missing(
        engine, rows, task, schema_version, cfg, fill_missing
    ))


def enqueue_kol_comments(engine, cfg, kol_names, *, codes=None, limit=None, since=None, until=None,
                         priority=0, scope_id=None, priority_of=None,
                         fill_missing=None, filter_config=None):
    """把合作 KOL 写的评论排进 `kol_comment_opinion`（PRD §4.4 M7）。

    判定单元、payload、指纹都与 comment_product 同构 —— 只是作者被限定在 KOL 名单内，
    任务名不同。32 位 KOL 的评论量很小，这是全套里最便宜的一个任务。
    """
    task = "kol_comment_opinion"
    filter_config = filter_config or load_comment_filter_config()
    prompt, schema_version = resolve(task, cfg)
    now = clock.now()
    rows = []
    with engine.connect() as conn:
        comment_routes = comment_routes_ready_on_connection(conn)
    for r in _comment_candidates(engine, codes=codes, limit=None, since=since, until=until,
                                 authors=list(kol_names), comment_routes=comment_routes,
                                 parent_filter_config=None if comment_routes else filter_config):
        rows.append(job_row_for_comment(cfg, prompt, schema_version, r,
                        priority=priority_of(r.posted_at, r.code) if priority_of else priority,
                                        scope_id=scope_id, now=now, task=task))
        if limit and len(rows) >= limit:
            break
    return _insert_jobs(engine, _prepare_fill_missing(
        engine, rows, task, schema_version, cfg, fill_missing
    ))


def _prepare_fill_missing(engine, rows, task, schema_version, cfg, requested):
    """Keep only incomplete annotation units and persist the no-overwrite mode.

    The flag is stored in ``annotation_jobs.stage`` because enqueue and run are
    deliberately separate commands.  The write path checks it again inside the
    transaction, so a concurrent worker cannot turn a gap-fill into a replace.
    """
    enabled = getattr(cfg, "fill_missing_only", False) if requested is None else bool(requested)
    # v3 修的是“已有结论可能完整但语义错误”，不是缺字段；如果仍按补洞模式运行，
    # 历史误判会全部被跳过。策略跟 Prompt 版本放在注册表中，方便以后发布同类修订。
    if requires_full_reannotation(
        task,
        getattr(cfg, "prompt_version", None),
        schema_version=schema_version,
    ):
        enabled = False
    if not enabled or not rows:
        return rows

    keys = {
        (row["target_type"], row["target_id"], row["subject_code"] or NO_SUBJECT)
        for row in rows
    }
    existing = {key: {} for key in keys}
    key_list = list(keys)
    # SQLite 的 bind 参数有上限；全量补洞可能包含几十万个判定单元，分块查询。
    with engine.connect() as conn:
        for offset in range(0, len(key_list), 200):
            found = conn.execute(
                select(
                    annotations.c.target_type,
                    annotations.c.target_id,
                    annotations.c.subject_code,
                    annotations.c.kind,
                    annotations.c.value_json,
                    annotations.c.annotation_id,
                )
                .where(tuple_(
                    annotations.c.target_type,
                    annotations.c.target_id,
                    annotations.c.subject_code,
                ).in_(key_list[offset:offset + 200]))
                .order_by(annotations.c.annotation_id)
            ).all()
            for target_type, target_id, subject_code, kind, value_json, _annotation_id in found:
                existing[(target_type, target_id, subject_code)][kind] = value_json

    selected = []
    for row in rows:
        key = (row["target_type"], row["target_id"], row["subject_code"] or NO_SUBJECT)
        values = existing.get(key, {})
        required = _required_kinds(task, schema_version, values)
        if required.issubset(values):
            continue
        row = dict(row)
        row["stage"] = FILL_MISSING_STAGE
        selected.append(row)
    return selected


def _required_kinds(task, schema_version, values):
    if task == "post_annotation":
        return {"post_type", "summary", "direction"}
    if task == "kol_comment_opinion":
        required = {"kol_summary", "kol_action"}
        if schema_version != "v1":
            required.add("post_type")
        return required
    required = {"relevance"}
    if schema_version != "v1":
        required.add("compliance")
    try:
        relevance = json.loads(values.get("relevance", "null"))
    except (TypeError, ValueError):
        relevance = None
    if relevance == "relevant":
        required.add("attitude")
    return required


def _insert_jobs(engine, rows):
    from radar_db.schema import analysis_scope_jobs

    keys = ("target_type", "target_id", "subject_code", "task", "input_hash")
    added = 0
    for offset in range(0, len(rows), 100):
        chunk = rows[offset:offset + 100]
        unique = {tuple(row[key] for key in keys): row for row in chunk}
        with engine.begin() as conn:
            query = select(annotation_jobs).where(tuple_(*[annotation_jobs.c[key] for key in keys]).in_(list(unique)))
            existing_rows = {
                tuple(row[key] for key in keys): row
                for row in conn.execute(query).mappings()
            }
            existing = {key: row["job_id"] for key, row in existing_rows.items()}
            # Exact eligibility can legitimately flip after a parent edit.  If
            # the identical source payload becomes eligible again, the unique
            # job row already exists in terminal ``superseded`` state; revive
            # it instead of silently leaving the unit unprocessable.
            for key, row in unique.items():
                previous = existing_rows.get(key)
                if previous is None or previous["status"] != "superseded":
                    continue
                conn.execute(
                    update(annotation_jobs)
                    .where(annotation_jobs.c.job_id == previous["job_id"])
                    .values(
                        status="pending",
                        priority=row["priority"],
                        attempts=0,
                        claimed_at=None,
                        lease_until=None,
                        last_error=None,
                        scope_id=row.get("scope_id"),
                        stage=row["stage"],
                        updated_at=row["updated_at"],
                    )
                )
                added += 1
            new = [row for key, row in unique.items() if key not in existing]
            if new:
                try:
                    with conn.begin_nested():
                        conn.execute(insert(annotation_jobs), new)
                    added += len(new)
                except IntegrityError:
                    for row in new:
                        try:
                            with conn.begin_nested():
                                conn.execute(insert(annotation_jobs).values(**row))
                            added += 1
                        except IntegrityError:
                            found = conn.execute(select(annotation_jobs.c.job_id).where(*[
                                annotation_jobs.c[key] == row[key] for key in keys])).first()
                            if found is None:
                                raise
                existing = {tuple(row[key] for key in keys): row["job_id"]
                            for row in conn.execute(query).mappings()}
            links = {(row["scope_id"], existing[tuple(row[key] for key in keys)])
                     for row in chunk if row.get("scope_id")}
            if links:
                present = set(conn.execute(select(analysis_scope_jobs.c.scope_id, analysis_scope_jobs.c.job_id)
                    .where(tuple_(analysis_scope_jobs.c.scope_id, analysis_scope_jobs.c.job_id).in_(list(links)))).all())
                missing = [{"scope_id": scope, "job_id": job_id} for scope, job_id in links - present]
                if missing:
                    try:
                        with conn.begin_nested():
                            conn.execute(insert(analysis_scope_jobs), missing)
                    except IntegrityError:
                        for link in missing:
                            try:
                                with conn.begin_nested():
                                    conn.execute(insert(analysis_scope_jobs).values(**link))
                            except IntegrityError:
                                found = conn.execute(select(analysis_scope_jobs.c.job_id).where(
                                    analysis_scope_jobs.c.scope_id == link["scope_id"],
                                    analysis_scope_jobs.c.job_id == link["job_id"])).first()
                                if found is None:
                                    raise
    return added


# ── 领取 ───────────────────────────────────────────────────────────────


def claim(
    engine,
    task,
    n,
    *,
    now=None,
    scope_id=None,
    grouped=False,
    stage=None,
    parent_filter_config=None,
    comment_routes=None,
):
    """领取至多 n 条待办，打上租约。

    可领取 = `pending`，或 `claimed` 但租约已过期。后者是 worker 崩溃后的回收路径 ——
    没有它，进程被 Ctrl-C 掉的那一刻正在处理的 30 条会永远卡在 claimed。

    `scope_id` 给了就只领这个抽取范围的任务（ADR-0020）：跑「3033 近 7 天」时，
    队列里别的产品、别的日期的待办一条都不该被带走。

    `stage` 给了就只领这一段的任务（ADR-0021）；不给则保持旧调用方的不分段行为。
    """
    parent_filter_config = comment_task_filter_config(task, parent_filter_config)
    now = now or clock.now()
    lease_until = now + timedelta(minutes=LEASE_MINUTES)
    claimed = []
    with _CLAIM_LOCK, engine.begin() as conn:
        route_active = (
            comment_routes_ready_on_connection(conn, lock=True)
            if task in COMMENT_TASKS and comment_routes is not False
            else False
        )
        if comment_routes is True and not route_active:
            raise RuntimeError("comment AI routes are not ready")
        if parent_filter_config is not None and not route_active:
            require_filter_ready_on_connection(
                conn,
                parent_filter_config,
                lock=True,
            )
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
        )
        filtered_source = None
        if route_active:
            filtered_source = (
                annotation_jobs
                .join(comments, comments.c.comment_id == annotation_jobs.c.target_id)
                .join(feeds, feeds.c.feed_id == comments.c.feed_id)
            )
            q = q.select_from(filtered_source)
            q = q.where(
                annotation_jobs.c.target_type == "comment",
                route_exists_predicate(
                    annotation_jobs.c.target_id,
                    annotation_jobs.c.subject_code,
                ),
            )
        elif parent_filter_config is not None:
            filtered_source = (
                annotation_jobs
                .join(comments, comments.c.comment_id == annotation_jobs.c.target_id)
                .join(feeds, feeds.c.feed_id == comments.c.feed_id)
            )
            q = q.select_from(filtered_source).where(
                annotation_jobs.c.target_type == "comment",
                annotation_jobs.c.subject_code == feeds.c.code,
                qualifying_feed_scope_predicate(feeds, parent_filter_config),
            )
        if stage is not None:
            q = q.where(annotation_jobs.c.stage == stage)
        if scope_id is not None:
            from radar_db.scope_jobs import scope_condition
            q = q.where(scope_condition(scope_id))
        if grouped:
            source = filtered_source if filtered_source is not None else annotation_jobs.outerjoin(
                comments,
                and_(
                    annotation_jobs.c.target_type == "comment",
                    comments.c.comment_id == annotation_jobs.c.target_id,
                ),
            ).outerjoin(feeds, or_(
                and_(annotation_jobs.c.target_type == "comment", feeds.c.feed_id == comments.c.feed_id),
                and_(annotation_jobs.c.target_type == "feed", feeds.c.feed_id == annotation_jobs.c.target_id),
            ))
            q = q.select_from(source)
            first = conn.execute(q.add_columns(feeds.c.posted_at, feeds.c.code.label("feed_code"))
                                 .order_by(annotation_jobs.c.priority.desc(), annotation_jobs.c.job_id)
                                 .limit(1)).mappings().first()
            if first is None:
                return []
            q = q.where(annotation_jobs.c.subject_code == first["subject_code"])
            if not route_active:
                q = q.where(feeds.c.code == first["feed_code"])
            if first["posted_at"] is not None:
                day = utc_naive_to_hkt(first["posted_at"]).date()
                lo, hi = hkt_range_utc_naive(day, day)
                q = q.where(feeds.c.posted_at >= lo, feeds.c.posted_at < hi)
            else:
                q = q.where(annotation_jobs.c.job_id == first["job_id"])
        q = (
            # 同一批尽量属于同一产品（§11.3）：按 subject_code 排，固定上下文能被
            # 供应商的 prompt cache 命中，也让模型少切换产品语境。
            q.order_by(
                annotation_jobs.c.priority.desc(),
                annotation_jobs.c.subject_code,
                annotation_jobs.c.job_id,
            )
            .limit(n)
        )
        # 先把 SELECT 取完再 UPDATE。游标没读完就写，SQLite 会把这个连接当作「持有读快照
        # 的事务要升级成写」—— 若别的线程在快照之后写过，它**立刻**返回 BUSY_SNAPSHOT，
        # 不经过 busy_timeout。单线程时代这个写法侥幸没炸。
        picked = [dict(r) for r in conn.execute(q).mappings().all()]
        for row in picked:
            conn.execute(
                update(annotation_jobs)
                .where(annotation_jobs.c.job_id == row["job_id"])
                .values(status="claimed", claimed_at=now, lease_until=lease_until)
            )
            claimed.append(row)
    return claimed


# ── 跑一批 ─────────────────────────────────────────────────────────────


def _fresh_stats(cfg, run_id, task):
    return {
        "run_id": run_id, "task": task, "input": 0, "success": 0, "error": 0,
        "requests": 0, "tok_in": 0, "tok_out": 0, "tok_reason": 0, "tok_cached": 0,
        "usage_known": True, "model": cfg.model, "aborted": None,
    }


def _local_stats():
    return {"success": 0, "error": 0, "requests": 0, "tok_in": 0, "tok_out": 0,
            "tok_reason": 0, "tok_cached": 0, "usage_known": True, "model": None}


def _merge(stats, local):
    for k in ("success", "error", "requests", "tok_in", "tok_out", "tok_reason", "tok_cached"):
        stats[k] += local[k]
    stats["usage_known"] = stats["usage_known"] and local["usage_known"]
    if local["model"]:
        stats["model"] = local["model"]


def run(engine, cfg=None, *, task="comment_product", max_items=None, provider=None,
        scope_id=None, budget_requests=None, stage=None, parent_filter_config=None,
        comment_routes=None):
    """跑一轮标注。返回 run 统计。

    `budget_requests`：本轮最多**领取**多少批（≈ 请求数，不含重试与二分）。
    价格未知时这是唯一能卡住花费的旋钮 —— 条数×批大小算出来的请求数是可以对着账单核的。

    `stage` 只领取指定漏斗段；不给则兼容直接调用时处理所有阶段。
    """
    cfg = cfg or config.load()
    parent_filter_config = comment_task_filter_config(task, parent_filter_config)
    with engine.connect() as conn:
        route_active = (
            comment_routes_ready_on_connection(conn)
            if task in COMMENT_TASKS and comment_routes is not False
            else False
        )
    if comment_routes is True and not route_active:
        raise RuntimeError("comment AI routes are not ready")
    if parent_filter_config is not None and not route_active:
        require_filter_ready(engine, parent_filter_config)
    provider = provider or build_provider(cfg)
    prompt, schema_version = resolve(task, cfg)

    provider_limit = getattr(provider, "max_batch_size", None)
    batch_size = min(cfg.micro_batch_size, provider_limit) if provider_limit else cfg.micro_batch_size
    budget = max_items if max_items is not None else batch_size
    run_id = new_run_id()
    started = clock.now()
    stats = _fresh_stats(cfg, run_id, task)
    stats["stage"] = stage
    workers = max(1, int(cfg.concurrency or 1))

    with engine.begin() as conn:
        conn.execute(
            insert(annotation_runs).values(
                run_id=run_id, task=task, provider=cfg.provider,
                model_id=cfg.model, model_revision=None,
                prompt_version=prompt_version(prompt, cfg),
                taxonomy_version=cfg.taxonomy_version,
                schema_version=schema_version,
                started_at=started, status="running",
                input_count=0, success_count=0, error_count=0,
            )
        )

    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="annotate")
    stop_event = threading.Event()
    control = getattr(provider, "control", None)
    inflight = {}
    batches_started = 0
    try:
        while True:
            # 补满在途：始终保持 `workers` 批在跑，直到预算或队列耗尽。
            while (
                len(inflight) < workers and budget > 0 and stats["aborted"] is None
                and not stop_event.is_set()
                and not (control is not None and control.stop_event.is_set())
                and (budget_requests is None or batches_started < budget_requests)
            ):
                jobs = claim(engine, task, min(batch_size, budget), scope_id=scope_id,
                             grouped=cfg.grouped_batches, stage=stage,
                             parent_filter_config=parent_filter_config,
                             comment_routes=route_active)
                if not jobs:
                    break
                if stop_event.is_set():
                    for job in jobs:
                        _release(engine, job, stop_event.reason)
                    break
                budget -= len(jobs)
                batches_started += 1
                stats["input"] += len(jobs)
                fut = pool.submit(
                    _process, engine, cfg, provider, prompt, schema_version, task, run_id,
                    jobs, _local_stats(),
                    stop_event=stop_event,
                )
                inflight[fut] = (len(jobs), jobs[0]["subject_code"] or None)
            if not inflight:
                break
            done, _ = wait(list(inflight), return_when=FIRST_COMPLETED)
            for fut in done:
                n_jobs, batch_code = inflight.pop(fut)
                try:
                    local = fut.result()
                    _merge(stats, local)
                    _emit_batch(engine, task, run_id, scope_id, batch_code, n_jobs, local)
                except _Abort as exc:
                    # 别的线程可能同时撞上同一个永久错误；记第一条即可。
                    if stats["aborted"] is None:
                        stats["aborted"] = str(exc)
                        log.error("整轮中止：%s", exc)
                    _merge(stats, exc.local)
            if stats["aborted"] is not None and not inflight:
                break
    finally:
        pool.shutdown(wait=True)
        if control is not None and control.stop_event.is_set():
            stats["aborted"] = control.reason
        _close_run(
            engine,
            run_id,
            stats,
            mark_synthesis_dirty=(
                task == "comment_product"
                and getattr(provider, "supports_generation", True)
            ),
        )
        if stats["aborted"]:
            emit(
                engine,
                _STAGE_OF.get(task, "L2"),
                f"{task} 中止：{stats['aborted'][:160]}",
                level="error",
                scope_id=scope_id,
                run_id=run_id,
            )

    return stats


_STAGE_OF = {"comment_product": "L2", "kol_comment_opinion": "L2", "post_annotation": "L2"}
_TASK_LABEL = {"comment_product": "评论", "kol_comment_opinion": "KOL 评论", "post_annotation": "帖子"}


def _emit_batch(engine, task, run_id, scope_id, code, n_jobs, local):
    """Record one concise event after a Luna batch finishes."""
    label = _TASK_LABEL.get(task, task)
    message = f"{code or '—'} Luna {label}批 {n_jobs} → 写入 {local['success']}"
    if local["error"]:
        message += f" / 失败 {local['error']}"
    if local["requests"] > 1:
        message += f"（{local['requests']} 次请求）"
    emit(
        engine,
        _STAGE_OF.get(task, "L2"),
        message,
        level="warn" if local["error"] else "info",
        code=code,
        scope_id=scope_id,
        run_id=run_id,
        data={
            "task": task,
            "n": n_jobs,
            "success": local["success"],
            "error": local["error"],
            "requests": local["requests"],
        },
    )


def _mark_touched_ranges(conn, run_id, mark_synthesis, ranges_touching):
    """Mark only synthesis windows intersecting comments changed by this run."""
    from datetime import date

    from radar_db.schema import meta_kv

    anchor_s = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "anchor")).scalar()
    anchor = date.fromisoformat(anchor_s[:10]) if anchor_s else None
    spans = conn.execute(
        select(annotations.c.subject_code, func.min(feeds.c.posted_at), func.max(feeds.c.posted_at))
        .select_from(
            annotations
            .join(
                comments,
                and_(annotations.c.target_type == "comment", comments.c.comment_id == annotations.c.target_id),
            )
            .join(feeds, feeds.c.feed_id == comments.c.feed_id)
        )
        .where(annotations.c.run_id == run_id)
        .group_by(annotations.c.subject_code)
    ).all()
    for code, lo, hi in spans:
        if not code:
            continue
        if anchor is None or lo is None or hi is None:
            mark_synthesis(conn, [code], True)
            continue
        touched = ranges_touching(
            anchor,
            utc_naive_to_hkt(lo).date(),
            utc_naive_to_hkt(hi).date(),
        )
        if touched:
            mark_synthesis(conn, [code], True, touched)


def _close_run(engine, run_id, stats, *, mark_synthesis_dirty=True):
    from radar_db.revisions import bump_revision, mark_synthesis, ranges_touching
    with engine.begin() as conn:
        bump_revision(conn, "annotation")
        if mark_synthesis_dirty:
            _mark_touched_ranges(conn, run_id, mark_synthesis, ranges_touching)
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


def _process(engine, cfg, provider, prompt, schema_version, task, run_id, jobs, local, depth=0, stop_event=None):
    """处理一批。失败时按 §11.3 二分，而不是整批判死。返回本批的局部统计。"""
    sources = _load_sources(engine, task, jobs)
    payloads, usable = [], []
    for job in jobs:
        src = sources.get((job["target_type"], job["target_id"]))
        has_text = src is not None and (
            (src.get("text") or "").strip()
            or (task == "post_annotation" and (src.get("title") or "").strip())
        )
        if not has_text:
            # 正文取不到 —— 规则层就该拦下（§6.5「空文本」不调 GPT）。判死不重试。
            _fail(engine, job, "源文本为空或不存在，不应进队列", dead=True)
            local["error"] += 1
            continue
        payload = _build_payload(task, job, src)
        current_hash = schemas.input_hash(
            payload, model=cfg.model, prompt_version=prompt_version(prompt, cfg),
            taxonomy_version=cfg.taxonomy_version, schema_version=schema_version,
        )
        if current_hash != job["input_hash"]:
            with engine.begin() as conn:
                conn.execute(update(annotation_jobs).where(annotation_jobs.c.job_id == job["job_id"])
                             .values(status="superseded", lease_until=None, last_error="Source input changed"))
            local["error"] += 1
            continue
        payloads.append(payload)
        usable.append((job, src))

    if not payloads:
        return local

    if cfg.grouped_batches:
        policy = BatchPolicy(cfg.micro_batch_size, cfg.max_input_tokens, cfg.max_payload_bytes)
        packed, oversized = pack_items(
            list(zip(usable, payloads)), key_of=lambda entry: (
                entry[0][0]["subject_code"], entry[0][1].get("code"),
                utc_naive_to_hkt(entry[0][1].get("posted_at")).date()
                if entry[0][1].get("posted_at") else None),
            payload_of=lambda entry: entry[1], system=prompt.SYSTEM, render=prompt.user_message,
            schema=schemas.batch_json_schema(task, schema_version), policy=policy,
        )
        for (job, _src), _payload in oversized:
            _fail(engine, job, "Input exceeds batch token/byte limit; text was not truncated")
            local["error"] += 1
        if len(packed) != 1 or oversized:
            try:
                for batch in packed:
                    _process(engine, cfg, provider, prompt, schema_version, task, run_id,
                             [entry[0][0] for entry in batch], local, depth, stop_event)
            except _Abort:
                _release_claimed(engine, jobs, "Run stopped before remaining packages")
                raise
            return local

    try:
        if stop_event is not None and stop_event.is_set():
            raise PermanentError(getattr(stop_event, "reason", "cancelled"))
        local["requests"] += 1
        complete_annotations = getattr(provider, "complete_annotations", None)
        if complete_annotations is not None:
            comp = complete_annotations(task, payloads, schema_version)
        else:
            comp = provider.complete_json(
                prompt.SYSTEM,
                prompt.user_message(payloads),
                schemas.batch_json_schema(task, schema_version),
                f"{task}_batch",
            )
    except PermanentError as exc:
        if stop_event is not None:
            stop_event.reason = str(exc)
            stop_event.set()
        # 401/400 这类：重试无意义，而且多半是配置问题，整批放回 pending 等人改配置。
        # **不判死** —— 把 30 条因为一个 Key 打错而判死，改完配置后还得手动复活。
        for job, _ in usable:
            _release(engine, job, f"永久错误：{exc}")
        local["error"] += len(usable)
        abort = _Abort(f"永久错误，{len(usable)} 条已放回待办：{exc}")
        abort.local = local
        raise abort from exc
    except TransientError as exc:
        local["usage_known"] = False
        if isinstance(exc, TruncatedOutput) and cfg.grouped_batches and len(usable) > 1 and depth < 6:
            mid = len(usable) // 2
            try:
                for portion in (usable[:mid], usable[mid:]):
                    _process(engine, cfg, provider, prompt, schema_version, task, run_id,
                             [job for job, _src in portion], local, depth + 1, stop_event)
            except _Abort:
                _release_claimed(engine, jobs, "Run stopped during truncated output split")
                raise
            return local
        # 供应商层已经退避重试过 max_retries 次了，到这里说明确实不通。
        _retry_or_dead(engine, cfg, usable, local, f"传输失败：{exc}")
        return local

    _record_usage(comp, local)
    local["model"] = comp.model
    control = getattr(provider, "control", None)
    if control is not None:
        control.record_batch([payload["item_id"] for payload in payloads], comp.response_id)
        if control.before_request is not None:
            control.before_request()
        if control.reason in ("source_changed", "lease_lost"):
            _release_claimed(engine, jobs, control.reason)
            abort = _Abort(control.reason)
            abort.local = local
            raise abort

    try:
        invalid = {}
        if cfg.grouped_batches:
            by_id, invalid = schemas.parse_batch_partial(
                task, comp.data, [payload["item_id"] for payload in payloads], schema_version)
        else:
            by_id = schemas.parse_batch(task, comp.data, [p["item_id"] for p in payloads],
                                        schema_version)
    except schemas.SchemaError as exc:
        # §11.3：JSON/Schema 失败先重试；连续失败后将批次二分。
        if len(usable) > 1 and depth < 6:
            mid = len(usable) // 2
            log.warning("批输出不合格（%s），二分为 %d + %d", exc, mid, len(usable) - mid)
            try:
                _process(engine, cfg, provider, prompt, schema_version, task, run_id,
                         [j for j, _ in usable[:mid]], local, depth + 1, stop_event)
                _process(engine, cfg, provider, prompt, schema_version, task, run_id,
                         [j for j, _ in usable[mid:]], local, depth + 1, stop_event)
            except _Abort:
                _release_claimed(engine, jobs, "Run stopped during schema split")
                raise
        else:
            _retry_or_dead(engine, cfg, usable, local, f"schema 失败：{exc}")
        return local

    latest_sources = _load_sources(engine, task, [job for job, _ in usable])
    to_write = []
    for job, src in usable:
        if _item_id(task, job) in invalid:
            continue
        item = by_id[_item_id(task, job)]
        latest = latest_sources.get((job["target_type"], job["target_id"]))
        if latest != src:
            with engine.begin() as conn:
                conn.execute(update(annotation_jobs).where(annotation_jobs.c.job_id == job["job_id"])
                             .values(status="superseded", lease_until=None, last_error="Source changed during inference"))
            local["error"] += 1
            continue
        to_write.append((job, src, item))
    ok, bad = _write_batch(engine, task, to_write, run_id, schema_version)
    local["success"] += ok
    local["error"] += bad
    if invalid:
        rejected = [(job, src) for job, src in usable if _item_id(task, job) in invalid]
        if by_id and depth < 6:
            _process(engine, cfg, provider, prompt, schema_version, task, run_id,
                     [job for job, _src in rejected], local, depth + 1, stop_event)
        elif len(rejected) > 1 and depth < 6:
            mid = len(rejected) // 2
            try:
                for portion in (rejected[:mid], rejected[mid:]):
                    _process(engine, cfg, provider, prompt, schema_version, task, run_id,
                             [job for job, _src in portion], local, depth + 1, stop_event)
            except _Abort:
                _release_claimed(engine, jobs, "Run stopped during split")
                raise
        else:
            _retry_or_dead(engine, cfg, rejected, local, "Invalid or missing batch items")
    return local


def _write_batch(engine, task, items, run_id, schema_version="v1"):
    """Write a batch atomically, then isolate individual bad rows on failure."""
    if not items:
        return 0, 0
    try:
        written = 0
        with engine.begin() as conn:
            for job, src, item in items:
                # Exact extraction can supersede an old claimed job while its
                # provider request is in flight.  Re-check inside the write
                # transaction so that stale model output cannot reverse the
                # newer deterministic exclusion.
                if job.get("job_id") is not None:
                    status = conn.execute(
                        select(annotation_jobs.c.status).where(
                            annotation_jobs.c.job_id == job["job_id"]
                        )
                    ).scalar()
                    if status == "superseded":
                        continue
                _write(engine, task, job, src, item, run_id, schema_version, conn)
                written += 1
        return written, 0
    except Exception as exc:  # noqa: BLE001
        if len(items) == 1:
            job = items[0][0]
            log.exception("写库失败 job=%s", job["job_id"])
            _fail(engine, job, f"写库失败：{exc}")
            return 0, 1
        log.warning("整批写库失败（%s），退化为逐条写", str(exc)[:120])
        ok = bad = 0
        for item in items:
            item_ok, item_bad = _write_batch(engine, task, [item], run_id, schema_version)
            ok += item_ok
            bad += item_bad
        return ok, bad


def _record_usage(comp, stats):
    u = comp.usage
    if u.input_tokens is None or u.output_tokens is None:
        stats["usage_known"] = False
        return
    stats["tok_in"] += u.input_tokens
    stats["tok_out"] += u.output_tokens
    stats["tok_reason"] += u.reasoning_tokens or 0
    stats["tok_cached"] += u.cached_tokens or 0


# ── 取源文本 ───────────────────────────────────────────────────────────


def _load_sources(engine, task, jobs):
    """一次查回整批的源文本与上下文。逐条查会让 30 条变成 30 次往返。

    评论带三样上下文，都是 §11.4 明确许可外发的：**父评论**、**帖子标题**、**帖子正文开头**。
    前两样 Gate 2 就有了 —— 首轮 100 条影子运行里 40% 判成 `needs_context`，样本是
    「有」「劲」「是股息」这种一两个字的回复，缺的不是模型能力，是我们没把它该看的东西发过去。
    第三样是这次补的（ADR-0020）：只带开头 `POST_CONTEXT_CHARS` 字，且 Prompt 里交代了
    它只用来理解语境，不代表评论者的观点。
    """
    ids = [j["target_id"] for j in jobs]
    out = {}
    if not ids:
        return out
    with engine.connect() as conn:
        if task in COMMENT_TASKS:
            parent = comments.alias("parent")
            grandparent = comments.alias("grandparent")
            great_grandparent = comments.alias("great_grandparent")
            q = (
                select(
                    comments.c.comment_id,
                    comments.c.content,
                    feeds.c.title,
                    feeds.c.content.label("post_content"),
                    parent.c.content.label("parent_content"),
                    grandparent.c.content.label("grandparent_content"),
                    great_grandparent.c.content.label("great_grandparent_content"),
                    feeds.c.posted_at,
                    feeds.c.code,
                )
                .select_from(
                    comments
                    # 外连接：取不到帖子或父评论时，这条评论**仍然要出现**在结果里。
                    .outerjoin(feeds, feeds.c.feed_id == comments.c.feed_id)
                    .outerjoin(parent, parent.c.comment_id == comments.c.reply_to_comment_id)
                    .outerjoin(grandparent, grandparent.c.comment_id == parent.c.reply_to_comment_id)
                    .outerjoin(
                        great_grandparent,
                        great_grandparent.c.comment_id == grandparent.c.reply_to_comment_id,
                    )
                )
                .where(comments.c.comment_id.in_(ids))
            )
            for (cid, content, title, post_content, parent_content, grandparent_content,
                 great_grandparent_content, posted_at, code) in conn.execute(q):
                parents = [
                    value for value in (
                        parent_content,
                        grandparent_content,
                        great_grandparent_content,
                    ) if value
                ]
                out[("comment", cid)] = {
                    "text": content,
                    "title": title,
                    "parent": parents[0] if parents else None,
                    "parents": parents,
                    "post_content": post_content,
                    "posted_at": posted_at, "code": code,
                }
        else:
            q = select(feeds.c.feed_id, feeds.c.title, feeds.c.content, feeds.c.code, feeds.c.posted_at).where(
                feeds.c.feed_id.in_(ids)
            )
            for fid, title, content, code, posted_at in conn.execute(q):
                out[("feed", fid)] = {"text": content, "title": title, "code": code, "posted_at": posted_at}
    return out


# 以评论 × 产品为判定单元的任务；帖子任务是另一种形状。
COMMENT_TASKS = ("comment_product", "kol_comment_opinion")


def comment_task_filter_config(task, parent_filter_config=None):
    """Resolve the mandatory parent-filter generation for a queue task.

    Non-comment tasks deliberately return ``None``.  Every comment-shaped task
    loads the versioned repository config when a caller omits it and refuses to
    run a config whose semantics do not match this worker's exact rule.
    """

    if task not in COMMENT_TASKS:
        return None
    selected = parent_filter_config or load_comment_filter_config()
    if selected.version != EXACT_RULE_VERSION:
        raise RuntimeError(
            f"worker exact rule version {EXACT_RULE_VERSION!r} does not match "
            f"comment filter config {selected.version!r}"
        )
    return selected


def require_comment_task_filter_on_connection(conn, task, parent_filter_config=None):
    """Resolve and lock the active filter generation in ``conn``'s transaction."""

    selected = comment_task_filter_config(task, parent_filter_config)
    if selected is not None:
        require_filter_ready_on_connection(conn, selected, lock=True)
    return selected


def _item_id(task, job):
    if task in COMMENT_TASKS:
        return f"comment:{job['target_id']}|product:{job['subject_code']}"
    return f"feed:{job['target_id']}"


def _product_block(code):
    """发给模型的产品块：代码＋名称＋别名（§11.4 白名单三键）。不在词表里的代码只给代码。"""
    if not code:
        return None
    lex = _lexicon()
    if code in lex.by_code:
        return lex.product_block(code)
    return {"code": code}


def _build_payload(task, job, src):
    if task in COMMENT_TASKS:
        post_context = (src.get("post_content") or "").strip()[:POST_CONTEXT_CHARS] or None
        return redact.comment_payload(
            _item_id(task, job),
            _product_block(job["subject_code"]),
            src["text"],
            post_title=src.get("title"),
            parent_comment=src.get("parent"),
            parent_comments=src.get("parents"),
            post_context=post_context,
        )
    return redact.post_payload(
        _item_id(task, job), src["text"], title=src.get("title"),
        product=_product_block(src.get("code")),
    )


# ── 写结论 ─────────────────────────────────────────────────────────────


def _kinds_for(task, item, src, schema_version):
    """一条模型输出 → `[(kind, value, spans, expect_evidence)]`。

    每个 kind 一行（`relevance` / `attitude` / `aspect` 在 §9 里是不同的原子任务），
    各带自己的证据片段：合规命中的引文挂在 `compliance` 行上，态度的引文挂在 `relevance` 行上。
    """
    if task == "post_annotation":
        spans = list(item.evidence_spans or [])
        kinds = [("post_type", item.post_type, spans, True)]
        # `summary=null` 与 `direction=null` 都是模型的**结论**，不是它没回答：schema 里
        # 两个键都必填（`extra="forbid"` ＋ 无默认值），模型必须显式写 null，而 Prompt 给
        # 了它们各自的含义 ——「帖子没有可读正文（纯图片、纯链接）」「帖子没有表达任何操作」。
        #
        # 所以这两行不能因为值是 null 就不写。不写的后果不是少一行数据，是**两件事在库里
        # 变成同一个样子**：「已标注、确实没得摘」和「这帖压根没标注过」都表现为查不到行。
        # 占位用 `false` 而不是某个字符串：它和摘要／方向枚举**类型不同**。
        kinds.append(("summary", item.summary if item.summary is not None else False, [], False))
        kinds.append(
            ("direction",
             "pending" if item.direction_pending
             else (item.direction if item.direction is not None else False),
             [], False)
        )
        return kinds

    if task == "kol_comment_opinion":
        spans = [item.evidence] if item.evidence else []
        # `summary=null`＝「这条评论没有对该产品表达观点」，是结论，落 false 占位（同帖子 summary）。
        kinds = [
            ("kol_summary", item.summary if item.summary is not None else False, spans, item.summary is not None),
            ("kol_action", item.action, [], False),
        ]
        if schema_version != "v1":
            kinds.append(("post_type", item.post_type, [], False))
        return kinds

    spans = [item.evidence] if item.evidence else []
    # 相关但没给出可定位证据 ⇒ 存疑。无关/需上下文本来就没有证据可给，不算问题。
    expect = item.relevance == "relevant"
    kinds = [("relevance", item.relevance, spans, expect), ("attitude", item.attitude, [], False)]
    if item.aspects:
        kinds.append(("aspect", item.aspects, [], False))
    if schema_version != "v1":
        if item.market_direction is not None:
            kinds.append(("market_direction", item.market_direction, [], False))
        # 空数组也写：「查过了没有」和「没查过」在库里必须分得开（runbook §20.4）。
        kinds.append((
            "compliance",
            {"tags": list(item.compliance_tags), "rationale": item.compliance_rationale},
            [item.compliance_evidence] if item.compliance_evidence else [],
            bool(item.compliance_tags),
        ))
    return kinds


def _write(engine, task, job, src, item, run_id, schema_version="v1", conn=None):
    """把一条标注写进库，连同通过校验的证据；可复用调用方事务。"""
    from contextlib import nullcontext

    now = clock.now()
    source_text = src["text"] or ""
    if task == "post_annotation":
        source_text = "\n".join(filter(None, [src.get("title"), src.get("text")]))

    kinds = _kinds_for(task, item, src, schema_version)

    # 先定位全部证据，再决定 review_state：任何一处证据编造 ⇒ 整条存疑。
    # 模型给了证据但一条都定位不到 ⇒ 证据是编的（Gate 0 复现过）。结论仍落库，
    # 但打 needs_review 交人工 —— 判断可能是对的，编造的只是引文。
    located = {}
    needs_review = bool(item.needs_review)
    for kind, _value, spans, expect in kinds:
        found = [(s, ev.locate(s, source_text)) for s in spans]
        verified = [(s, loc) for s, loc in found if loc.found]
        located[kind] = verified
        if (spans and not verified) or (expect and not spans):
            needs_review = True

    with (nullcontext(conn) if conn is not None else engine.begin()) as conn:
        written_rows = []
        for kind, value, _spans, _expect in kinds:
            if value is None:
                continue
            if job.get("stage") == FILL_MISSING_STAGE:
                already_present = conn.execute(
                    select(annotations.c.annotation_id).where(
                        annotations.c.target_type == job["target_type"],
                        annotations.c.target_id == job["target_id"],
                        annotations.c.subject_code == (job["subject_code"] or NO_SUBJECT),
                        annotations.c.kind == kind,
                    ).limit(1)
                ).first()
                if already_present is not None:
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
            ann_id = res.inserted_primary_key[0]
            written_rows.append((kind, json.dumps(value, ensure_ascii=False), None,
                                 "needs_review" if needs_review else "pending", ann_id))
            for _quote, loc in located.get(kind, []):
                conn.execute(
                    insert(annotation_evidence).values(
                        annotation_id=ann_id,
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
        if task == "comment_product":
            run = conn.execute(select(annotation_runs.c.taxonomy_version)
                               .where(annotation_runs.c.run_id == run_id)).first()
            neardup.propagate(conn, job["target_id"], job["subject_code"], written_rows, run_id, now,
                             taxonomy_version=run.taxonomy_version if run else "",
                             schema_version=schema_version)

        conn.execute(
            update(annotation_jobs)
            .where(annotation_jobs.c.job_id == job["job_id"])
            .values(status="done", updated_at=now, last_error=None, lease_until=None)
        )


# ── 任务状态流转 ───────────────────────────────────────────────────────


def _done(engine, job, conn=None):
    from contextlib import nullcontext

    with (nullcontext(conn) if conn is not None else engine.begin()) as conn:
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


def _release_claimed(engine, jobs, reason):
    with engine.begin() as conn:
        conn.execute(update(annotation_jobs).where(
            annotation_jobs.c.job_id.in_([job["job_id"] for job in jobs]),
            annotation_jobs.c.status == "claimed",
        ).values(status="pending", lease_until=None, last_error=reason))


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


def pending_count(engine, task, scope_id=None, stage=None):
    with engine.connect() as conn:
        q = (
            select(func.count())
            .select_from(annotation_jobs)
            .where(annotation_jobs.c.task == task,
                   annotation_jobs.c.status.in_(("pending", "claimed")))
        )
        if stage is not None:
            q = q.where(annotation_jobs.c.stage == stage)
        if scope_id is not None:
            from radar_db.scope_jobs import scope_condition
            q = q.where(scope_condition(scope_id))
        return conn.execute(q).scalar_one()


def reprioritize(engine, *, anchor=None, ownership=None, dry_run=False):
    """Recompute priorities for live jobs using the current anchor and scope windows."""
    from datetime import date

    from radar_db.schema import analysis_scopes, meta_kv

    with engine.connect() as conn:
        if anchor is None:
            anchor_s = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "anchor")).scalar()
            anchor = date.fromisoformat(anchor_s[:10]) if anchor_s else None
        elif isinstance(anchor, datetime):
            anchor = anchor.date()
        scopes = {row.scope_id: row for row in conn.execute(select(analysis_scopes))}
    if ownership is None:
        from jobs.import_dump import pool_codes

        ownership = pool_codes()

    def as_date(value):
        return value.date() if isinstance(value, datetime) else value

    def current_from(scope_id):
        scope = scopes.get(scope_id)
        if scope is None or not scope.with_baseline:
            return None
        date_from = as_date(scope.date_from)
        date_to = as_date(scope.date_to)
        total_days = (date_to - date_from).days + 1
        return date_from + timedelta(days=total_days // 2)

    live = annotation_jobs.c.status.in_(("pending", "claimed"))
    comment_q = (
        select(
            annotation_jobs.c.job_id,
            annotation_jobs.c.priority,
            annotation_jobs.c.scope_id,
            feeds.c.code,
            feeds.c.posted_at,
        )
        .select_from(
            annotation_jobs
            .join(comments, comments.c.comment_id == annotation_jobs.c.target_id)
            .join(feeds, feeds.c.feed_id == comments.c.feed_id)
        )
        .where(live, annotation_jobs.c.target_type == "comment")
    )
    feed_q = (
        select(
            annotation_jobs.c.job_id,
            annotation_jobs.c.priority,
            annotation_jobs.c.scope_id,
            feeds.c.code,
            feeds.c.posted_at,
        )
        .select_from(annotation_jobs.join(feeds, feeds.c.feed_id == annotation_jobs.c.target_id))
        .where(live, annotation_jobs.c.target_type == "feed")
    )
    changes = []
    with engine.connect() as conn:
        for query in (comment_q, feed_q):
            for job_id, old, scope_id, code, posted_at in conn.execute(query):
                boundary = current_from(scope_id)
                posted_day = utc_naive_to_hkt(posted_at).date() if posted_at is not None else None
                current = boundary is None or (posted_day is not None and posted_day >= boundary)
                new = job_priority(posted_at, anchor, own=ownership.get(code) == "own", current=current)
                if new != old:
                    changes.append((job_id, new))
    if not dry_run and changes:
        now = clock.now()
        with engine.begin() as conn:
            for offset in range(0, len(changes), 500):
                for job_id, new in changes[offset:offset + 500]:
                    conn.execute(
                        update(annotation_jobs)
                        .where(annotation_jobs.c.job_id == job_id)
                        .values(priority=new, updated_at=now)
                    )
    log.info("重排优先级：%d 条改动%s", len(changes), "（dry-run 未写）" if dry_run else "")
    return len(changes)


def estimate(engine, cfg, task, scope_id=None, stage=None):
    """`--dry-run` 的用量估算。**不是报价**：网关没有给价格，这里只给条数、请求数与 token 区间。"""
    n = pending_count(engine, task, scope_id, stage=stage)
    prompt, _sv = resolve(task, cfg)
    batch = max(1, cfg.micro_batch_size)
    requests = math.ceil(n / batch) if n else 0
    # 中文 1 字 ≈ 1–1.6 token，给区间不给点估计。
    sys_chars = len(prompt.SYSTEM)
    sys_tok_lo, sys_tok_hi = sys_chars / 1.6, sys_chars / 1.0
    return {
        "task": task, "scope_id": scope_id, "stage": stage, "pending_items": n, "batch_size": batch,
        "requests": requests,
        "tokens_in_low": int(n * EST_IN_PER_ITEM + requests * sys_tok_lo),
        "tokens_in_high": int(n * EST_IN_PER_ITEM * 1.3 + requests * sys_tok_hi),
        "tokens_out_low": int(n * EST_OUT_PER_ITEM),
        "tokens_out_high": int(n * EST_OUT_PER_ITEM * 1.3),
        "note": "估算；网关未给价格，estimated_cost 仍为 NULL",
    }


# ── CLI ────────────────────────────────────────────────────────────────


def main(argv=None):
    """影子运行入口。

        python -m jobs.extract --codes 3033,2822 --from 2026-09-01 --to 2026-09-07
        python -m jobs.annotate --run --scope <scope_id> --max-items 100 --max-http-requests 100 \
          --calibration-report <calibration.json> --quality-report <gold-eval.json>
        python -m jobs.annotate --run --scope <scope_id> --dry-run

    `--enqueue` 与 `--run` **分开两步**，不是一个命令里顺次做完：排队不花钱，跑标注花钱。
    分开之后可以先排队、看一眼 `--status` 的数量对不对，再决定要不要发出去。
    按 ETF × 时间段抽取并预过滤请用 `python -m jobs.extract`。
    """
    import argparse

    ap = argparse.ArgumentParser(description="AI 标注作业")
    ap.add_argument("--enqueue", action="store_true", help="排进待办；排哪种由 --task 决定")
    ap.add_argument("--run", action="store_true", help="领取待办并调模型")
    ap.add_argument("--status", action="store_true", help="只看队列状态")
    ap.add_argument("--reprioritize", action="store_true", help="按当前锚点重算存量任务优先级")
    ap.add_argument("--dry-run", action="store_true", help="只估算待办的请求数与 token，不调模型")
    ap.add_argument("--task", default="comment_product")
    ap.add_argument("--codes", help="逗号分隔的产品代码，留空＝全部")
    ap.add_argument("--authors", help="逗号分隔的作者名（帖子任务），留空＝全部")
    ap.add_argument("--since", help="起始日期 YYYY-MM-DD（含）")
    ap.add_argument("--until", help="结束日期 YYYY-MM-DD（含）")
    ap.add_argument("--limit", type=int, help="排队条数上限")
    ap.add_argument("--max-items", type=int, help="本轮最多处理多少条")
    ap.add_argument("--budget-requests", type=int, help="本轮最多领取多少批（≈请求数）")
    ap.add_argument("--max-http-requests", type=int, help="Required for --run; includes retries")
    ap.add_argument("--calibration-report", type=Path,
                    help="comment_product 付费运行所需的批量校准报告")
    ap.add_argument("--quality-report", type=Path,
                    help="comment_product 付费运行所需的 v3 人工金标报告")
    ap.add_argument("--scope", help="只处理这个抽取范围（analysis_scopes.scope_id）")
    ap.add_argument("--stage", choices=(STAGE_STUDENT, STAGE_LLM, FILL_MISSING_STAGE),
                    help="只处理指定漏斗段；默认不分段")
    ap.add_argument("--priority", type=int, default=0)
    ap.add_argument(
        "--fill-missing", action="store_true",
        help="只排缺失 kind，写入时也不替换任何已有标注",
    )
    args = ap.parse_args(argv)
    if args.run and not args.dry_run and (args.max_http_requests is None or args.max_http_requests < 1):
        ap.error("--run requires --max-http-requests")
    if args.enqueue and args.task == "comment_product":
        ap.error("comment_product 必须先用 python -m jobs.extract 做 exact 筛选，禁止原始排队")
    if args.run and not args.dry_run and args.task == "comment_product" and not args.scope:
        ap.error("comment_product 只允许按 jobs.extract 生成的 --scope 运行")

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s %(message)s"
    )
    engine = make_engine()
    cfg = config.load(_allow_missing_key=not args.run or args.dry_run)
    # 配置里唯一敏感的是 Key，`redacted()` 只留尾四位 —— 够分辨「换过 Key 没有」，
    # 又不会把它写进任何一份可能被贴出去的日志（runbook §0）。
    log.info("配置：%s", json.dumps(cfg.redacted(), ensure_ascii=False))

    if args.run and not args.dry_run and args.task == "comment_product":
        # This low-level command remains available for bounded diagnosis, but a
        # real provider call receives the same release gates as orchestration.
        from jobs import analyze as analysis_job
        try:
            analysis_job.check_calibration(
                cfg, args.calibration_report, require_singleton=True,
            )
            analysis_job.check_quality(cfg, args.quality_report)
            load_current_exact_scope(
                engine,
                args.scope,
                require_comment_product=True,
            )
        except (ValueError, RuntimeError) as exc:
            ap.error(str(exc))

    parent_filter_config = None
    if args.task in COMMENT_TASKS and (
        args.enqueue or (args.run and not args.dry_run)
    ):
        try:
            with engine.connect() as conn:
                route_active = comment_routes_ready_on_connection(conn)
            if route_active:
                with engine.begin() as conn:
                    supersede_ineligible_route_jobs(conn)
            else:
                from jobs.backfill_comment_filter import prepare_comment_task_execution

                parent_filter_config, _retired = prepare_comment_task_execution(
                    engine,
                    supersede=True,
                    tasks=(args.task,),
                )
        except RuntimeError as exc:
            ap.error(str(exc))

    if args.reprioritize:
        print(json.dumps({"reprioritized": reprioritize(engine, dry_run=args.dry_run)}, ensure_ascii=False))
        return 0

    since = _parse_day(args.since)
    until = _parse_day(args.until, end=True)

    if args.enqueue:
        codes = [c.strip() for c in args.codes.split(",")] if args.codes else None
        authors = [a.strip() for a in args.authors.split(",")] if args.authors else None
        if args.task == "comment_product":
            n = enqueue_comments(engine, cfg, codes=codes, limit=args.limit, since=since,
                                 until=until, priority=args.priority, scope_id=args.scope,
                                 fill_missing=args.fill_missing or cfg.fill_missing_only)
        elif args.task == "post_annotation":
            n = enqueue_posts(engine, cfg, codes=codes, limit=args.limit, since=since,
                               until=until, authors=authors, priority=args.priority,
                               scope_id=args.scope,
                               fill_missing=args.fill_missing or cfg.fill_missing_only)
        elif args.task == "kol_comment_opinion":
            from jobs.extract import master_accounts
            n = enqueue_kol_comments(engine, cfg, authors or master_accounts()[0], codes=codes,
                                     limit=args.limit, since=since, until=until,
                                     priority=args.priority, scope_id=args.scope,
                                     fill_missing=args.fill_missing or cfg.fill_missing_only,
                                     filter_config=parent_filter_config)
        else:
            ap.error(f"--task {args.task} 没有对应的排队函数")
        log.info("已排队 %d 条（%s）", n, args.task)

    if args.dry_run:
        print(json.dumps(estimate(engine, cfg, args.task, args.scope, args.stage), ensure_ascii=False, indent=1))
        return 0

    if args.run:
        from ai.providers.base import RunControl
        from radar_db.leases import WorkerLease
        control = RunControl(args.max_http_requests)
        with WorkerLease(engine, "own-analysis"), control.interruptible():
            stats = run(engine, cfg, task=args.task, max_items=args.max_items, scope_id=args.scope,
                        budget_requests=args.budget_requests, stage=args.stage,
                        provider=build_provider(cfg, control=control),
                        parent_filter_config=parent_filter_config)
        log.info("本轮：%s", json.dumps(stats, ensure_ascii=False))
        return 2 if stats["aborted"] or stats["error"] else 0

    if args.status or not (args.enqueue or args.run):
        _print_status(engine, args.task)
    return 0


def _parse_day(s, end=False):
    if not s:
        return None
    lo, hi = hkt_range_utc_naive(s, s)
    return hi if end else lo


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
