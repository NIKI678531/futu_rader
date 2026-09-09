# ADR-0014 — 数据访问层用 SQLAlchemy Core，不用 ORM

- **状态**：已接受
- **日期**：2026-09-09
- **相关**：[ADR-0002](0002-mysql-over-clickhouse.md)

## 背景

[ADR-0002](0002-mysql-over-clickhouse.md) 定了 MySQL 8。后端怎么访问它有三种常见做法：裸 SQL ＋ PyMySQL、SQLAlchemy Core、SQLAlchemy ORM ＋ alembic（源系统 `market_insight` 用的就是最后这套）。

## 决策

**SQLAlchemy Core**（不用 ORM）。`requirements.txt` 用 `SQLAlchemy` ＋ `PyMySQL`，两个文件里的 `clickhouse-driver` 删掉。

## 理由

- 源系统本来就是 SQLAlchemy ＋ alembic 那套，同生态省心。
- 但本系统是**只读工作台**：ORM 的对象图和身份映射一点用没有，反而会诱导人在 Python 里做本该在 SQL 里做的聚合——那就直接踩铁律 1 的边（口径散进 Python 循环里，就不再「只有一份实现」了）。
- Core 给的是参数绑定、方言、连接池这些真正有用的东西，而 **SQL 仍然是手写的、看得见的、可以整段贴进 ADR 或 PRD 讨论里的**。口径公式要能被客户逐条核对，这一点很重要。

## 后果

- 口径公式以 SQL 的形式活在 `backend/core/`，评审时可以直接对着 PRD 第 3 章逐条比。
- 没有 ORM 迁移，schema 变更靠 `init_db.sql` ＋ 显式的 DDL 脚本。本项目是只读消费方，schema 变更频率低，可接受。

## 否决的备选

- **裸 SQL ＋ PyMySQL**：参数绑定和连接池要自己搭，省下的复杂度还不如引入的 bug 多。
- **SQLAlchemy ORM ＋ alembic**：见理由第 2 条。
