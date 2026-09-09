-- ClickHouse 初始化脚本。容器首次启动时由 /docker-entrypoint-initdb.d/ 执行。
--
-- 当前只建库。表结构等数据管道设计定稿再补 —— 采集范围、字段可得性与保留策略都还没定，
-- 现在拍一版表只会在第一次真实采集时推倒重来。
--
-- 补表时守住两条（CLAUDE.md 铁律 2、PRD §3.6）：
--   1. 可缺失的计数字段一律 Nullable，缺失落 NULL；**绝不用 0 代替未知** ——
--      0 是「已取得数据且统计值确实为零」的专用值。
--   2. 只存原始事实，不存算好的热度/环比/排名。口径归 backend/core/，
--      物化视图也不行：那等于把公式抄了第二份（铁律 1）。

CREATE DATABASE IF NOT EXISTS futu_radar;

-- TODO: posts / comments / accounts / products —— 待数据管道设计定稿。
