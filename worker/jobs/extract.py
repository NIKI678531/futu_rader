"""按 ETF × 时间段抽取候选 → 规则预过滤 → 排进待办（ADR-0020 步骤 3）。

    python -m jobs.extract --codes 3033,7226 --from 2026-06-01 --to 2026-08-25
    python -m jobs.extract --own --range d30 --with-baseline --dry-run
    python -m jobs.extract --all --from 2026-05-28 --to 2026-08-25 --task both

## 它做什么、不做什么

做：把范围内的评论（按帖子的挂载产品与帖子日期）取出来，逐条过 `ai/prefilter.py` 的五条
规则，剔掉的以 `provider=rule` 落库，留下的排进 `annotation_jobs` 并打上 `scope_id`；
帖子只排 KOL 与官号作者的；最后给一份统计报告与 token 估算。**不调模型，不花钱。**

不做：任何口径计算。它只回答「哪些评论在范围内、哪些不用问模型」。

## 时间口径

沿用市场域的「评论归属帖子日期」：`feeds.posted_at ∈ [from, to]`（闭区间，实现用半开
`< to + 1 天`）。评论自身的 `posted_at` 大量缺失，改口径需要单独决策并回归五页统计。

`--with-baseline` 把紧邻的上一等长区间也纳入 —— 环比要基准期，页面上的「较上一等长区间」
没有基准期的标注就只能显示「数据暂不可用」。基准期的任务优先级低于当前期：先亮再全。

## 幂等

同一候选在同一版本下只会有一条待办（`annotation_jobs` 唯一键），重复运行只会报
「已存在 N 条」而不会重复排队；规则剔除同样按 `(comment, code, 规则指纹)` 去重。
所以可以放心地对重叠范围反复运行。
"""

import argparse
import json
import logging
import os
import sys
import uuid
import tempfile
from contextlib import contextmanager
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import insert, or_, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import clock  # noqa: E402
from ai import config, neardup, prefilter  # noqa: E402
from ai.lexicon import offpool_stocks, product_aliases  # noqa: E402
from collection.exact import (  # noqa: E402
    EXACT_RULE,
    EXACT_RULE_VERSION,
    ExactProductMatcher,
    route_parent_feed,
)
from jobs import annotate  # noqa: E402
from jobs.import_dump import pool_codes  # noqa: E402
from radar_db import make_engine  # noqa: E402
from radar_db.comment_filter import (  # noqa: E402
    load_comment_filter_config,
    require_filter_ready,
)
from radar_db.comment_routes import (  # noqa: E402
    COMMENT_ROUTE_VERSION,
    readiness_on_connection as comment_routes_ready_on_connection,
)
from radar_db.schema import (  # noqa: E402
    analysis_scopes,
    annotation_jobs,
    annotations,
    comments,
    feed_mentions,
    feeds,
    mentions,
    meta_kv,
)
from radar_db.time_windows import hkt_range_utc_naive, utc_naive_to_hkt  # noqa: E402

log = logging.getLogger("worker.extract")

MASTER = REPO_ROOT / "backend" / "fixtures" / "demo" / "master.json"
REPORT_DIR = REPO_ROOT / ".scratch" / "llm-90d"

RANGE_DAYS = {"d1": 1, "d2": 2, "d7": 7, "d14": 14, "d30": 30}


def new_scope_id():
    return "scope-" + clock.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]


def master_accounts(path=MASTER):
    """KOL 32 位＋官号 20 个的作者名（全称与简称都算）。与 `SqlProvider._author_type` 同一份名单。"""
    with open(path, encoding="utf-8") as fh:
        m = json.load(fh)
    kols = [k["name"] for k in m["kols"]]
    officials = [o["full"] for o in m["officials"]] + [o["short"] for o in m["officials"]]
    return kols, officials


def resolve_codes(args, ownership):
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        unknown = [c for c in codes if c not in ownership]
        if unknown:
            raise SystemExit(f"不在产品池里的代码：{unknown}")
        return codes
    if args.all:
        return list(ownership)
    return [c for c, o in ownership.items() if o == "own"]


