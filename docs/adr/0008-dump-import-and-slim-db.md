# ADR-0008 — 10 GB dump 的导入与瘦库派生

- **状态**：已接受，**导入路径于 2026-09-10 修订**（见文末「修订」）
- **日期**：2026-09-09
- **相关**：[ADR-0002](0002-mysql-over-clickhouse.md)、[ADR-0009](0009-worker-scope.md)、[ADR-0016](0016-sqlite-local-mysql-prod.md)

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

---

## 修订（2026-09-10）：导入路径改为流式解析直写瘦库

**产品决策**：不为一次性导入装一套 MySQL。原「全量导入本地 MySQL → SQL 派生瘦库」两步并成一步：**流式解析 dump，边读边按产品池与时间窗过滤，直接写 SQLite 瘦库**（本地 SQLite / 生产 MySQL 的分工见 [ADR-0016](0016-sqlite-local-mysql-prod.md)）。

上面「否决的备选」第 2 条正是这条路，理由是**静默丢数据**。那个理由没有被绕过，而是被逐条消解——实现见 `worker/jobs/dumpio.py`，其模块 docstring 与 `worker/tests/test_dumpio.py` 是这几条的落点：

| 原风险 | 消解方式 |
|---|---|
| 正则处理不了 `\'` 与 `\\`，撞上就静默截断 | 不用正则。字符级扫描，引号状态显式，反斜杠奇偶判断转义 |
| 少读一列 = 把字段里的逗号当成了分隔符 | `ColumnCountMismatch` **硬失败**，报到具体行号，不跳过 |
| 字符串未闭合就读到哪算哪 | `ValueError` 硬失败 |
| 块边界／文件末尾少一行没人发现 | `DumpReader.stats` 记读入与留存行数供对账；两处边界各有回归测试 |

**实际结果**：扫描 991,273 行，留下 **504,400 篇**帖子（120 只产品池 × 最近 120 天），耗时分钟级。产物写进 `meta_kv`（`imported_rows` / `dump_rows_scanned` / `source` / `window_from`），随时可对账。

### 导入后的两条验证：已跑，结果如下

1. **被截断的热帖占多少** —— 近 30 天（2026-07-27 ~ 08-25）147,477 篇帖子里，平台计数 `comment_count` 合计 **109,568**，我们实际解析出 **97,445** 条评论，覆盖率 **88.9%**；`comments_truncated` 为真的帖子 **2,824 篇（1.9%）**。数字写进 [ADR-0011](0011-comment-volume-caliber.md)。
2. **`sql` provider 会不会一片空白** —— 不会。全库 504,400 篇帖子、350,399 条评论、927,071 条提及、18,292 个用户；`LOW_SAMPLE=10` 对头部产品毫无压力（单只产品 7 天可有 6,000+ 条评论），对长尾产品仍会触发，这正是它该做的事。

### 顺带查明的两件事

- **`raw_json` 坏行占 167 / 504,400 ＝ 0.033%**（源库 `text` 列 65,535 字节截断）。它们的 `share_count` 只能写 NULL，后果见 [ADR-0011](0011-comment-volume-caliber.md) 与 `backend/providers/sql.py`。
- **官号／KOL 只能按名字认**：`master.json` 里官号 `url` 带的 user-id 在真实 `feeds.author_uid` 里一个都不存在（那是设计源生成的演示 id）。按全称匹配官号命中 **15/20**，按名字匹配 KOL 命中 **18/32**（其中 master 标了 `active` 的 26 位里命中 14 位）。这不是 bug，是主数据与真实数据尚未对齐——换正式名单时一并解决。

**隐私约束不变**：原始 dump 与瘦库都不进 git，也不要贴进 issue 或聊天。瘦库默认落在仓库树**外**（`%LOCALAPPDATA%\futu-radar\radar.db`），理由见 `radar_db/__init__.py`。
