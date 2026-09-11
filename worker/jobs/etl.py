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
   ADR-0009 把它们列为多对多提及来源，实测不成立——正文提及只能走
   `summary.rich_text[].stock`。这一条**写进代码注释是为了下次别再去翻那三个字段**。
3. **`share_count` 在 `common` 与顶层都有，`feed_comm` 下没有**（feed_type 5 实测）。
   ADR-0009 说随 `feed_type` 换名，所以三处都试；三处都没有就写 NULL，不写 0（铁律 2）。
4. **约 0.09% 的 `raw_json` 不是合法 JSON**：源库那一列是 `text`（65535 字节上限），
   超长载荷被静默截断。这些行的 `share_count` / 评论 / 正文提及**全部取不到**，
   落 NULL 并把 `raw_json_broken` 置真——让「取不到」和「确实是零」在库里就分得开。
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "worker"))

from sqlalchemy import delete, insert, select  # noqa: E402

from jobs.import_dump import pool_codes, ticker_to_code  # noqa: E402
from radar_db import make_engine  # noqa: E402
from radar_db.schema import (  # noqa: E402
    comments,
    feeds,
    mentions,
    src_feeds,
    src_stocks,
    src_users,
    users,
)

BATCH = 2000


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def rich_text(items):
    """把富途的富文本段还原成纯文本。

    段类型实测只有 6 种：0=文本 1=表情 2=@用户 3=标的 6=大表情 7=带链接文本。
    表情与标的按上游 `content_text` 的写法还原成 `[捂脸]` 和 `$07709.HK$` ——
    这样评论正文和帖子正文是同一套写法，将来喂给标注模型时不用分两种 prompt。
    """
    if not items:
        return None
    out = []
    for seg in items:
        t = seg.get("type")
        if t == 0:
            out.append(seg.get("text") or "")
        elif t == 1:
            out.append(f"[{(seg.get('emotion') or {}).get('text', '')}]")
        elif t == 2:
            out.append("@" + ((seg.get("user") or {}).get("nick_name") or ""))
        elif t == 3:
            s = seg.get("stock") or {}
            code = s.get("stock_code") or ""
            mkt = (s.get("market_type_label") or "").upper()
            out.append(f"${code}.{mkt}$" if code else "")
        elif t == 6:
            out.append("[表情]")
        elif t == 7:
            out.append((seg.get("text_link") or {}).get("text") or "")
    text = "".join(out).strip()
    return text or None


def _first(*vals):
    """第一个不是 None 的值；全是 None 就返回 None（不是 0）。"""
    for v in vals:
        if v is not None:
            return v
    return None