def resolve_window(args, engine):
    """返回 `(date_from, date_to_inclusive)`。`--range` 相对 `meta_kv.anchor`，与页面同一锚点。"""
    if args.range:
        with engine.connect() as conn:
            anchor = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "anchor")).scalar()
        if not anchor:
            raise SystemExit("meta_kv 里没有 anchor：先跑 import_dump / etl")
        to = datetime.strptime(anchor[:10], "%Y-%m-%d")
        frm = to - timedelta(days=RANGE_DAYS[args.range] - 1)
        return frm, to
    if not (getattr(args, "from_") and args.to):
        raise SystemExit("需要 --from 与 --to，或 --range")
    frm = datetime.strptime(args.from_, "%Y-%m-%d")
    to = datetime.strptime(args.to, "%Y-%m-%d")
    if to < frm:
        raise SystemExit("--to 早于 --from")
    return frm, to


@contextmanager
def candidate_snapshot(engine, codes, since, until, page_size=1000):
    with engine.connect() as conn:
        comment_routes = comment_routes_ready_on_connection(conn)
    with tempfile.TemporaryDirectory(prefix="radar-extract-") as directory:
        handles, paths = OrderedDict(), {}
        try:
            for row in _comment_candidates_hkt(
                engine, codes=codes, since=since, until=until, page_size=page_size,
                comment_routes=comment_routes,
            ):
                key = (row.code, utc_naive_to_hkt(row.posted_at).date().isoformat())
                if key not in handles:
                    if len(handles) >= 32:
                        handles.popitem(last=False)[1].close()
                    paths[key] = Path(directory) / ("-".join(key) + ".jsonl")
                    handles[key] = paths[key].open("a", encoding="utf-8")
                handles.move_to_end(key)
                handles[key].write(json.dumps(dict(row._mapping), ensure_ascii=False, default=str) + "\n")
        finally:
            for handle in handles.values():
                handle.close()

        def reader(_engine, *, codes, since, until, **_kwargs):
            for code, day in sorted(paths):
                if code not in codes or not since.date().isoformat() <= day < until.date().isoformat():
                    continue
                path = Path(directory) / f"{code}-{day}.jsonl"
                with path.open(encoding="utf-8") as source:
                    for line in source:
                        value = json.loads(line)
                        value["posted_at"] = datetime.fromisoformat(value["posted_at"])
                        yield SimpleNamespace(**value)
        yield reader


def _utc_bounds(since, until):
    """Translate local-midnight ``[since, until)`` markers to database bounds."""
    return hkt_range_utc_naive(since.date(), (until - timedelta(days=1)).date())


def _comment_candidates_hkt(engine, *, codes, since, until, comment_routes=None, **kwargs):
    lo, hi = _utc_bounds(since, until)
    if comment_routes is None:
        with engine.connect() as conn:
            comment_routes = comment_routes_ready_on_connection(conn)
    return annotate._comment_candidates(
        engine,
        codes=codes,
        since=lo,
        until=hi,
        include_body_targets=False,
        include_empty=True,
        comment_routes=comment_routes,
        **kwargs,
    )


def _candidate_values(row):
    mapping = getattr(row, "_mapping", None)
    return dict(mapping) if mapping is not None else dict(vars(row))


def _exact_candidate_routes(
    engine,
    rows,
    matcher=None,
    *,
    batch_size=500,
    filter_config=None,
):
    """Yield one anchor route per comment based only on its parent feed."""

    # ``matcher`` is accepted for preview/calibration compatibility. Product
    # aliases and reply text are intentionally outside the parent-feed seam.
    del matcher
    filter_config = filter_config or load_comment_filter_config()

    iterator = iter(rows)
    while True:
        batch = []
        try:
            for _ in range(batch_size):
                batch.append(next(iterator))
        except StopIteration:
            pass
        if not batch:
            return

        feed_ids = {row.feed_id for row in batch}
        by_feed = {feed_id: set() for feed_id in feed_ids}
        feed_facts = {}
        with engine.connect() as conn:
            feed_facts = {
                row.feed_id: row
                for row in conn.execute(
                    select(feeds.c.feed_id, feeds.c.code, feeds.c.source_ticker).where(
                        feeds.c.feed_id.in_(feed_ids)
                    )
                )
            }
            records = conn.execute(
                select(feed_mentions.c.feed_id, feed_mentions.c.raw_ticker).where(
                    feed_mentions.c.feed_id.in_(feed_ids),
                )
            )
            for feed_id, ticker in records:
                by_feed.setdefault(feed_id, set()).add(ticker)

        for row in batch:
            parent = feed_facts.get(row.feed_id)
            anchor_code = parent.code if parent is not None else getattr(
                row, "anchor_code", row.code
            )
            route = route_parent_feed(
                anchor_code=anchor_code,
                source_ticker=parent.source_ticker if parent is not None else None,
                mentioned_tickers=by_feed.get(row.feed_id, ()),
                config=filter_config,
            )
            routed = []
            for code in route.subject_codes:
                values = _candidate_values(row)
                values["code"] = code
                routed.append(SimpleNamespace(**values))
            yield row, route, routed


