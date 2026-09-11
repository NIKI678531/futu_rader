"""dump → 瘦库的 `src_*` 镜像层（[ADR-0008](../../docs/adr/0008-dump-import-and-slim-db.md)）。

    python -m jobs.import_dump --dump "<path>/dump-market_insight-....sql"

## 两遍扫描，不是一遍

按产品池过滤 feeds 需要先有 `ticker → stock_id` 映射，而 `futu_comments_stocks` 在 dump 里
排在 `futu_comments_feeds` **后面**（mysqldump 按表名字母序写，9.0 GB 的 feeds 在前）。
所以：

1. **第一遍**只取 `futu_comments_stocks`（51 KB）与 `futu_comments_users`（10.8 MB）。
   feeds 那 9 GB 连 UTF-8 解码都不做（见 `dumpio._lines`），成本是纯 I/O。
2. **第二遍**取 feeds，用第一遍的 stock_id 集合 ＋ 时间窗当场过滤。

一遍扫完再删的做法也能work，但那要先把整个时间窗内 323 只标的的 `raw_json` 全落盘
（GB 级），再删掉不要的。多花一遍纯 I/O 比多写几 GB 便宜。

## 产品池从哪来

61 自家 ＋ 59 竞品是**客户维护的主数据**，当前仓库里唯一一份在
`backend/fixtures/demo/master.json`。实测这 120 个代码在 dump 的 323 只标的里
**一只不缺**。将来客户给正式名单时换掉这个来源即可，其余不变。

## 时间窗

真实数据止于 **2026-08-26**（dump 打包日）。PRD §3.1 最长预设区间是近 30 天，环比要再往前
一个等长区间，所以 60 天是下限；默认取 120 天留余量。锚点（「今天」）取导入后实测的
最大 `posted_at`，写进 `meta_kv`，由 `/meta` 下发——前端不自算（ADR-0012）。
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "worker"))

from sqlalchemy import delete, func, insert, select  # noqa: E402

from jobs.dumpio import DumpReader, to_python  # noqa: E402
from radar_db import create_all, db_url, make_engine  # noqa: E402
from radar_db.schema import meta_kv, src_feeds, src_stocks, src_users  # noqa: E402

# dump 里的列数，取自各表的 CREATE TABLE。**写死是故意的**：列数变了要炸，
# 不能靠解析器「自适应」——那正是静默丢数据的入口。
FEEDS_COLS = 14
STOCKS_COLS = 10
USERS_COLS = 13

MASTER = REPO_ROOT / "backend" / "fixtures" / "demo" / "master.json"
BATCH = 2000


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def pool_codes(path=MASTER):
    """产品池的 120 个代码。返回 ``{code: ownership}``。"""
    with open(path, encoding="utf-8") as fh:
        master = json.load(fh)
    return {p["code"]: p["ownership"] for p in master["products"]}


def ticker_to_code(ticker):
    """``'03033.HK'`` → ``'3033'``。实测 120/120 与产品池对得上。"""
    head = ticker.split(".", 1)[0]
    return head.lstrip("0") or "0"


def _dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S") if s else None


def _int(v):
    return None if v is None else int(v)


def pass1(dump, engine, codes, all_stocks):
    """第一遍：stocks ＋ users。返回要保留的 ``stock_id`` 集合。"""
    reader = DumpReader(
        dump,
        {"futu_comments_stocks": STOCKS_COLS, "futu_comments_users": USERS_COLS},
    )
    keep_ids = set()
    matched = {}
    stock_rows, user_rows = [], []

    with engine.begin() as conn:
        conn.execute(delete(src_stocks))
        conn.execute(delete(src_users))

        for table, fields in reader.rows():
            row = to_python(fields)
            if table == "futu_comments_stocks":
                stock_id = int(row[0])
                ticker = row[1]
                code = ticker_to_code(ticker)
                in_pool = code in codes
                if in_pool:
                    matched[code] = ticker
                if in_pool or all_stocks:
                    keep_ids.add(stock_id)
                stock_rows.append(
                    dict(
                        stock_id=stock_id,
                        ticker=ticker,
                        market=row[2],
                        instrument_type=row[3],
                        name_zh=row[4],
                        name_en=row[5],
                    )
                )
            else:
                user_rows.append(
                    dict(
                        user_id=row[0],
                        nick_name=row[1],
                        follower_num=_int(row[2]),
                        following_num=_int(row[3]),
                        home_visitor_num=_int(row[4]),
                        sns_gender=_int(row[5]),
                        ip_region=row[6],
                        self_description=row[7],
                        scraped_at=_dt(row[11]),
                    )
                )
                if len(user_rows) >= BATCH:
                    conn.execute(insert(src_users), user_rows)
                    user_rows = []

        if stock_rows:
            conn.execute(insert(src_stocks), stock_rows)
        if user_rows:
            conn.execute(insert(src_users), user_rows)

    missing = sorted(set(codes) - set(matched))
    log(
        f"第一遍完成：标的 {reader.stats['futu_comments_stocks']['read']} 只，"
        f"用户 {reader.stats['futu_comments_users']['read']} 人；"
        f"产品池命中 {len(matched)}/{len(codes)}"
    )
    if missing:
        # 硬失败而不是警告：少一只产品，那只产品在页面上会「看起来没人讨论」——
        # 而那和「确实没人讨论」在界面上长得一模一样（铁律 2 的失败模式）。
        raise SystemExit(f"产品池有 {len(missing)} 只在 dump 里找不到：{missing}")
    return keep_ids


def anchor_of(conn):
    """锚点＝**最近一个完整自然日**，不是最大 `posted_at` 那天。

    设计源的 `ANCHOR` 逐字是「最近一个完整自然日（HKT）」。实测这份 dump 的最大
    `posted_at` 是 `2026-08-26 03:00`——采集在当天上午 11:01 打包，所以 8-26 只有 3 小时
    数据（657 帖，前后几天都是 5000 上下）。拿它当锚点，「昨日」这个预设区间会显示成
    活跃度暴跌 87%，而那是个采集边界，不是舆情变化。

    判定：最大 `posted_at` 那天若没跑到 23 点，就退一天。返回 `(锚点日, 最大时间戳)`
    —— 两个都写进 `meta_kv`，好让「数据到哪」和「锚点在哪」分得开。
    """
    hi = conn.execute(select(func.max(src_feeds.c.posted_at))).scalar()
    hi = hi if isinstance(hi, datetime) else _dt(hi)
    day = hi.date()
    last = conn.execute(
        select(func.max(src_feeds.c.posted_at)).where(
            src_feeds.c.posted_at < datetime.combine(day, datetime.min.time())
            + timedelta(days=1),
            src_feeds.c.posted_at >= datetime.combine(day, datetime.min.time()),
        )
    ).scalar()
    last = last if isinstance(last, datetime) else _dt(last)
    return (day if last.hour >= 23 else day - timedelta(days=1)), hi


def pass2(dump, engine, keep_ids, cutoff):
    """第二遍：feeds，按 stock_id ＋ 时间窗过滤。"""
    reader = DumpReader(dump, {"futu_comments_feeds": FEEDS_COLS}, stop_when_past=True)
    cutoff_s = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    def keep(_table, fields):
        # fields[1]=stock_id（裸 token），fields[3]=posted_at（字符串切片）。
        # 在**反转义之前**判断，被丢掉的行连 raw_json 都不会被拷成 Python 字符串。
        return int(fields[1][1]) in keep_ids and fields[3][1] >= cutoff_s

    batch = []
    t0 = time.time()
    with engine.begin() as conn:
        conn.execute(delete(src_feeds))

    # 逐批提交，不是一个大事务：raw_json 是 GB 级的，攒在一个事务里 WAL 会涨到和库一样大。
    # synchronous=OFF 下每批提交的开销可以忽略。
    with engine.connect() as conn:
        for _table, fields in reader.rows(keep=keep):
            row = to_python(fields)
            batch.append(
                dict(
                    feed_id=int(row[0]),
                    stock_id=int(row[1]),
                    feed_type=int(row[2]),
                    posted_at=_dt(row[3]),
                    author_uid=row[4],
                    author_name=row[5],
                    feed_title=row[6],
                    content_text=row[7],
                    like_count=int(row[8]),
                    comment_count=int(row[9]),
                    image_count=int(row[10]),
                    raw_json=row[11],
                    scraped_at=_dt(row[12]),
                )
            )
            if len(batch) >= BATCH:
                conn.execute(insert(src_feeds), batch)
                conn.commit()
                batch = []
                s = reader.stats["futu_comments_feeds"]
                log(f"  已扫 {s['read']:,} 帖，留下 {s['kept']:,}（{time.time()-t0:.0f}s）")
        if batch:
            conn.execute(insert(src_feeds), batch)
            conn.commit()

    return reader.stats["futu_comments_feeds"]


def main(argv=None):
    ap = argparse.ArgumentParser(description="把 mysqldump 导入本地瘦库的 src_* 镜像层")
    ap.add_argument("--dump", default=os.getenv("DUMP_PATH"), help="dump 文件路径")
    ap.add_argument("--days", type=int, default=120, help="保留最近多少天的帖子")
    ap.add_argument(
        "--all-stocks",
        action="store_true",
        help="连产品池之外的 203 只标的也留下（默认只留 120 只）",
    )
    args = ap.parse_args(argv)

    if not args.dump:
        ap.error("需要 --dump 或环境变量 DUMP_PATH")
    dump = Path(args.dump)
    if not dump.exists():
        ap.error(f"dump 不存在：{dump}")

    codes = pool_codes()
    engine = make_engine(bulk=True)
    log(f"瘦库：{db_url()}")
    log(f"dump：{dump}（{dump.stat().st_size / 1e9:.1f} GB）")
    create_all(engine)

    keep_ids = pass1(dump, engine, codes, args.all_stocks)

    # 时间窗要相对**数据的最大日**，不是系统时间。真实数据止于 2026-08-26，
    # 按系统时间（2026-09-10）往回数 120 天会白白丢掉两周数据。
    # dump 文件的 mtime 是打包时间，够用来定这个上界；真正的锚点在导入完实测。
    ceiling = datetime.fromtimestamp(dump.stat().st_mtime)
    cutoff = ceiling - timedelta(days=args.days)
    log(f"第二遍：保留 {cutoff:%Y-%m-%d} 起、{len(keep_ids)} 只标的的帖子")

    stats = pass2(dump, engine, keep_ids, cutoff)

    with engine.begin() as conn:
        n, lo = write_meta(conn, dump, stats["read"])

    log(
        f"完成：扫 {stats['read']:,} 帖，留 {stats['kept']:,} 帖；"
        f"落库 {n:,} 行（{lo:%Y-%m-%d} … 见下）"
    )
    if n != stats["kept"]:
        # 对账。读了多少、留了多少、落了多少三个数不一致 ⇒ 静默丢数据，必须炸。
        raise SystemExit(f"对账失败：过滤后 {stats['kept']} 行，库里只有 {n} 行")


def write_meta(conn, dump, rows_scanned):
    """把锚点等元信息写进 `meta_kv`。导入完调用，也可单独重跑。"""
    anchor, hi = anchor_of(conn)
    lo, n = conn.execute(
        select(func.min(src_feeds.c.posted_at), func.count()).select_from(src_feeds)
    ).one()
    lo = lo if isinstance(lo, datetime) else _dt(lo)
    conn.execute(delete(meta_kv))
    conn.execute(
        insert(meta_kv),
        [
            # 「今天」＝最近一个完整自然日。前端不自算，由 /meta 下发（ADR-0012）。
            {"k": "anchor", "v": anchor.strftime("%Y-%m-%d")},
            {"k": "anchor_ts", "v": f"{anchor:%Y-%m-%d} 23:59:59"},
            # 数据实际到哪一刻。与 anchor 分开记：8-26 有 3 小时数据，但它不是完整一天。
            {"k": "data_max_ts", "v": hi.strftime("%Y-%m-%d %H:%M:%S")},
            {"k": "window_from", "v": lo.strftime("%Y-%m-%d")},
            {"k": "imported_rows", "v": str(n)},
            {"k": "dump_rows_scanned", "v": str(rows_scanned)},
            {"k": "source", "v": dump.name},
        ],
    )
    log(f"锚点 anchor = {anchor:%Y-%m-%d}（最近一个完整自然日；数据止于 {hi:%Y-%m-%d %H:%M}）")
    return n, lo


if __name__ == "__main__":
    main()
