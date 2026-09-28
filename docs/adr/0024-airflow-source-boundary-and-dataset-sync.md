# ADR-0024 — Airflow 负责社区采集，Radar 以源运行回执和游标同步

- **状态**：已接受
- **日期**：2026-09-25
- **扩展**：[ADR-0009](0009-worker-scope.md) 的 worker 边界；Radar 仍不直接访问富途社区
- **相关**：[ADR-0016](0016-sqlite-local-mysql-prod.md)、[ADR-0025](0025-online-fact-reconciliation-and-comment-coverage.md)、[ADR-0026](0026-scheduled-ai-budget-and-calibration-gate.md)

## 背景

Radar 原先只从本地历史库构造事实，生产社区数据则由另一套 Airflow 仓库中的
`all`、`important`、`feed details`、`users` 四类任务采集。若 Radar 自己再持有
Cookie 和网页协议，会形成两套采集实现；若仅把 Airflow Dataset 事件当成进度，
事件合并、重试或乱序又会让系统无法证明已经同步到哪一次采集。

`futu_api_check` 只验证 OpenD 行情接口。它成功不能证明社区网页可访问，也不能证明
登录 Cookie 有效。

## 决策

### 1. 两个系统只通过数据库事实衔接

- Airflow 与 collector 镜像负责访问社区网页、持有 Cookie，并把结果写入
  MarketInsight MySQL。
- Airflow 是生产环境唯一调度器；Radar worker 只提供可重入命令，生产定时不再另起一套
  常驻 scheduler。本地 CLI 仅用于验证、恢复和显式回放。
- Radar 的 `FutuRefresh` 只使用只读源库账号拉取数据；Radar 不访问社区网页，
  不保存 Cookie，也不在 DAG 中复制字段解析和业务规则。
- `FutuRefresh` 对外只暴露 `sync(SyncRequest) -> SyncResult` 与
  `analyze(AiRequest) -> AiResult`。JSONL 入口保留为恢复适配器，不是生产数据源。

这扩展了 ADR-0009，而不推翻它：当时否决的是“让 Radar worker 自己在线抓取”；
现在接入的是外部 collector 已落库的事实。

### 2. Dataset 是唤醒信号，源运行回执才是同步边界

四个社区采集任务成功后发布同一个
`market-insight://futu-community` Dataset。`futu_radar_sync` 被唤醒后，把生产者的
稳定 source run ID 传给 worker。

源表 `futu_comments_collection_runs` 记录采集类型、计划时段、状态、开始/结束时间、
high-watermark、完整日期与错误摘要；目标表 `ingestion_runs` 记录该 source run 的
同步结果。重复处理同一 source run 必须成为幂等 noop。

Dataset 事件可以被 Airflow 合并或重复投递，因此它不能代替上述回执，也不能代替
`collector_checkpoints`。同步开始时固定各数据流的 high-watermark；事实页与对应
checkpoint 在同一个目标库事务中提交，崩溃后只会从最后一页已提交位置继续。

### 3. 完整日期只有全量采集能够推进

只有成功扫描 collector **本次完整配置集合**的 `comments_all` 运行可以声明并推进
`source_complete_through`；回执必须保存配置数量与指纹，并证明每个配置标的都已尝试且
成功。Radar 随后只接纳其中属于自身 120 产品池的事实。`comments_important`、
`feed_details`、`users` 只能提高新鲜度，不能把一个日期宣称为完整。Radar 仅在该回执已经
完整同步后发布新的完整日期和锚点。

`futu_api_check` 保持独立且不发布社区 Dataset；另设无写入的社区登录态检查，专门在
Cookie 过期或网页会话不可用时失败并走现有告警回调。

### 4. 生产任务运行固定镜像，秘密只经 Secret 注入

采集 DAG 使用固定、不可变的 collector 镜像；同步与 AI DAG 使用固定、不可变的
Radar worker 镜像。源库、目标库、Cookie 和 AI 凭证由 Airflow Connection 或
Kubernetes Secret 注入，不放进 Bash 参数、DAG 源码或日志。

`futu_radar_sync` 限制 `max_active_runs=1` 并允许两次失败重试。互斥只是减少争用；
正确性仍由稳定 run ID、目标库租约、事务和 checkpoint 保证。

## 理由

- 网页协议、Cookie 与反爬变化只留在 collector 一侧，Radar 的业务事实转换可独立测试。
- 数据库回执能精确表达“哪一次采集、看到了哪个边界、是否完整”，而 Dataset 只能表达
  “有数据可能变化了”。
- 固定镜像消除每次任务现场 `pip install` 的依赖漂移，也避免分布式 worker 间共享
  `/tmp` 的错误假设。
- 将 OpenD 和社区会话分开监控，告警才能指向真实故障域。

## 后果

- MarketInsight 必须维护 `futu_comments_collection_runs`（含配置数量／指纹和实际覆盖数），
  并为 Radar 提供只读账号。
- 两个仓库之间新增了显式数据契约；源 schema 或产品池配置变化时必须走显式 backfill，
  不能让旧 checkpoint 静默继续。
- Airflow 可重试和 Dataset 可重复投递，但不能通过清空目标状态来“重跑”。

## 否决的备选

- **Radar 直接抓社区网页**：复制 Cookie、反爬和存储逻辑，破坏 ADR-0009 的边界。
- **DAG 直接写 Radar 的 facts 表**：把规范化、幂等和 AI 失效规则散落进多个 DAG。
- **只看 Dataset 时间戳推进进度**：无法安全处理事件合并、乱序、失败重试与部分采集。