def _historical_exact_units(engine, *, codes, since, until):
    """Return active historical comment/subject units in the current scope.

    Most units are reachable through today's section-scoped candidate query.
    The ``subject_code`` arm is deliberate: legacy exact-v2 could route a reply
    outside its parent section, so those old subjects must still be retired.
    """

    selected_codes = tuple(dict.fromkeys(str(code) for code in codes))
    if not selected_codes:
        return []
    lo, hi = _utc_bounds(since, until)
    def scope(subject_code):
        return or_(
            subject_code.in_(selected_codes),
            feeds.c.code.in_(selected_codes),
        )

    def columns(subject):
        return (
            comments.c.comment_id,
            subject.label("subject_code"),
            comments.c.content,
            comments.c.feed_id,
            feeds.c.code.label("anchor_code"),
        )
    job_query = (
        select(*columns(annotation_jobs.c.subject_code))
        .select_from(
            annotation_jobs
            .join(comments, comments.c.comment_id == annotation_jobs.c.target_id)
            .join(feeds, feeds.c.feed_id == comments.c.feed_id)
        )
        .where(
            annotation_jobs.c.target_type == "comment",
            annotation_jobs.c.task == "comment_product",
            annotation_jobs.c.status.in_(("pending", "claimed", "failed", "done")),
            feeds.c.posted_at >= lo,
            feeds.c.posted_at < hi,
            scope(annotation_jobs.c.subject_code),
        )
    )
    newer = annotations.alias("newer_exact_route")
    annotation_query = (
        select(*columns(annotations.c.subject_code))
        .select_from(
            annotations
            .join(comments, comments.c.comment_id == annotations.c.target_id)
            .join(feeds, feeds.c.feed_id == comments.c.feed_id)
        )
        .where(
            annotations.c.target_type == "comment",
            annotations.c.kind == "relevance",
            annotations.c.review_state != "rejected",
            ~select(newer.c.annotation_id).where(
                newer.c.supersedes_id == annotations.c.annotation_id
            ).exists(),
            feeds.c.posted_at >= lo,
            feeds.c.posted_at < hi,
            scope(annotations.c.subject_code),
        )
    )

    units = {}
    with engine.connect() as conn:
        for row in conn.execute(job_query):
            units[(row.comment_id, row.subject_code)] = row
        for row in conn.execute(annotation_query):
            units.setdefault((row.comment_id, row.subject_code), row)
    return list(units.values())


def _stale_exact_route_decisions(
    engine, *, codes, since, until, matcher=None, filter_config=None, now
):
    """Build exclusions for historical subjects no longer selected by routing."""

    units = _historical_exact_units(
        engine,
        codes=codes,
        since=since,
        until=until,
    )
    if not units:
        return []

    del matcher
    filter_config = filter_config or load_comment_filter_config()
    feed_ids = sorted({row.feed_id for row in units})
    by_feed = {feed_id: set() for feed_id in feed_ids}
    feed_facts = {}
    with engine.connect() as conn:
        for offset in range(0, len(feed_ids), 500):
            batch = feed_ids[offset:offset + 500]
            for fact in conn.execute(
                select(feeds.c.feed_id, feeds.c.code, feeds.c.source_ticker).where(
                    feeds.c.feed_id.in_(batch)
                )
            ):
                feed_facts[fact.feed_id] = fact
            records = conn.execute(
                select(feed_mentions.c.feed_id, feed_mentions.c.raw_ticker).where(
                    feed_mentions.c.feed_id.in_(batch),
                )
            )
            for feed_id, ticker in records:
                by_feed.setdefault(feed_id, set()).add(ticker)

    decisions = []
    for row in units:
        parent = feed_facts.get(row.feed_id)
        route = route_parent_feed(
            anchor_code=parent.code if parent is not None else row.anchor_code,
            source_ticker=parent.source_ticker if parent is not None else None,
            mentioned_tickers=by_feed.get(row.feed_id, ()),
            config=filter_config,
        )
        if row.subject_code in route.subject_codes:
            continue
        decisions.append((
            row.comment_id,
            row.subject_code,
            row.content,
            prefilter.Decision(
                EXACT_RULE,
                {
                    "reason": "stale_subject_route",
                    "route_reason": route.reason,
                    "matched_tickers": list(route.matched_tickers),
                    "rule_version": EXACT_RULE_VERSION,
                    "decided_at": now.isoformat(timespec="seconds"),
                },
            ),
        ))
    return decisions


