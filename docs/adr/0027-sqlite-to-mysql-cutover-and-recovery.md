# ADR-0027 — 生产切换采用可验证的 SQLite → MySQL 流式迁移与影子对账

- **状态**：已接受
- **日期**：2026-09-25
- **扩展**：[ADR-0016](0016-sqlite-local-mysql-prod.md) 的生产 MySQL 决策
- **相关**：[ADR-0024](0024-airflow-source-boundary-and-dataset-sync.md)、[ADR-0025](0025-online-fact-reconciliation-and-comment-coverage.md)

## 背景

现有约 5.5 GB SQLite 不只是四张 facts 表，还包含 AI 运行、任务、现行结论、合成状态和
数据版本。直接把新服务指向一个空 MySQL 会丢失历史；复制一个正在写入的 SQLite 会得到
跨表不一致快照；把进程租约和 claimed 状态原样搬到另一台机器，又会制造永不释放的任务。

ADR-0016 规定本地 SQLite、生产 MySQL，但没有规定已有运行中数据库如何安全切换。

## 决策

### 1. 迁移前冻结并备份，目标必须先到 Alembic head

先停止旧 backend/worker 写入，对 SQLite 做字节级备份并记录大小与 SHA-256。目标必须是
独立于 Airflow metadata DB 的空 MySQL 8 数据库，并先执行当前 Alembic `upgrade head`。

生产迁移命令只接受 SQLite 源与 MySQL 目标；连接串从环境／Secret 读取，不作为 CLI 参数。
默认拒绝非空目标，`--resume` 只用于同一次中断复制的续跑。

### 2. 逐表流式复制持久状态，排除机器级租约

迁移按批流式复制所有源库中存在的持久表，包括 facts、AI 历史、进度、revision、采集
checkpoint、ingestion run、每日预算和计数观察。`runtime_leases` 属于进程／机器所有权，
明确跳过。

复制完成并通过校验后，将中断时仍为 `claimed` 的 AI job 释放为 `pending`，清除其租约时间；
已完成 job、annotation 链、`data_revision` 与 AI source version 均保持不变。

### 3. 行数全比对，主键顺序样本做确定性哈希

每张持久表比较源／目标行数；再按主键稳定排序，对共同列的确定性样本比较 SHA-256。
任何一张表不一致都中止切换。哈希是抽样防线，不替代全表行数，也不宣称是全库内容证明。

### 4. 先影子 reconciliation，再一次性切换读写方

迁移后先对 MarketInsight 做一次全量 reconciliation 的 dry-run，再实际发布并建立三个
增量游标。核对迁移报告、API smoke test 和首次同步结果后，才把 Flask、worker 与 Airflow
一起切到目标 MySQL。SQLite 备份保留为只读回滚源，直到首个 Dataset 同步验收完成。

## 理由

- 空目标和 Alembic head 消除“边复制边猜 schema”的不可验证状态。
- 流式批处理不要求把 5.5 GB 数据一次装入内存。
- 租约不能跨机器继承，但业务进度与版本必须继承；二者分开处理才既能恢复又不重复推理。
- 迁移校验只能证明历史被复制，源库 reconciliation 才能证明在线数据链路也接通。

## 后果

- 切换窗口内旧 SQLite 必须停止写入；这是一次有计划的短暂停写，而不是在线双写迁移。
- MySQL 目标不得与 Airflow metadata 共库，权限、备份和故障域分别管理。
- 回滚以保留的 SQLite 备份为准；不能把已经运行过的新 MySQL 反向覆盖旧文件。

## 否决的备选

- **应用启动时自动复制**：没有冻结点、可观察进度和验收门槛。
- **迁移租约与 claimed 状态**：把旧进程所有权错误带到新运行环境。
- **只检查几张大表的行数**：AI 历史、预算或 checkpoint 丢失仍可能悄悄上线。
- **迁移完成立刻切流量**：无法区分迁移正确与在线源适配器正确。

