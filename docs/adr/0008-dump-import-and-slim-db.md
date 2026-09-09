# ADR-0008 — 10 GB dump 的导入与瘦库派生

- **状态**：已接受
- **日期**：2026-09-09
- **相关**：[ADR-0002](0002-mysql-over-clickhouse.md)、[ADR-0009](0009-worker-scope.md)

## 背景

客户提供的 `dump-market_insight-202608261101-2.sql`：

- **10,324,670,435 字节（约 10.3 GB）**，压缩包 420 MB
- MySQL 8 生产 RDS 全量 dump，24 张表
- futu 相关 5 张表，约 **99 万个帖子**
- `futu_comments_stocks` 有 **323 只标的**（227 ETF ＋ 96 个股）
- **我们的 120 只产品（61 自家 ＋ 59 竞品）全部覆盖，一只不缺**
- 数据从 **2015 年**起（样例 `posted_at` 2015-09-22，`scraped_at` 2026-04-14）——历史全量回填
- 含真实用户昵称、IP 归属地、个人简介

演示期真正用得上的只有极小一撮：323 只里只有 120 只是我们的，11 年历史里只用得上最近 30 天（PRD §3.1 最长预设区间）。

## 决策

**三步：全量导入 → SQL 派生瘦库 → 项目只挂瘦库。**

1. **一次性全量导入**本地 MySQL。导入前先把 dump **从 OneDrive 拷到本地 SSD**（同步盘的按需下载会拖死 10 GB 顺序读）。导入时关 binlog、`innodb_flush_log_at_trx_commit=0`、调大 `max_allowed_packet`——一次性成本。
2. **SQL 派生瘦库**：120 只标的 × 最近 N 天的帖子，外加从 `raw_json` 抽出的原始事实表（见 [ADR-0009](0009-worker-scope.md)）。
3. 项目 compose 只挂瘦库。原始全量库是本机一次性产物，不入项目。

**原始 dump 与瘦库都不进 git**：`.gitignore` 加 `*.sql` / `*.7z`；dump 路径走 `.env` 的 `DUMP_PATH`，`.env.example` 里只写占位。

## 导入后立刻要跑的两条验证

这两个数现在是未知的，靠正则在 10 GB 上猜是错的工具；导入后各一句 SQL：

1. **被截断的热帖占评论总量多少** —— `SELECT SUM(comment_count), SUM(JSON_LENGTH(raw_json,'$.comment.comment_items')) FROM futu_comments_feeds`。决定 [ADR-0011](0011-comment-volume-caliber.md) 的两套口径差多远。
2. **最近 30 天里 120 只 ETF 各有多少帖子／评论** —— 决定 `LOW_SAMPLE=10` 合不合适（见 [ADR-0013](0013-prd-open-items-o1-o8.md) O6），也决定 `mysql` provider 是不是一片空白。

## 后果

- 瘦库小到可以随时重建、可以拷给同事。
- 全量导入是小时级的一次性操作，写进 [README.md](../../README.md) 的首次搭建步骤。
- 瘦库同样含真实用户数据，**同样不进 git**，也不要随手贴进 issue 或聊天。

## 否决的备选

- **全量导入后直接查原始库**：每次查询都在 10 GB 上扫，且 99% 的行用不到。
- **导入前用脚本裁剪 dump**：要正则解析 INSERT 行，脆且容易静默丢数据——而静默丢数据在这个项目里是最难发现的一类错误。