def _ts(v):
    """富途的 unix 秒（字符串）→ naive datetime。跟 posted_at 一样用本地无时区形式。"""
    if v in (None, "", "0"):
        return None
    try:
        return datetime.fromtimestamp(int(v), tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return None


def _mention_code(stock):
    """正文提及的标的 → 产品池口径的 code。

    港股 `stock_code` 是 5 位补零（`'07709'`），与产品池的 `'7709'` 差前导零。
    非港股保留市场前缀，免得 `'AAPL'` 和某只港股 code 撞在一起。
    """
    code = stock.get("stock_code")
    if not code:
        return None
    mkt = (stock.get("market_type_label") or "").lower()
    if mkt == "hk":
        return code.lstrip("0") or "0"
    return f"{mkt}:{code}" if mkt else code


def run(engine, batch_size=BATCH):
    codes = pool_codes()

    with engine.connect() as conn:
        stock_code = {
            sid: ticker_to_code(ticker)
            for sid, ticker in conn.execute(select(src_stocks.c.stock_id, src_stocks.c.ticker))
        }

    with engine.begin() as conn:
        for tbl in (feeds, comments, mentions, users):
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

    n = broken = n_comments = n_mentions = 0
    seen_comment = set()
    fbuf, cbuf, mbuf = [], [], []
    t0 = time.time()

    src = engine.connect().execution_options(stream_results=True, yield_per=500)
    out = engine.connect()
    try:
        for row in src.execute(select(src_feeds)):
            n += 1
            code = stock_code[row.stock_id]

            j = None
            try:
                j = json.loads(row.raw_json)
            except (ValueError, TypeError):
                broken += 1

            common = (j or {}).get("common") or {}
            feed_comm = (j or {}).get("feed_comm") or {}
            comment = (j or {}).get("comment") or {}
            user_info = (j or {}).get("user_info") or {}

            items = comment.get("comment_items") or []
            fbuf.append(
                dict(
                    feed_id=row.feed_id,
                    code=code,
                    posted_at=row.posted_at,
                    feed_type=row.feed_type,
                    # 列几乎全空，身份靠 raw_json 回填（实测 99.4% 的列为 NULL）。
                    author_uid=row.author_uid or user_info.get("user_id"),
                    author_name=row.author_name or user_info.get("nick_name"),
                    title=row.feed_title,
                    content=row.content_text,
                    like_count=row.like_count,
                    comment_count=row.comment_count,
                    image_count=row.image_count,
                    # 三处都试；都没有就 NULL —— 热度公式少了转发项就该说少了。
                    share_count=_first(
                        (j or {}).get("share_count"),
                        common.get("share_count"),
                        feed_comm.get("share_count"),
                    ),
                    browse_count=_first(
                        (j or {}).get("browse_count"), common.get("browse_count")
                    ),
                    # raw_json 坏掉时「我们解析到几条评论」是未知，不是 0。
                    comments_parsed=None if j is None else len(items),
                    comments_truncated=None if j is None else bool(comment.get("has_more")),
                    original_lang=common.get("original_lang") if j is not None else None,
                    raw_json_broken=j is None,
                )
            )

            # ── 提及 ──────────────────────────────────────────────
            # 挂载标的：帖子被采集时所属的讨论区。只导入了产品池，所以必然 in_pool。
            mbuf.append(dict(feed_id=row.feed_id, code=code, source="anchor", in_pool=True))
            body = set()
            for seg in ((j or {}).get("summary") or {}).get("rich_text") or []:
                s = seg.get("stock")
                if not s:
                    continue
                c = _mention_code(s)
                if c and c != code:
                    body.add(c)
            for c in body:
                mbuf.append(
                    dict(feed_id=row.feed_id, code=c, source="body", in_pool=c in codes)
                )

            # ── 评论 ──────────────────────────────────────────────
            for it in items:
                cid = it.get("comment_id")
                if not cid:
                    continue
                cid = int(cid)
                # 同一条评论可能同时出现在 comment_items 与 popular_comments，
                # 也可能被两条帖子引用。评论条数是 ADR-0011 的口径基数，重复计数会直接
                # 污染它，所以在事实层就去重。
                if cid in seen_comment:
                    continue
                seen_comment.add(cid)
                author = it.get("author") or {}
                reply_to = it.get("reply_to_comment_id")
                reply_to = int(reply_to) if reply_to not in (None, "", "0", 0) else None
                cbuf.append(
                    dict(
                        comment_id=cid,
                        feed_id=row.feed_id,
                        posted_at=_ts(it.get("timestamp")),
                        author_uid=author.get("user_id"),
                        author_name=author.get("nick_name"),
                        content=rich_text(it.get("rich_text_items")),
                        like_count=(it.get("like") or {}).get("liked_num"),
                        reply_to_comment_id=reply_to,
                    )
                )

            if len(fbuf) >= batch_size:
                n_comments += len(cbuf)
                n_mentions += len(mbuf)
                _flush(out, fbuf, cbuf, mbuf)
                fbuf, cbuf, mbuf = [], [], []
                log(f"  {n:,}/504,400 帖（{time.time() - t0:.0f}s）")

        n_comments += len(cbuf)
        n_mentions += len(mbuf)
        _flush(out, fbuf, cbuf, mbuf)
    finally:
        src.close()
        out.close()

    return {"feeds": n, "broken": broken, "comments": n_comments, "mentions": n_mentions}


def _flush(conn, fbuf, cbuf, mbuf):
    if fbuf:
        conn.execute(insert(feeds), fbuf)
    if cbuf:
        conn.execute(insert(comments), cbuf)
    if mbuf:
        conn.execute(insert(mentions), mbuf)
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
    )


if __name__ == "__main__":
    main()
