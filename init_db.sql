-- MySQL 初始化脚本。容器首次启动时由 /docker-entrypoint-initdb.d/ 执行。
--
-- 这里**只建库、只给权限，不建表**。表结构的唯一定义在 `radar_db/schema.py`
-- （backend 与 worker 共用同一份 MetaData），由 `create_all()` 建出来 ——
-- 在这里再抄一份 DDL，第一天两边一样，第三天就不是了，而「列名对不上」在只读工作台里
-- 表现为某个字段静默变成缺失，正是 CLAUDE.md 铁律 2 最怕的形态。
--
-- 存储选型是 MySQL 8 而非 ClickHouse，理由见 docs/adr/0002-mysql-over-clickhouse.md。

CREATE DATABASE IF NOT EXISTS futu_radar
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

GRANT ALL PRIVILEGES ON futu_radar.* TO 'radar'@'%';
FLUSH PRIVILEGES;