def run(engine, cfg, *, codes, date_from, date_to, task="comment_product", with_baseline=False,
        drop_offpool=True, dry_run=False, authors=None, ownership=None, report_dir=REPORT_DIR,
        candidate_reader=None):
    """执行一次抽取。返回 stats dict（也写进 `analysis_scopes.stats_json`）。"""
    filter_config = None
    with engine.connect() as conn:
        comment_routes = comment_routes_ready_on_connection(conn)
    if task in ("comment_product", "both"):
        filter_config = load_comment_filter_config()
        if not dry_run and not comment_routes:
            _require_comment_filter_ready(engine, filter_config)
    ownership = ownership or pool_codes()
    now = clock.now()
    scope_id = new_scope_id()
    until = date_to + timedelta(days=1)
    days = (date_to - date_from).days + 1
    windows = [("current", date_from, until)]
    if with_baseline:
        b_to = date_from
        b_from = date_from - timedelta(days=days)
        windows.append(("baseline", b_from, b_to))

    prompt, schema_version = annotate.resolve("comment_product", cfg)
    anchor = _read_anchor(engine) or date_to.date()
    stats = {
        "scope_id": scope_id, "task": task, "codes": codes, "date_from": date_from.strftime("%Y-%m-%d"),
        "date_to": date_to.strftime("%Y-%m-%d"), "with_baseline": with_baseline,
        "time_basis": "feed_posted_at", "dry_run": dry_run, "anchor": anchor.isoformat(),
        "prompt_version": prompt.VERSION, "taxonomy_version": cfg.taxonomy_version,
        "schema_version": schema_version, "exactRuleVersion": EXACT_RULE_VERSION,
        "commentRouteVersion": COMMENT_ROUTE_VERSION if comment_routes else None,
        "comments": None, "posts": None, "estimate": None,
    }

    def priority_of(name):
        # 最近窗口优先（ADR-0021）：距锚点分档 ＋ 自家 ＋ 当前期。三类任务同一个函数。
        return lambda posted_at, code: annotate.job_priority(
            posted_at, anchor, own=ownership.get(code) == "own", current=(name == "current"))

    if not dry_run:
        with engine.begin() as conn:
            conn.execute(
                insert(analysis_scopes).values(
                    scope_id=scope_id, task=task, codes_json=json.dumps(codes),
                    date_from=windows[-1][1], date_to=date_to, time_basis="feed_posted_at",
                    with_baseline=with_baseline, prompt_version=prompt.VERSION,
                    taxonomy_version=cfg.taxonomy_version, schema_version=schema_version,
                    stats_json=None, created_at=now,
                )
            )

    if task in ("comment_product", "both"):
        stats["comments"] = _extract_comments(
            engine, cfg, prompt, schema_version, scope_id, codes, windows, ownership,
            drop_offpool=drop_offpool, dry_run=dry_run, now=now, priority_of=priority_of,
            candidate_reader=candidate_reader, filter_config=filter_config,
            comment_routes=comment_routes,
        )
        # 合作 KOL 的评论顺手排进 `kol_comment_opinion`（KOL 详情 M7 要它；量很小）。
        kols, _officials = master_accounts()
        stats["kol_comments"] = 0
        if not dry_run:
            for name, since, until in windows:
                lo, hi = _utc_bounds(since, until)
                stats["kol_comments"] += annotate.enqueue_kol_comments(
                    engine, cfg, kols, codes=codes, since=lo, until=hi,
                    scope_id=scope_id, priority_of=priority_of(name),
                    filter_config=filter_config,
                )
    if task in ("post_annotation", "both"):
        stats["posts"] = _extract_posts(
            engine, cfg, scope_id, codes, windows, authors, dry_run=dry_run, priority_of=priority_of,
        )

    if not dry_run:
        stats["estimate"] = {
            t: annotate.estimate(engine, cfg, t, scope_id)
            for t in (("comment_product", "post_annotation") if task == "both" else (task,))
        }
        with engine.begin() as conn:
            conn.execute(
                analysis_scopes.update()
                .where(analysis_scopes.c.scope_id == scope_id)
                .values(stats_json=json.dumps(stats, ensure_ascii=False))
            )
    if report_dir is not None:
        _write_report(stats, report_dir)
    return stats


