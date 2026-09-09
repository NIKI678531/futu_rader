# ADR-0009 — worker 第一期职责：`raw_json` ETL ＋ 标注作业调度

- **状态**：已接受
- **日期**：2026-09-09
- **相关**：[ADR-0008](0008-dump-import-and-slim-db.md)、[ADR-0010](0010-annotations-and-ai-pipeline.md)

## 背景

`worker/` 现在只有一个 heartbeat 任务，`jobs/collect.py` 是占位。

而 dump 的 `raw_json` 列里装的是**富途 API 的完整原始载荷**，MySQL 列只抽了极小一部分。列里没有、JSON 里有的关键字段：

| 字段 | 位置 | 为什么重要 |
|---|---|---|
| `share_count` | `common.share_count` / `feed_comm.share_count`（随 `feed_type` 换名） | **热度公式的转发项**；没有它热度整体算不出来 |
| `browse_count` | 同上 | 浏览数 |
| `comment_items[]` | `comment.comment_items` | **评论正文**、评论作者、评论获赞、`reply_to_comment_id` |
| `summary.rich_text[].stock` | 帖子摘要结构 | **正文提及的结构化标的**（`stock_code` / `display_symbol`），上游已解析 |
| `all_related_stock_infos` / `stock_items` / `plate_ids` | 顶层 | 多对多提及关系 |
| `user_info.user_id` | 顶层 | `author_uid` **列大量为 NULL**，作者身份要从这里回填 |

## 决策

worker 第一期承担 **`raw_json` → 结构化原始事实表的 ETL**，产出四张表：

```
feeds      帖子：含从 JSON 抽出的 share_count / browse_count
comments   评论：正文、作者、时间、获赞、reply_to_comment_id
mentions   提及：feed_id × stock_code，来源标记（挂载 / 正文）
users      发帖人：user_id 从 raw_json 回填
```

外加调度 [ADR-0010](0010-annotations-and-ai-pipeline.md) 的标注作业。

**worker 只落事实，一个口径公式都不碰**——聚合、去重、排名、热度全部在 `backend/core/`（铁律 1）。

**明确不做**：真去富途在线采集。反爬、token、法务授权都不在第一期射程内。

## 理由

- 这段 ETL [ADR-0008](0008-dump-import-and-slim-db.md) 的派生步骤本来就要写。让它长在 worker 里而不是一次性脚本里，将来真采集接上时**原样复用**。
- 「拆 JSON 成原始表」严格符合「worker 只落原始数据」——它不产生任何指标。
- 若 worker 继续是空壳，它就是个白占资源的容器，且第一期结束时仍未被验证过。

## 后果

- `worker/requirements.txt` 去掉 `clickhouse-driver`，加 `SQLAlchemy` ＋ `PyMySQL`。
- `feed_type` 决定 `share_count` 在 `common` 还是 `feed_comm` 下——ETL 必须两个都试，且**取不到时写 NULL，不写 0**（铁律 2）。
- 96.5% 的帖子评论是全量嵌入的，3.5% 被上游分页截断（`has_more=1`，34,941 / 991,046）。ETL 要**如实记录截断标记**，供 [ADR-0011](0011-comment-volume-caliber.md) 的口径使用。

## 否决的备选

- **worker 继续空壳、第一期靠手工导入**：ETL 代码仍要写，只是写在没人维护的地方。
- **worker 真去采集**：见「明确不做」。
