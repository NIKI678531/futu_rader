"""瘦库 schema —— 两层：dump 原样镜像（`src_*`）＋ ETL 事实表。

## 为什么分两层

`src_*` 是 dump 里那五张 futu 表的**逐字段镜像**，只做两件事：按产品池过滤、按时间窗过滤
（[ADR-0008](../docs/adr/0008-dump-import-and-slim-db.md) 的瘦库派生）。它保留 `raw_json`，
所以 ETL 改一次口径不用重扫 9 GB 原始 dump——重扫一次约 60 秒不算贵，但要求原始文件一直在，
而那个文件在 OneDrive 上、且不进 git。

事实表是 [ADR-0009](../docs/adr/0009-worker-scope.md) 定的四张：`feeds` / `comments` /
`mentions` / `users`。它们**只有事实，没有一个口径值**——热度、排名、环比、去重全部在
`backend/core/`（铁律 1）。

## NULL 的纪律

可缺失的计数字段一律 `nullable=True`，取不到写 NULL。**绝不写 0**（铁律 2）：

- `like_count` / `comment_count` / `image_count` 在源库是 `int NOT NULL`，所以事实表里也
  NOT NULL —— 它们是真的取到了。
- `share_count` / `browse_count` 只在 `raw_json` 里。实测近 3 万条帖子 100% 可得，但
  `raw_json` 有 0.01% 因源库 `text` 列 65535 字节截断而不是合法 JSON，那些行只能写 NULL。
  热度公式少了转发项就不该假装算得出来。

## 方言

SQLite（本地）与 MySQL 8（生产）共用。`raw_json` 在 MySQL 下必须是 `LONGTEXT`：
源库用的是 `TEXT`（65535 上限，且已经因此截断了一部分数据），我们没有理由复制这个缺陷。
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql

metadata = MetaData()

# 源库 raw_json 是 TEXT（65535），已实测有 0.01% 被静默截断成坏 JSON。我们用 LONGTEXT。
LONGTEXT = Text().with_variant(mysql.LONGTEXT(charset="utf8mb4"), "mysql")
MEDIUMTEXT = Text().with_variant(mysql.MEDIUMTEXT(charset="utf8mb4"), "mysql")


# ── 第一层：dump 镜像 ──────────────────────────────────────────────────────

src_stocks = Table(
    "src_stocks",
    metadata,
    Column("stock_id", BigInteger, primary_key=True, autoincrement=False),
    Column("ticker", String(20), nullable=False, unique=True),  # '03033.HK'
    Column("market", String(10), nullable=False),
    Column("instrument_type", String(20), nullable=False),  # 'etf' | 'stock'
    Column("name_zh", String(200)),
    Column("name_en", String(200)),
)

src_feeds = Table(
    "src_feeds",
    metadata,
    Column("feed_id", BigInteger, primary_key=True, autoincrement=False),
    Column("stock_id", BigInteger, nullable=False, index=True),
    Column("feed_type", Integer, nullable=False),
    Column("posted_at", DateTime, nullable=False, index=True),
    Column("author_uid", String(40)),  # 源库大量为 NULL，ETL 从 raw_json 回填
    Column("author_name", String(200)),
    Column("feed_title", MEDIUMTEXT),
    Column("content_text", MEDIUMTEXT),
    Column("like_count", Integer, nullable=False),
    Column("comment_count", Integer, nullable=False),
    Column("image_count", Integer, nullable=False),
    Column("raw_json", LONGTEXT, nullable=False),
    Column("scraped_at", DateTime, nullable=False),
)

src_users = Table(
    "src_users",
    metadata,
    Column("user_id", String(40), primary_key=True),
    Column("nick_name", String(200)),
    Column("follower_num", Integer),
    Column("following_num", Integer),
    Column("home_visitor_num", Integer),
    Column("sns_gender", Integer),
    Column("ip_region", String(80)),
    Column("self_description", MEDIUMTEXT),
    Column("scraped_at", DateTime, nullable=False),
)


# ── 第二层：ETL 事实表（ADR-0009） ────────────────────────────────────────

feeds = Table(
    "feeds",
    metadata,
    Column("feed_id", BigInteger, primary_key=True, autoincrement=False),
    # 挂载标的：帖子被采集时所属的个股讨论区（CONTEXT.md「挂载标的」）。
    # code 是产品池里的代码（'3033'），由 ticker '03033.HK' 去前导零得到。
    Column("code", String(10), nullable=False, index=True),
    Column("posted_at", DateTime, nullable=False, index=True),
    Column("feed_type", Integer, nullable=False),
    Column("author_uid", String(40), index=True),
    Column("author_name", String(200), index=True),
    Column("title", MEDIUMTEXT),
    Column("content", MEDIUMTEXT),
    # 源库 NOT NULL，确实取到了。
    Column("like_count", Integer, nullable=False),
    Column("comment_count", Integer, nullable=False),
    Column("image_count", Integer, nullable=False),
    # 只在 raw_json 里。raw_json 坏掉时写 NULL —— 热度公式少了转发项，就该说少了。
    Column("share_count", Integer),
    Column("browse_count", Integer),
    # 我们实际解析到几条评论，以及上游是不是分页截断了（ADR-0011 两套口径靠这两列区分）。
    Column("comments_parsed", Integer),
    Column("comments_truncated", Boolean),
    Column("original_lang", Integer),  # 摘要「随原文语言」用（ADR-0010）
    # raw_json 不是合法 JSON（源库 TEXT 截断）。这一列为真时，上面几个 NULL 是有解释的。
    Column("raw_json_broken", Boolean, nullable=False, default=False),
    Index("ix_feeds_code_posted", "code", "posted_at"),
)

comments = Table(
    "comments",
    metadata,
    Column("comment_id", BigInteger, primary_key=True, autoincrement=False),
    Column("feed_id", BigInteger, nullable=False, index=True),
    Column("posted_at", DateTime, index=True),
    Column("author_uid", String(40), index=True),
    Column("author_name", String(200)),
    Column("content", MEDIUMTEXT),
    Column("like_count", Integer),
    Column("reply_to_comment_id", BigInteger),  # 0 在源里表示「不是回复」→ ETL 写 NULL
)

mentions = Table(
    "mentions",
    metadata,
    Column("feed_id", BigInteger, primary_key=True, autoincrement=False),
    Column("code", String(10), primary_key=True),
    # 'anchor' = 挂载标的（帖子所属讨论区）；'body' = 正文提及（raw_json 的 rich_text）。
    # 两者语义不同，市场域与账号域的提及口径也不同（CONTEXT.md「提及」），不能合并成一列。
    Column("source", String(10), primary_key=True),
    Column("in_pool", Boolean, nullable=False),  # 是否属于 120 只产品池
)

users = Table(
    "users",
    metadata,
    Column("user_id", String(40), primary_key=True),
    Column("nick_name", String(200), index=True),
    Column("follower_num", Integer),
    Column("following_num", Integer),
    Column("ip_region", String(80)),
    Column("self_description", MEDIUMTEXT),
)

# ── 第三层：AI 标注（ADR-0017 取代 ADR-0010 的单表形态） ──────────────────
#
# ADR-0010 定的是一张 `annotations` 单表。它撑不住生产标注，原因有三条，每条都会让
# 读数在界面上说谎：
#
# 1. **判定单元不对。** 一条评论可以同时评价两只 ETF，且对 A 积极、对 B 消极
#    （runbook §10.1）。按 `(target_type, target_id, kind)` 存，第二只产品的态度要么
#    覆盖第一只，要么变成一行分不清说谁的记录。判定单元必须是 `(comment_id, subject_code)`。
# 2. **没有版本，就没法重跑。** 换模型、改 Prompt、改标签体系之后，旧行和新行长得一样，
#    既不能比较也不能回滚。model / prompt / taxonomy / schema 四个版本必须落在行上。
# 3. **没有证据，结论就不可回到原文。** 「完成定义」要求每个 AI 结论都能指回原文片段
#    （runbook §19）。
#
# 拆成五张表：runs（一次批处理）、jobs（待办队列）、annotations（结论）、
# evidence（原文片段）、review_decisions（人工复核）。

# runbook §10.3：SQLite 的自动行号**只认精确的 `INTEGER PRIMARY KEY`**。`BigInteger`
# 在 SQLite 下渲染成 `BIGINT`，那不是 rowid 别名 —— autoincrement 会**静默失效**，
# 第二行插入就撞主键。MySQL 侧仍然要 BIGINT（标注行数会远超 int 范围）。
AUTO_PK = BigInteger().with_variant(Integer, "sqlite")

# 帖子级标注（post_type / summary / direction）不针对某只产品，但 `subject_code` 不能用
# NULL 表示「不针对产品」：SQL 的唯一约束里 **NULL 互不相等**，两个 NULL 行都能插进去，
# 幂等就废了。用空串当哨兵，并在应用层统一归一化（见 worker/ai/schemas.py 的 NO_SUBJECT）。
NO_SUBJECT = ""

annotation_runs = Table(
    "annotation_runs",
    metadata,
    Column("run_id", String(40), primary_key=True),  # 应用生成的 ULID
    Column("task", String(40), nullable=False),  # 'comment_product' | 'post_annotation'
    Column("provider", String(40), nullable=False),  # 'openai_compatible' | 'local_qwen'
    # 供应商**返回的**模型名，不是我们请求的那个。两者不一致时以返回值为准：
    # 网关会做别名转发，请求 'gpt-5.6-luna' 实际跑的可能是另一个快照。
    Column("model_id", String(80), nullable=False),
    Column("model_revision", String(80)),
    Column("prompt_version", String(40), nullable=False),
    Column("taxonomy_version", String(40), nullable=False),
    Column("schema_version", String(40), nullable=False),
    Column("started_at", DateTime, nullable=False),
    Column("finished_at", DateTime),
    Column("status", String(20), nullable=False),  # 'running' | 'done' | 'failed'
    Column("input_count", Integer, nullable=False, default=0),
    Column("success_count", Integer, nullable=False, default=0),
    Column("error_count", Integer, nullable=False, default=0),
    # 用量来自响应的 usage 块。取不到写 NULL —— 成本算不出来就该说算不出来（铁律 2）。
    Column("token_input", BigInteger),
    Column("token_output", BigInteger),
    Column("token_reasoning", BigInteger),  # 推理模型的思考 token，计费但不在输出里
    Column("estimated_cost", Float),
    Index("ix_runs_task_started", "task", "started_at"),
)

annotation_jobs = Table(
    "annotation_jobs",
    metadata,
    Column("job_id", AUTO_PK, primary_key=True, autoincrement=True),
    Column("target_type", String(10), nullable=False),  # 'feed' | 'comment'
    Column("target_id", BigInteger, nullable=False),
    Column("subject_code", String(10), nullable=False, default=NO_SUBJECT),
    Column("task", String(40), nullable=False),
    # 输入指纹：正文＋上下文＋产品＋模型＋prompt/taxonomy/schema 版本（runbook §11.3）。
    # 正文改了或版本换了 ⇒ hash 变 ⇒ 是一件**新的**待办，不是重复。
    Column("input_hash", String(64), nullable=False),
    Column("status", String(20), nullable=False),  # pending|claimed|done|failed|dead
    Column("priority", Integer, nullable=False, default=0),
    Column("attempts", Integer, nullable=False, default=0),
    # 租约：worker 崩溃后任务不能永远卡在 claimed。过期即可被重新领取。
    Column("claimed_at", DateTime),
    Column("lease_until", DateTime),
    Column("last_error", Text),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    # 这条待办属于哪次「按 ETF × 时间段」的抽取（`analysis_scopes`）。`run(scope_id=…)`
    # 只领本 scope 的任务 —— 没有它，跑 3033 近 7 天时会把队列里别的产品、别的日期一起领走。
    # 可空：Gate 0–2 的影子任务没有 scope。
    Column("scope_id", String(40), index=True),
    Column("stage", String(10), nullable=False, server_default="student"),
    # 同一个 (目标, 产品, 任务, 输入指纹) 只该有一条待办。重复排队 = 重复付费。
    UniqueConstraint(
        "target_type", "target_id", "subject_code", "task", "input_hash",
        name="uq_jobs_target_input",
    ),
    Index("ix_jobs_claimable", "status", "priority", "job_id"),
    Index("ix_jobs_task_stage_status", "task", "stage", "status"),
)

worker_events = Table(
    "worker_events", metadata,
    Column("event_id", AUTO_PK, primary_key=True, autoincrement=True),
    Column("ts", DateTime, nullable=False),
    Column("level", String(10), nullable=False),
    Column("stage", String(12), nullable=False),
    Column("code", String(10)),
    Column("scope_id", String(40)),
    Column("run_id", String(40)),
    Column("message", Text, nullable=False),
    Column("data_json", Text),
    Index("ix_worker_events_ts", "ts"),
)

# 一次「按 ETF × 时间段」的抽取范围（`worker/jobs/extract.py`）。
#
# `annotation_runs` 记的是**一次执行**，`analysis_scopes` 记的是**一个业务窗口**：
# 「3033 与 7226，2026-06-01 到 08-25，评论任务」。同一个 scope 可以被多次 run 分几天跑完，
# 也可以在中断后续跑；覆盖率（候选多少、剔了多少、标了多少）挂在 scope 上，不挂在 run 上。
analysis_scope_jobs = Table(
    "analysis_scope_jobs", metadata,
    Column("scope_id", String(40), primary_key=True),
    Column("job_id", BigInteger, primary_key=True),
)

runtime_leases = Table(
    "runtime_leases", metadata,
    Column("name", String(80), primary_key=True),
    Column("owner", String(40), nullable=False),
    Column("expires_at", DateTime, nullable=False),
)

source_snapshots = Table(
    "source_snapshots", metadata,
    Column("feed_id", BigInteger, primary_key=True),
    Column("source", String(80), nullable=False),
    Column("input_hash", String(64), nullable=False),
    Column("payload_json", LONGTEXT, nullable=False),
    Column("observed_at", DateTime, nullable=False),
)

analysis_scopes = Table(
    "analysis_scopes",
    metadata,
    Column("scope_id", String(40), primary_key=True),  # 时间前缀＋随机尾，同 run_id
    Column("task", String(40), nullable=False),
    Column("codes_json", Text, nullable=False),  # ["3033","7226"]
    Column("date_from", DateTime, nullable=False),  # 闭区间起
    Column("date_to", DateTime, nullable=False),  # 闭区间止（实现用半开 < to+1d）
    Column("time_basis", String(20), nullable=False),  # 'feed_posted_at'（市场域口径）
    Column("with_baseline", Boolean, nullable=False, default=False),
    Column("prompt_version", String(40), nullable=False),
    Column("taxonomy_version", String(40), nullable=False),
    Column("schema_version", String(40), nullable=False),
    # 抽取时的统计快照：候选数、各规则剔除数、可复用数、新排队数、token 估算……
    # 是 JSON 因为这些键会随规则演进而变，而它们只用来给人看与做报表。
    Column("stats_json", Text),
    Column("created_at", DateTime, nullable=False),
)

# 产品 × 区间级的 AI 生成物（热议总结、舆情总结、主题命名、负面类别、阶段观点、话题、竞品原因）。
#
# 与 `annotations` 分表，因为判定单元不同：那边是「一条评论对一只产品」，这边是
# 「一只产品在一个区间」。硬塞进 `annotations` 要把 `target_id`（BigInteger）挪用成产品代码、
# 把 `subject_code` 挪用成区间 —— 两列都会失去原义。
#
# `input_fingerprint` 覆盖：参与生成的 annotation_id 集合＋core/ 算出的事实 JSON＋Prompt 版本。
# 底层标注一变（重跑、reject）指纹就变 ⇒ 旧生成物自动失效、下次 synthesize 重生成。
# 这也是为什么不能只拿 `d7` 当缓存键：同一个 `d7` 在不同锚点、不同标注版本下是不同的输入。
synthesis_outputs = Table(
    "synthesis_outputs",
    metadata,
    Column("synthesis_id", AUTO_PK, primary_key=True, autoincrement=True),
    Column("code", String(10), nullable=False),
    Column("range_key", String(10), nullable=False),  # d1|d2|d7|d14|d30
    Column("anchor", String(10), nullable=False),  # 'YYYY-MM-DD'，来自 meta_kv
    # 'hot_summary'|'summary'|'theme_label'|'neg_category'|'stage'|'topic_label'|'competitor_reason'
    Column("kind", String(30), nullable=False),
    # 同一 kind 下的子键：主题是 '<polarity>|<aspect>'，阶段是 '<n>'，竞品是 '<code>'；
    # 单值 kind（hot_summary / summary）写 NO_SUBJECT。
    Column("subkey", String(40), nullable=False, default=NO_SUBJECT),
    Column("input_fingerprint", String(64), nullable=False),
    Column("value_json", Text, nullable=False),
    # 模型引用的证据 id（来自输入里带 id 的引文）。程序已校验它是输入 id 的子集。
    Column("evidence_ids_json", Text),
    Column("run_id", String(40), nullable=False),
    Column("review_state", String(20), nullable=False, default="pending"),
    Column("created_at", DateTime, nullable=False),
    Column("supersedes_id", BigInteger),
    UniqueConstraint(
        "code", "range_key", "anchor", "kind", "subkey", "input_fingerprint",
        name="uq_synthesis_unit",
    ),
    Index("ix_synthesis_lookup", "code", "range_key", "anchor", "kind"),
    Index("ix_synthesis_supersedes", "supersedes_id"),
)

annotations = Table(
    "annotations",
    metadata,
    Column("annotation_id", AUTO_PK, primary_key=True, autoincrement=True),
    Column("target_type", String(10), nullable=False),
    Column("target_id", BigInteger, nullable=False),
    # 判定单元的第二半（runbook §10.1）。帖子级标注写 NO_SUBJECT。
    Column("subject_code", String(10), nullable=False, default=NO_SUBJECT),
    # 'relevance'|'attitude'|'aspect'|'post_type'|'direction'|'summary'|'topic_label'|
    # 'neg_category'|'compliance'|'market_direction'（runbook §9）
    Column("kind", String(30), nullable=False),
    # 值统一存 JSON：aspect 是多标签、summary 是字符串、attitude 是枚举，
    # 一列 Text 存三种形状只会逼每个读取方各自猜一次。
    Column("value_json", Text, nullable=False),
    # **校准后**的概率。模型自报的 confidence 不是概率（runbook §11.1 末条），
    # 未校准时写 NULL —— 写模型自报值会让前端 0.7 阈值筛出一批没有意义的「高置信」。
    Column("calibrated_confidence", Float),
    Column("run_id", String(40), nullable=False),
    Column("input_hash", String(64), nullable=False),
    # 'pending' 模型写完 | 'needs_review' 模型自报存疑 | 'approved' 人工通过 |
    # 'rejected' 人工否决 | 'corrected' 人工改过
    # 它**不是发布门槛**（ADR-0019）：除 'rejected' 外全部直接上界面，这一列决定的是
    # 页面上挂哪一枚徽章（'needs_review' ⇒ 「AI 生成 · 待确认」）。发布规则的唯一实现
    # 处是 backend/providers/sql.py 的 `_current_annotations()`。
    Column("review_state", String(20), nullable=False, default="pending"),
    Column("created_at", DateTime, nullable=False),
    # 重跑产生的新行指向被它取代的旧行。**不删旧行**：模型失败或回滚时要能回到上一版
    # （runbook §11.3「模型失败不得覆盖旧的已确认结果」）。
    Column("supersedes_id", BigInteger),
    UniqueConstraint(
        "target_type", "target_id", "subject_code", "kind", "input_hash", "run_id",
        name="uq_annotations_unit",
    ),
    Index("ix_annotations_target", "target_type", "target_id", "kind"),
    Index("ix_annotations_subject", "subject_code", "kind", "review_state"),
    Index("ix_annotations_run", "run_id"),
    Index("ix_annotations_supersedes", "supersedes_id"),
    Index("ix_annotations_kind_target", "kind", "target_type"),
)

annotation_evidence = Table(
    "annotation_evidence",
    metadata,
    Column("evidence_id", AUTO_PK, primary_key=True, autoincrement=True),
    Column("annotation_id", BigInteger, nullable=False, index=True),
    Column("source_target_type", String(10), nullable=False),
    Column("source_target_id", BigInteger, nullable=False),
    # 字符偏移。**实测模型会把证据改写成转述**（Gate 0 首次调用即复现），
    # 所以偏移由程序在原文里定位后回填，不采信模型自报的位置；定位不到的整条判 needs_review。
    Column("start_offset", Integer),
    Column("end_offset", Integer),
    Column("quote_text", Text, nullable=False),
    Column("quote_hash", String(64), nullable=False),
    Index("ix_evidence_source", "source_target_type", "source_target_id"),
)

review_decisions = Table(
    "review_decisions",
    metadata,
    Column("decision_id", AUTO_PK, primary_key=True, autoincrement=True),
    Column("annotation_id", BigInteger, nullable=False, index=True),
    Column("reviewer", String(80), nullable=False),
    Column("decision", String(20), nullable=False),  # approve|reject|correct
    Column("corrected_value_json", Text),
    Column("reason_code", String(40)),
    Column("reviewed_at", DateTime, nullable=False),
)

# 导入产出的元信息。最要紧的是 anchor：真实数据止于 2026-08-26，「今天」必须取
# 数据最大日而不是系统时间，否则「近 7 天」是空的（ADR-0012 的锚点机制不变，值变）。
meta_kv = Table(
    "meta_kv",
    metadata,
    Column("k", String(60), primary_key=True),
    Column("v", Text),
)

price_instruments = Table(
    "price_instruments", metadata,
    Column("code", String(10), primary_key=True),
    Column("provider", String(20), nullable=False),
    Column("symbol", String(30), nullable=False),
    Column("currency", String(10), nullable=False),
    Column("exchange", String(20), nullable=False),
    Column("name", String(255)),
    Column("timezone", String(40), nullable=False),
    Column("verified_at", DateTime, nullable=False),
)

price_bars = Table(
    "price_bars", metadata,
    Column("code", String(10), primary_key=True),
    Column("provider", String(20), primary_key=True),
    Column("interval", String(10), primary_key=True),
    Column("timestamp", DateTime, primary_key=True),
    Column("adjustment", String(30), primary_key=True),
    Column("session_date", String(10), nullable=False),
    *(Column(field, Numeric(20, 8), nullable=False) for field in ("open", "high", "low", "close")),
    Column("volume", BigInteger),
    Column("fetched_at", DateTime, nullable=False),
)

price_syncs = Table(
    "price_syncs", metadata,
    Column("code", String(10), primary_key=True),
    Column("interval", String(10), primary_key=True),
    Column("date_from", String(10), primary_key=True),
    Column("date_to", String(10), primary_key=True),
    Column("status", String(30), nullable=False),
    Column("reason", String(80)),
    Column("row_count", Integer, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