def _read_anchor(engine):
    from datetime import date

    with engine.connect() as conn:
        a = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "anchor")).scalar()
    return date.fromisoformat(a[:10]) if a else None


def _require_comment_filter_ready(engine, filter_config):
    """Prevent task mutations until the historical parent facts are activated."""

    if filter_config.version != EXACT_RULE_VERSION:
        raise RuntimeError(
            f"worker exact rule version {EXACT_RULE_VERSION!r} does not match "
            f"comment filter config {filter_config.version!r}"
        )
    require_filter_ready(engine, filter_config)


def _extract_comments(engine, cfg, prompt, schema_version, scope_id, codes, windows, ownership,
                      *, drop_offpool, dry_run, now, priority_of=None, fold_neardup=True,
                      candidate_reader=None, filter_config=None, comment_routes=False):
    plex = product_aliases.ProductLexicon()
    filter_config = filter_config or load_comment_filter_config()
    extra = offpool_stocks.load_from_db(engine, set(ownership))
    slex = offpool_stocks.StockLexicon(extra)
    pf = prefilter.Prefilter(plex, slex, drop_offpool=drop_offpool)

    counts = {"candidates": 0, "dropped": {r: 0 for r in (*prefilter.RULES, EXACT_RULE)}, "kept": 0,
              "near_duplicate_members": 0, "cluster_rows_written": 0,
              "queued_new": 0, "already_queued_or_done": 0, "rule_rows_written": 0,
              "rule_rows_withdrawn": 0,
              "by_window": {}, "earliest": None, "latest": None, "offpool_stock_size": slex.size()}
    rule_run_id = None
    if not dry_run:
        rule_run_id = "rule-" + scope_id
        prefilter.open_rule_run(engine, rule_run_id, "comment_product", now,
                                taxonomy_version=cfg.taxonomy_version, schema_version=schema_version)

    if candidate_reader is not None:
        windows = [(f"{name}:{(since + timedelta(days=offset)).date()}",
                    since + timedelta(days=offset), min(until, since + timedelta(days=offset + 1)))
                   for name, since, until in windows for offset in range((until - since).days)]
    for name, since, until in windows:
        kept_rows, decisions = [], {}
        n_cand = 0
        candidate_rows = (candidate_reader or _comment_candidates_hkt)(
            engine, codes=codes, since=since, until=until,
            comment_routes=comment_routes,
        )
        routed_candidates = (
            (
                row,
                SimpleNamespace(
                    subject_codes=(row.code,),
                    reason="content_cashtag_route",
                    matched_tickers=(),
                ),
                (row,),
            )
            for row in candidate_rows
        ) if comment_routes else _exact_candidate_routes(
            engine, candidate_rows, filter_config=filter_config
        )
        for anchor_row, route, routed_rows in routed_candidates:
            n_cand += 1
            # Candidate discovery is section-scoped. A filtered parent writes
            # one auditable exclusion for its anchor; a qualifying parent sends
            # every reply through the unchanged downstream prefilter/model.
            if not comment_routes and anchor_row.code not in route.subject_codes:
                d = prefilter.Decision(
                    EXACT_RULE,
                    {
                        "reason": route.reason,
                        "matched_tickers": list(route.matched_tickers),
                        "rule_version": EXACT_RULE_VERSION,
                        "decided_at": now.isoformat(timespec="seconds"),
                    },
                )
                counts["dropped"][d.rule] += 1
                decisions[(anchor_row.comment_id, anchor_row.code)] = (
                    anchor_row.comment_id,
                    anchor_row.code,
                    anchor_row.content,
                    d,
                )
            for r in routed_rows:
                d = pf.classify(r.content, r.code, comment_id=r.comment_id,
                                author_uid=r.author_uid, feed_id=r.feed_id)
                if d.dropped:
                    counts["dropped"][d.rule] += 1
                    decisions[(r.comment_id, r.code)] = (r.comment_id, r.code, r.content, d)
                    continue
                kept_rows.append(r)
        # Re-evaluate historical subjects independently of today's discovery
        # rows so legacy cross-product routes and newly ineligible parents are
        # superseded even though they no longer appear as current candidates.
        if not comment_routes:
            for item in _stale_exact_route_decisions(
                engine,
                codes=codes,
                since=since,
                until=until,
                filter_config=filter_config,
                now=now,
            ):
                decisions.setdefault((item[0], item[1]), item)
        # A comment/product unit is queued exactly once.
        kept_rows = list({(r.comment_id, r.code): r for r in kept_rows}.values())
        # 第 6 条：同产品同日近重复折叠（ADR-0021）。成员不排任务，只写簇行；代表照常排。
        if fold_neardup:
            reps, members = neardup.fold(
                kept_rows,
                key_of=lambda r: (
                    r.code, utc_naive_to_hkt(r.posted_at).date() if r.posted_at else None,
                ),
                id_of=lambda r: r.comment_id, text_of=lambda r: r.content,
            )
        else:
            reps, members = kept_rows, []
        counts["near_duplicate_members"] += len(members)
        rows = []
        for r in reps:
            if priority_of is not None:
                priority = priority_of(name.split(":")[0])(r.posted_at, r.code)
            else:
                # 自家优先、当前期优先：页面先亮再全。
                priority = (2 if ownership.get(r.code) == "own" else 0) + (1 if name == "current" else 0)
            rows.append(annotate.job_row_for_comment(cfg, prompt, schema_version, r,
                                                     priority=priority, scope_id=scope_id, now=now))
        n_kept = len(rows)
        counts["candidates"] += n_cand
        counts["kept"] += n_kept
        counts["by_window"][name] = {"from": since.strftime("%Y-%m-%d"),
                                     "to": (until - timedelta(days=1)).strftime("%Y-%m-%d"),
                                     "candidates": n_cand, "kept": n_kept,
                                     "near_duplicate_members": len(members)}
        if not dry_run:
            inserted = annotate._insert_jobs(engine, rows)
            counts["queued_new"] += inserted
            counts["already_queued_or_done"] += len(rows) - inserted
            counts["rule_rows_withdrawn"] += prefilter.withdraw_rule_exclusions(
                engine,
                rule_run_id,
                [(r.comment_id, r.code, r.content) for r in kept_rows],
                now,
            )
            counts["rule_rows_written"] += prefilter.write_rule_annotations(
                engine, rule_run_id, list(decisions.values()), now)
            counts["cluster_rows_written"] += neardup.write_cluster_rows(
                engine, rule_run_id, [(r.comment_id, r.code, rep, d) for r, rep, d in members], now)
        else:
            counts["queued_new"] += len(rows)

    if not dry_run:
        from radar_db.schema import annotation_runs
        with engine.begin() as conn:
            conn.execute(
                annotation_runs.update().where(annotation_runs.c.run_id == rule_run_id)
                .values(finished_at=clock.now(), status="done",
                        input_count=counts["candidates"],
                        success_count=(counts["rule_rows_written"] + counts["rule_rows_withdrawn"]))
            )
    return counts


