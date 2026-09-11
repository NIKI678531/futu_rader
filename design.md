# 设计入口

**本文件不承载设计正文**，只给指路牌和一张「现在到哪儿了」的现状表。
架构文档 `docs/architecture.md` **待补**；它落地之前，工程约定看 [README.md](README.md)，数据契约看 [docs/PRD.md](docs/PRD.md) 第 5 章。

注意「设计」在本仓库有两个意思，别混：
- **视觉设计源** = `design/` 目录，Claude Design 项目的逐字节只读镜像，**永远不要手改**；再导入流程见 [README.md](README.md) 的 *Re-importing from Claude Design*。
- **系统设计** = 本文件指向的那些文档。

| 要找什么 | 去哪儿 |
|---|---|
| 系统架构 | `docs/architecture.md`（**待补**）；在此之前看 [README.md](README.md) |
| 数据契约 / 后端要提供哪些接口 | [docs/PRD.md](docs/PRD.md) 第 5 章；23 组函数→REST 端点的映射表见 [plan.md](plan.md) §2.1 |
| 三段式重构的步骤与验证 | [plan.md](plan.md) |
| 工程定案（Flask、端口、响应信封、worker 边界） | [plan.md](plan.md) §6 |
| 前端如何从设计源移植而来 | [README.md](README.md) 的 *How the port maps onto the design source* |
| 三条铁律 | [CLAUDE.md](CLAUDE.md) |
| 领域词汇 | [CONTEXT.md](CONTEXT.md)（术语表；写代码、起变量名、写 issue 前先看） |
| 第一期工程定案 | [docs/adr/](docs/adr/) 共 14 篇；约定见 `docs/agents/domain.md` |
| 第一期施工 spec（24 个契约函数 + API 门面 + 五页接线） | [docs/specs/phase-1-api-integration.md](docs/specs/phase-1-api-integration.md)（含每页验收标准、六态边界、测试策略） |

## 当前落地结构

| 部分 | 状态 |
|---|---|
| `frontend/` 五个页面 | **可运行**。数据仍读 `design/radar-data.js` 的演示数据，**未接后端**。 |
| `backend/` | **骨架**。只有 `GET /api/v1/meta`（返回 `fixtures/meta.json` 静态示例）与 `/health`。 |
| `backend/core/` 口径实现 | **空**。PRD 第 3 章的公式一条都没实现；这里是它们**唯一**的落点。 |
| PRD §5 其余 22 组端点 | **未实现**，映射表已定，见 [plan.md](plan.md) §2.1。 |
| `worker/` | **骨架**。只有一个 heartbeat 任务；`jobs/collect.py` 是占位。 |
| 数据库 | 选型已改为 **MySQL 8**（[ADR-0002](docs/adr/0002-mysql-over-clickhouse.md)）。表结构**未落地**，`init_db.sql` 仍是 ClickHouse 时期的只建库版本。 |
| 10GB 真实 dump | **未导入**。导入与瘦库派生方案见 [ADR-0008](docs/adr/0008-dump-import-and-slim-db.md)，其中两条「导入后验证」尚未跑过。 |
| 双 provider / AI 标注 | provider 已实现；标注管线已落地但**尚未驱动页面**（129 条脱敏影子运行，无 approved 结果）。见 [ADR-0001](docs/adr/0001-dual-provider.md)、[ADR-0017](docs/adr/0017-ai-annotation-pipeline-production.md)。 |
| 两个 Dockerfile 与 compose | **未验证**：`docker compose config` 解析通过，但 Docker daemon 当时没起，镜像一次都没构建过。 |
| 前端容器镜像 | **不做**（plan.md Q5）。 |

这张表随实施进度更新 —— 骨架被当成成品是这类仓库最常见的误读。
