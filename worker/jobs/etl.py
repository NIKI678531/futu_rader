"""`src_*` 镜像 → 四张原始事实表（[ADR-0009](../../docs/adr/0009-worker-scope.md)）。

    python -m jobs.etl

`raw_json` 里装的是富途 API 的完整原始载荷，MySQL 列只抽了极小一部分。这一步把它拆成
`feeds` / `comments` / `mentions` / `users`，好让 `backend/core/` 的口径 SQL 只查扁平表
——那也是本地 SQLite 与生产 MySQL 能共用一份口径 SQL 的前提（JSON 函数两个方言不通用）。

**这里一个口径公式都不碰。** 热度、去重、排名、环比、情绪净值全部在 `backend/core/`
（铁律 1）。这里只回答「这条帖子的转发数是多少」，不回答「这只产品的热度是多少」。

## 导入 20,000 条样本上实测到的、决定实现的四件事

1. **`author_uid` 列 99.4% 为空**，其中 99.9% 能从 `raw_json.user_info.user_id` 回填。
   所以作者身份必须从 JSON 拿，列只是兜底。
2. **`all_related_stock_infos` / `stock_items` / `plate_ids` 全部为空**（20,000 条无一例外）。
   ADR-0009 把它们列为多对多提及来源，实测不成立——正文提及走
   `summary.rich_text[].stock`，并补充解析 `title + content_text` 内的严格 FUTU cashtag；
   dump 与 MarketInsight 数据库同步共用 `collection.normalization.normalize_feed`。
3. **`share_count` 在 `common` 与顶层都有，`feed_comm` 下没有**（feed_type 5 实测）。
   ADR-0009 说随 `feed_type` 换名，所以三处都试；三处都没有就写 NULL，不写 0（铁律 2）。
4. **约 0.09% 的 `raw_json` 不是合法 JSON**：源库那一列是 `text`（65535 字节上限），
   超长载荷被静默截断。这些行的 `share_count` / 评论 / 正文提及**全部取不到**，
   落 NULL 并把 `raw_json_broken` 置真——让「取不到」和「确实是零」在库里就分得开。
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "worker"))

from sqlalchemy import delete, insert, select  # noqa: E402

from collection.normalization import normalize_feed  # noqa: E402
from jobs.backfill_comment_filter import (  # noqa: E402
    STATUS_KEY as COMMENT_FILTER_STATUS_META_KEY,
    finalize_parent_filter,
    require_exact_rule_version,
    supersede_all_comment_jobs,
)
from jobs.import_dump import pool_codes, ticker_to_code  # noqa: E402
from radar_db import make_engine  # noqa: E402
from radar_db.comment_filter import (  # noqa: E402
    load_comment_filter_config,
    normalize_source_ticker,
)
from radar_db.comment_routes import COMMENT_ROUTE_READY_META_KEY  # noqa: E402
from radar_db.schema import (  # noqa: E402
    comments,
    comment_product_routes,
    feed_mentions,
    feeds,
    mentions,
    meta_kv,
    src_feeds,
    src_stocks,
    src_users,
    users,
)

BATCH = 2000


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _hash_fact(hasher, kind, value):
    """Add one normalized output fact to a deterministic streaming digest."""

    hasher.update(kind.encode("ascii"))
    hasher.update(b"\0")
    hasher.update(json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8"))
    hasher.update(b"\n")


def run(engine, batch_size=BATCH):
    codes = pool_codes()
    filter_config = require_exact_rule_version(load_comment_filter_config())
    content_hasher = hashlib.sha256()

    with engine.connect() as conn:
        stock_facts = {
            sid: (ticker_to_code(ticker), normalize_source_ticker(ticker))
            for sid, ticker in conn.execute(select(src_stocks.c.stock_id, src_stocks.c.ticker))
        }
        for row in conn.execute(select(
            src_users.c.user_id,
            src_users.c.nick_name,
            src_users.c.follower_num,
            src_users.c.following_num,
            src_users.c.ip_region,
            src_users.c.self_description,
        ).order_by(src_users.c.user_id)):
            _hash_fact(content_hasher, "user", row._mapping)

    superseded_comment_jobs = 0
    with engine.begin() as conn:
        # Invalidate before the first destructive write.  If a later batch
        # fails, product reads/workers stay closed and global cache revisions
        # already reflect that the fact tables may be only partially rebuilt.
        finalize_parent_filter(conn, filter_config, activate=False)
        superseded_comment_jobs = supersede_all_comment_jobs(conn)
        conn.execute(delete(meta_kv).where(meta_kv.c.k == COMMENT_ROUTE_READY_META_KEY))
        conn.execute(delete(comment_product_routes))
        for tbl in (comments, mentions, feed_mentions, feeds, users):
            conn.execute(delete(tbl))
        # users 是 src_users 的直接投影：ETL 不给它加任何推断字段。
        conn.execute(
            insert(users).from_select(
                ["user_id", "nick_name", "follower_num", "following_num", "ip_region",
                 "self_description"],
                select(
                    src_users.c.user_id,
                    src_users.c.nick_name,
                    src_users.c.follower_num,
                    src_users.c.following_num,
                    src_users.c.ip_region,
                    src_users.c.self_description,
                ),
            )
        )

    n = broken = n_comments = n_mentions = n_feed_mentions = unresolved_source_tickers = 0
    seen_comment = set()
    fbuf, cbuf, mbuf, fmbuf = [], [], [], []
    t0 = time.time()

    src = engine.connect().execution_options(stream_results=True, yield_per=500)
    out = engine.connect()
    try:
        for row in src.execute(select(src_feeds).order_by(src_feeds.c.feed_id)):
            n += 1
            code, source_ticker = stock_facts[row.stock_id]
            observation = normalize_feed(
                dict(row._mapping),
                code,
                row.scraped_at,
                source_ticker=source_ticker,
            )
            feed_fact = dict(observation.feed)
            fbuf.append(feed_fact)
            _hash_fact(content_hasher, "feed", feed_fact)
            broken += int(bool(observation.feed["raw_json_broken"]))
            unresolved_source_tickers += int(observation.feed["source_ticker"] is None)

            # Historical dump ETL and MarketInsight synchronization share one
            # input normalizer.  In particular, body mentions keep the anchor's
            # own cashtag and strict tags in title/content, while ``anchor``
            # remains provenance only.
            for mention in observation.mentions:
                value = dict(mention)
                value["in_pool"] = value["source"] == "anchor" or value["code"] in codes
                mbuf.append(value)
                _hash_fact(content_hasher, "mention", value)
            for mention in observation.feed_mentions:
                value = dict(mention)
                fmbuf.append(value)
                _hash_fact(content_hasher, "feed_mention", value)

            # ── 评论 ──────────────────────────────────────────────
            for normalized_comment in observation.comments:
                cid = normalized_comment["comment_id"]
                # 同一条评论可能同时出现在 comment_items 与 popular_comments，
                # 也可能被两条帖子引用。评论条数是 ADR-0011 的口径基数，重复计数会直接
                # 污染它，所以在事实层就去重。
                if cid in seen_comment:
                    continue
                seen_comment.add(cid)
                comment_fact = dict(normalized_comment)
                cbuf.append(comment_fact)
                _hash_fact(content_hasher, "comment", comment_fact)

            if len(fbuf) >= batch_size:
                n_comments += len(cbuf)
                n_mentions += len(mbuf)
                n_feed_mentions += len(fmbuf)
                _flush(out, fbuf, cbuf, mbuf, fmbuf)
                fbuf, cbuf, mbuf, fmbuf = [], [], [], []
                log(f"  {n:,}/504,400 帖（{time.time() - t0:.0f}s）")

        n_comments += len(cbuf)
        n_mentions += len(mbuf)
        n_feed_mentions += len(fmbuf)
        _flush(out, fbuf, cbuf, mbuf, fmbuf)
    finally:
        src.close()
        out.close()

    stats = {
        "feeds": n,
        "broken": broken,
        "comments": n_comments,
        "mentions": n_mentions,
        "feed_mentions": n_feed_mentions,
        "unresolved_source_tickers": unresolved_source_tickers,
        "content_digest": content_hasher.hexdigest(),
        "superseded_comment_jobs": superseded_comment_jobs,
    }
    stamp(engine, stats, filter_config)
    return stats


def stamp(engine, stats, filter_config=None):
    """发布这轮 ETL 的内容代、revision 与父帖筛选 readiness。

    后端进程把扫描结果按区间缓存，而缓存只在 `meta_kv` 变了的时候才丢。ETL 只重建
    事实表，因此必须在这里显式刷新数据/AI 输入 revision、标记合成为 dirty，并在事实
    自检通过后重新发布与当前配置 digest 绑定的 readiness。

    代使用规范化事实内容的 SHA-256，而不是时间戳。相同输入重跑仍得到同一个值；行数
    不变但正文、ticker 或回复内容改变时，代也会改变，避免后端继续服务旧缓存。
    """
    filter_config = require_exact_rule_version(
        filter_config or load_comment_filter_config()
    )
    generation = {
        **stats,
        "feed_mentions": stats.get("feed_mentions", 0),
        "unresolved_source_tickers": stats.get("unresolved_source_tickers", 0),
    }
    generation["content_digest"] = stats.get("content_digest") or hashlib.sha256(
        json.dumps(
            generation,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    row = {
        "k": "etl_generation",
        "v": (
            "feeds={feeds} comments={comments} mentions={mentions} "
            "feed_mentions={feed_mentions} unresolved_source_tickers="
            "{unresolved_source_tickers} broken={broken} content_sha256={content_digest}"
        ).format(**generation),
    }
    with engine.begin() as conn:
        # 单键改写。两个方言的 upsert 语法不通用，delete + insert 在事务里等价且可移植。
        conn.execute(delete(meta_kv).where(meta_kv.c.k == row["k"]))
        conn.execute(insert(meta_kv).values(**row))
        return finalize_parent_filter(
            conn,
            filter_config,
            activate=generation["unresolved_source_tickers"] == 0,
            status_key=COMMENT_FILTER_STATUS_META_KEY,
            clear_backfill_state=True,
        )


def _flush(conn, fbuf, cbuf, mbuf, fmbuf):
    if fbuf:
        conn.execute(insert(feeds), fbuf)
    if cbuf:
        conn.execute(insert(comments), cbuf)
    if mbuf:
        conn.execute(insert(mentions), mbuf)
    if fmbuf:
        conn.execute(insert(feed_mentions), fmbuf)
    conn.commit()


def main(argv=None):
    ap = argparse.ArgumentParser(description="src_* → feeds/comments/mentions/users")
    ap.add_argument("--batch", type=int, default=BATCH)
    args = ap.parse_args(argv)

    engine = make_engine(bulk=True)
    stats = run(engine, args.batch)
    log(
        f"完成：帖子 {stats['feeds']:,}（坏 raw_json {stats['broken']:,}，"
        f"{stats['broken'] / max(stats['feeds'], 1):.2%}）、"
        f"评论 {stats['comments']:,}、提及 {stats['mentions']:,}"
        f"、筛选提及 {stats['feed_mentions']:,}"
        f"、未解析来源 ticker {stats['unresolved_source_tickers']:,}"
        f"、退休旧评论任务 {stats['superseded_comment_jobs']:,}"
    )


if __name__ == "__main__":
    main()
