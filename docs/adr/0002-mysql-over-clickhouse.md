# ADR-0002 — 用 MySQL 8 取代 ClickHouse

- **状态**：已接受
- **日期**：2026-09-09
- **⚠️ 推翻既有决策**：[plan.md](../../plan.md) §6 Q4 定的是 ClickHouse。本 ADR 显式推翻它。

## 背景

`plan.md` §6 Q4 选 ClickHouse，理由是「同 ChatInsight」。那个决定是在**不知道存在这份 dump** 的前提下做的。

现在已知：数据源是 `market_insight`，**MySQL 8 生产 RDS**（`hkg-csop-aws-prd-vpc02-rds12…ap-east-1`），用 alembic 管迁移，24 张表，futu 相关 5 张。

## 决策

改用 **MySQL 8**。ClickHouse 从 `docker-compose.yml` 移除，两个 `requirements.txt` 里的 `clickhouse-driver` 一并删掉。

## 理由

1. **这是 `mysqldump` 产物**。灌 ClickHouse 要先做一次有损 DDL 转换（`AUTO_INCREMENT`、`text`、`datetime` 语义全要重写），10 GB 的转换脚本本身就是个会静默出错的新组件。
2. **源系统就是 MySQL**，且已有 alembic 迁移历史。worker 将来最可能的形态是只读直连那台 RDS 或订阅它的增量，同构最省事。
3. **访问模式是「按产品 × 按天聚合」**，量级在这个只读工作台上 MySQL 完全够。ClickHouse 的列存收益在这里不明显，而它的代价（另一套方言、弱 JOIN、无事务）是实打实的。

真到十亿行级别再引 ClickHouse 不迟——那时数据形状也更清楚了。

## 后果

- `init_db.sql` 从 ClickHouse DDL 改写为 MySQL DDL。
- 端口重排：MySQL 3307（比默认 3306 高一位，避免与本机既有实例冲突），ClickHouse 的 8124/9008 释放。
- 与 ChatInsight 的技术栈出现分叉。这是**有意的**：两个项目的数据形状不同，为了「看起来一致」而选错存储是更贵的错误。

## 否决的备选

- **保留 ClickHouse**：需要写并维护一个 10 GB 的有损转换，收益为零。
- **Postgres**：`plan.md` Q4 提过。它没有「与源系统同构」这个好处，而那正是这次选型的决定性因素。