def _extract_posts(engine, cfg, scope_id, codes, windows, authors, *, dry_run, priority_of=None):
    kols, officials = master_accounts()
    if authors is None or authors == "both":
        names = kols + officials
    elif authors == "kol":
        names = kols
    elif authors == "official":
        names = officials
    else:
        names = list(authors)
    out = {"authors": len(names), "queued_new": 0, "by_window": {}}
    for name, since, until in windows:
        lo, hi = _utc_bounds(since, until)
        if dry_run:
            # 只数不排：复用排队函数的查询会写库，这里直接数候选。
            from sqlalchemy import and_, func, or_
            from radar_db.schema import feeds
            with engine.connect() as conn:
                n = conn.execute(
                    select(func.count()).select_from(feeds).where(
                        feeds.c.code.in_(codes), feeds.c.posted_at >= lo, feeds.c.posted_at < hi,
                        feeds.c.author_name.in_(names),
                        or_(and_(feeds.c.content.isnot(None), feeds.c.content != ""),
                            and_(feeds.c.title.isnot(None), feeds.c.title != "")),
                    )
                ).scalar_one()
        else:
            n = annotate.enqueue_posts(engine, cfg, codes=codes, since=lo, until=hi,
                                       authors=names, scope_id=scope_id,
                                       priority=1 if name == "current" else 0,
                                       priority_of=priority_of(name) if priority_of else None)
        out["queued_new"] += n
        out["by_window"][name] = n
    return out


