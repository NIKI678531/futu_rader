# ADR-0016 — 本地 SQLite、生产 MySQL；provider 更名 `mysql` → `sql`

- **状态**：已接受
- **日期**：2026-09-10
- **相关**：[ADR-0001](0001-dual-provider.md)、[ADR-0002](0002-mysql-over-clickhouse.md)、[ADR-0008](0008-dump-import-and-slim-db.md)、[ADR-0014](0014-data-access-layer.md)

## 背景

[ADR-0002](0002-mysql-over-clickhouse.md) 把存储定为 MySQL 8。产品随后给了两条约束：

1. **数据源是那份 10.3 GB 的 dump**，不为一次性导入去装一套 MySQL（[ADR-0008](0008-dump-import-and-slim-db.md) 修订）。
2. **「本地是 SQLite，生产环境才是 MySQL」**——原话。

于是有一个必须回答的问题：同一份口径 SQL，在两个方言上跑，会不会算出两个不同的数？

## 决策

**一套 schema、一套口径 SQL，方言由 `RADAR_DB_URL` 决定。**

```
（留空）                                                    → %LOCALAPPDATA%\futu-radar\radar.db
mysql+pymysql://radar:radar@mysql:3306/futu_radar?charset=utf8mb4   → compose 里的生产形态
```

- 表结构的唯一定义在仓库根的 **`radar_db/schema.py`**（一份 `MetaData`，`backend/` 与 `worker/` 共用，两个 Dockerfile 各 `COPY` 一次）。
- `init_db.sql` 只剩 `CREATE DATABASE` ＋ `GRANT`，**不含任何 DDL**——表由 `create_all` 建，避免 DDL 有两份且第三天就不一样。
- provider 由 `mysql` 更名为 **`sql`**（`providers/sql.py`）。`DATA_PROVIDER=mysql` 作为兼容别名保留。

### 两个方言能共用的前提（这是硬约束，不是希望）

**`backend/core/` 与 `providers/sql.py` 只查扁平事实表，绝不碰 `raw_json`。**

JSON 函数是 MySQL 8 与 SQLite 之间差异最大的一块（`JSON_EXTRACT` 的路径语法、`JSON_LENGTH` 的 NULL 语义、`->>`  的可用性都不同）。拆 JSON 是 worker 的活，读路径拿到的永远是已经拆好的列。这条一旦破，两个方言就会在无人察觉的情况下算出不同的数——而这个项目里最贵的错误就是这一类。

其余用到的东西（`COUNT`、`SUM`、`JOIN`、`IN`、`BETWEEN`、`ORDER BY`）在两个方言上语义一致。

## 理由

- **为什么本地不用 MySQL**：装一个只为读一份静态的、一次性导入的库，对每个新同事都是一道额外的门槛；而 SQLite 就是一个文件，可以直接拷。
- **为什么生产不用 SQLite**：并发写、连接池、运维备份，SQLite 一个都不擅长，而 [ADR-0002](0002-mysql-over-clickhouse.md) 选 MySQL 的理由（与源系统同构、worker 将来可能直连 RDS）在生产侧仍然成立。
- **为什么改名**：`DATA_PROVIDER=mysql` 会让人以为本地也得起一个 MySQL——而本地跑的是 SQLite。名字撒的谎比文档纠正得快。
- **为什么瘦库默认在仓库树外**：仓库在 P: 盘（SMB 共享），实测顺序写 9.4 MB/s vs C: 盘 3.1 GB/s，差 330 倍；且瘦库含真实用户昵称／IP 归属地／个人简介（[ADR-0008](0008-dump-import-and-slim-db.md)），放在树外就不必靠 `.gitignore` 拦住它。理由完整版在 `radar_db/__init__.py`。

## 后果

- `docker-compose.yml` 里 ClickHouse（8124/9008）换成 MySQL 8（宿主 3307），带 healthcheck，backend/worker 都 `depends_on: service_healthy`。
- 本地开发的完整链路是：导入 → ETL → `DATA_PROVIDER=sql` 起后端。**不需要 Docker**。
- 跨方言的行为差异要靠测试守住，而不是靠自律。当前守法：`backend/tests/test_sql_provider.py` 用 `radar_db.schema` 在内存 SQLite 上建同一套表跑读路径。**MySQL 侧目前没有等价的自动化测试**——第一次真正部署到 compose 时必须人工对一遍关键数字（见 README 的验收清单）。
- [ADR-0014](0014-data-access-layer.md) 的「schema 变更靠 `init_db.sql` ＋ 显式 DDL 脚本」这一句被本 ADR 取代：schema 变更改 `radar_db/schema.py`。ADR-0014 的正题（用 Core 不用 ORM）不变——`radar_db/` 里只有 `Table` 定义和 `create_all`，没有映射类、没有 session。

## 否决的备选

- **本地也用 MySQL（Docker）**：违反产品给的第 2 条约束，且给每个新同事加一道门槛。
- **本地 SQLite、生产 MySQL，但各写一份 SQL**：口径就有了两份实现，直接踩铁律 1。
- **只用 SQLite，生产也是**：见「理由」第 2 条。
