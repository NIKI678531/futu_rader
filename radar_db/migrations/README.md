# 瘦库迁移

ADR-0017、[runbook](../../docs/ai-data-integration-runbook.md) Gate 2 的「引入数据库迁移」。

## 为什么不能继续用 `create_all()`

`metadata.create_all()` 只做一件事：**建缺失的表**。它不会给已存在的表加列、改类型、
加约束或加索引 —— 而且不会报错，它会安静地什么都不做。

本机那个 5.27 GB 的库已经建过表了。在 `create_all` 时代改 `schema.py` 的后果是：
新环境建出来的是新结构，老环境还是旧结构，两边跑的是同一份代码。症状会出现在
某一个环境上（`no such column`），而本机永远复现不了。

## 常用命令

从**仓库根**执行（`-c` 的路径是相对仓库根的）：

```bash
# 当前版本
worker/.venv/Scripts/alembic -c radar_db/alembic.ini current

# 升到最新
worker/.venv/Scripts/alembic -c radar_db/alembic.ini upgrade head

# 看一眼将要执行的 SQL（不连库，不改任何东西）
worker/.venv/Scripts/alembic -c radar_db/alembic.ini upgrade head --sql
```

目标库由 `RADAR_DB_URL` 决定 —— 和 backend、worker 运行时用的是同一个
`radar_db.db_url()`。留空则落到 `%LOCALAPPDATA%\futu-radar\radar.db`。

生产（compose 内）：

```bash
RADAR_DB_URL='mysql+pymysql://radar:***@mysql:3306/futu_radar?charset=utf8mb4' \
  alembic -c radar_db/alembic.ini upgrade head
```

## 已有的库：先 stamp，再 upgrade

已经用 `create_all()` 建好的库里表都在，但没有 `alembic_version` 表。直接 `upgrade`
会在第一条 `CREATE TABLE feeds` 上炸掉。先告诉 Alembic「你已经在 0001 了」：

```bash
worker/.venv/Scripts/alembic -c radar_db/alembic.ini stamp 0001
worker/.venv/Scripts/alembic -c radar_db/alembic.ini upgrade head
```

全新的空库直接 `upgrade head`。

## 版本

| 版本 | 内容 |
|---|---|
| `0001` | 基线：迁移引入之前 `create_all()` 建出来的那套表（`src_*`、四张事实表、ADR-0010 的单表 `annotations`、`meta_kv`）。**不改变任何东西**，只是给后续迁移一个共同起点。 |
| `0002` | AI 标注管线五张表，取代单表形态：`annotation_runs` / `annotation_jobs` / `annotations` / `annotation_evidence` / `review_decisions`。同时修掉 §10.3 的 SQLite 主键问题。 |

## 写新迁移时

1. **先改 `schema.py`，再生成迁移**，两者必须一致 ——
   `worker/tests/test_migrations.py` 会逐表逐列比对两条建库路径的产物，不一致就红。
2. `--autogenerate` 的产物要人工读一遍再提交。带 `with_variant` 的类型
   （`AUTO_PK`、`LONGTEXT`）它认不出方言差异，会生成错的 DDL。
3. **不要从 `radar_db.schema` 导入类型别名**。迁移是冻结在时间里的：它描述的是当时
   那一刻的 schema。导入今天的别名，会让这份历史随着今天的代码一起漂移 ——
   而它早就在生产上跑过了。在迁移文件里重新定义一遍。
4. 改了 Prompt / 标签体系 / Schema 的话，改的不是这里，是
   `AI_PROMPT_VERSION` / `AI_TAXONOMY_VERSION` / `AI_SCHEMA_VERSION`（runbook §11.3）。