def _write_report(stats, report_dir):
    """统计报告：只有计数，没有任何原文或作者信息，可以进 `.scratch/`。"""
    try:
        Path(report_dir).mkdir(parents=True, exist_ok=True)
        path = Path(report_dir) / f"{stats['scope_id']}.md"
        lines = [
            f"# 抽取报告 {stats['scope_id']}",
            "",
            f"- 任务：{stats['task']}　{'（dry-run，未写库）' if stats['dry_run'] else ''}",
            f"- 产品：{len(stats['codes'])} 只　{', '.join(stats['codes'][:12])}{' …' if len(stats['codes']) > 12 else ''}",
            f"- 区间：{stats['date_from']} ～ {stats['date_to']}（帖子日期口径）"
            + ("＋基准期" if stats["with_baseline"] else ""),
            f"- 版本：prompt={stats['prompt_version']} taxonomy={stats['taxonomy_version']} schema={stats['schema_version']}",
            "",
        ]
        c = stats.get("comments")
        if c:
            lines += [
                "## 评论",
                f"- 候选：{c['candidates']:,}",
                "- 规则剔除：" + "、".join(f"{k} {v:,}" for k, v in c["dropped"].items()),
                f"- 近重复折叠：{c.get('near_duplicate_members', 0):,} 条成员（抄代表结论，不排任务）",
                f"- 交模型：{c['kept']:,}（新排队 {c['queued_new']:,}，已存在 {c['already_queued_or_done']:,}）",
                f"- 规则结论落库：{c['rule_rows_written']:,} 个判定单元",
                "- 分窗：" + "；".join(
                    f"{k} {v['from']}～{v['to']} 候选 {v['candidates']:,} 留 {v['kept']:,}"
                    for k, v in c["by_window"].items()),
                "",
            ]
        p = stats.get("posts")
        if p:
            lines += ["## 帖子（KOL＋官号作者）", f"- 作者名 {p['authors']} 个，新排队 {p['queued_new']:,}", ""]
        e = stats.get("estimate")
        if e:
            lines += ["## 用量估算（不是报价）"]
            for t, est in e.items():
                lines.append(
                    f"- {t}：待办 {est['pending_items']:,}，请求约 {est['requests']:,}，"
                    f"输入 token {est['tokens_in_low']:,}–{est['tokens_in_high']:,}，"
                    f"输出 token {est['tokens_out_low']:,}–{est['tokens_out_high']:,}"
                )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log.info("报告：%s", path)
    except OSError as exc:  # 报告写不进去不该让抽取失败
        log.warning("报告写入失败：%s", exc)


def main(argv=None):
    ap = argparse.ArgumentParser(description="按 ETF × 时间段抽取候选并预过滤、排队（不调模型）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--codes", help="逗号分隔的产品代码")
    g.add_argument("--own", action="store_true", help="全部自家产品（默认）")
    g.add_argument("--all", action="store_true", help="完整生产产品池")
    ap.add_argument("--from", dest="from_", help="起始日期 YYYY-MM-DD（含）")
    ap.add_argument("--to", help="结束日期 YYYY-MM-DD（含）")
    ap.add_argument("--range", choices=sorted(RANGE_DAYS), help="相对 meta_kv.anchor 的预设区间")
    ap.add_argument("--with-baseline", action="store_true", help="同时纳入上一等长区间（环比要基准期）")
    ap.add_argument("--task", choices=("comment_product", "post_annotation", "both"), default="comment_product")
    ap.add_argument("--authors", choices=("kol", "official", "both"), default="both", help="帖子任务只排这些作者")
    ap.add_argument("--no-prefilter-offpool", action="store_true", help="关掉第 5 条规则（仅个股）")
    ap.add_argument("--dry-run", action="store_true", help="只数不写：不建 scope、不排队、不落规则结论")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s %(message)s")
    engine = make_engine()
    # 抽取不发请求：没配 Key 也能排队；只要 AI_PRIMARY_MODEL 在（它进 input_hash）。
    cfg = config.load(_allow_missing_key=True)
    ownership = pool_codes()
    codes = resolve_codes(args, ownership)
    date_from, date_to = resolve_window(args, engine)

    stats = run(engine, cfg, codes=codes, date_from=date_from, date_to=date_to, task=args.task,
                with_baseline=args.with_baseline, drop_offpool=not args.no_prefilter_offpool,
                dry_run=args.dry_run, authors=args.authors, ownership=ownership)
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
