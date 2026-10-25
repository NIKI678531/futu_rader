# ADR-0038 — 半小时采集、小时级 D0 分析与成功发布

- **状态**：已接受
- **日期**：2026-10-26
- **补充**：[ADR-0035](0035-current-data-day-range.md)

## 背景

MarketInsight 每半小时更新一次，但 D0 面向业务用户只需要每小时刷新一次。原任务在香港时间
13:00、15:00 把源同步、行情同步和 D0 入队串在一起，既没有利用半小时数据水位，也没有
整轮运行时限。业务页的版本还会被 worker 心跳和处理中间 revision 推动，可能在一轮 AI
尚未完成时反复刷新。

## 决策

1. `RADAR_INTRADAY_SYNC_CRON` 独立执行源库增量同步，生产为 `5,35 * * * *`；run id 包含
   分钟，整点与半点不会互相去重。
2. `AI_INTRADAY_CRON` 每小时只提交一个 D0，生产为 `40 * * * *`。D0 继续使用持久化队列、
   最高优先级、active-key 去重和 follow-up 合并；行情失败不阻塞社区内容分析。
3. D0 的 `RunControl` 使用 `AI_INTRADAY_DEADLINE_SECONDS=1200`。达到软截止后不再发新 HTTP
   请求；结合 120 秒单请求超时和 30 秒收尾预算，配置校验保证硬上限不超过 30 分钟。
4. 只有完整成功的分析才在结束队列事务中推进 `ai_public_revision`。D0 同时记录
   `ai_intraday_published_slot` 与 `ai_intraday_published_through`。partial、超时和失败保持上一版。
5. `/version.dataRevision` 只依赖公开 revision 与产品目录，不再被采集报告、心跳或处理中间
   revision 推动。Admin 的进度与健康检查仍实时读取内部状态。
6. 现有指纹、annotation job 和 synthesis generation 继续负责增量复用；没有变化的输入不会
   重复调用模型。
7. D0 保持最高队列优先级。低优先级区间在请求边界发现更高优先级任务时以 `preempted`
   退出并重新入队；源 revision 变化的历史任务也重新规划续跑。D0 自身遇到下一轮源变化则
   终止为 partial，等待下一小时，避免同一小时无限追跑。

## 后果

- 源事实每半小时进入 Radar，业务页在完整分析成功后按小时自动换版。
- 当前一轮超过软截止时，不发布半成品；下一小时基于数据库最新状态重试。
- 手动或每日完整分析成功也推进公开 revision，确保对应业务结果可见。
- 页面重新打开时仍读取数据库当前事实；公开 revision 控制的是已打开页面的自动换版边界，
  不是对事实表建立一份时态快照。
