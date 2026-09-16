# CLAUDE.md —— 开工须知

## 这是什么

**舆情雷达（futu-radar）**：面向 CSOP ETF 产品／市场团队的**只读**舆情工作台，监控富途牛牛社区中自家 61 只 ETF 与客户维护的 59 只竞品的讨论、态度、KOL 与官号动态。无关注／指派／处置／预警推送。

五个页面（路由以 `frontend/src/App.jsx` 为准）：

| 路由 | 页面 | 设计源文件 | 一句话 |
|---|---|---|---|
| `/sector` | 板块总览 | `sector-overview.dc.html` | 全市场活跃 ETF 舆情分布 |
| `/product` | 产品监控 | `product-monitor.dc.html` | 单产品完整舆情视图 |
| `/kol` | KOL 影响力 | `kol-activity.dc.html` | KOL 发帖内容与阵地分布 |
| `/kol/detail` | KOL 详情 | `kol-detail.dc.html` | 单 KOL 画像与发帖记录 |
| `/official` | 官号动态 | `official-activity.dc.html` | 重点官号发布与内容摘要 |

- 一级导航只有两个域：**市场**（板块总览／产品监控）、**账号**（KOL 影响力／官号动态）。
- KOL 详情不在导航里，从 KOL 影响力「详情 →」进入。
- `/` 与未匹配路径都重定向到 `/official`。

## 常用命令

`package.json` 只有一个，在 `frontend/`，所有前端命令都要先 `cd frontend`。

```bash
cd frontend && npm install
cd frontend && npm run dev      # React 移植版 → http://localhost:5173
cd frontend && npm run build    # 产出 frontend/dist
```

- 日常只启动真实数据后端 `8008` 与前端 `5173`。后端默认 `sql`，前端默认连接 `8008`，不再另起 `8019`；缺失数据按缺失态展示。
- `demo` fixture 与 `npm run design` 仅保留给显式回归测试／设计验收，日常启动任务不启动演示服务。测试服务与日常端口隔离，完成后关闭。
- 网络盘（P: 盘）首启慢／HMR 需轮询的原因与适配，见 `frontend/vite.env.js` 顶部注释。
- `npm run build` 的 `@import` 告警属预期，原因见 [README.md](README.md)。

后端与采集端（Python 3.11，各自独立虚拟环境）：

```bash
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
cd backend && .venv/Scripts/python -m pytest -q
cd backend && .venv/Scripts/python app.py   # 默认 sql，8008，瘦库真实供数
cd worker  && .venv/Scripts/python -m pytest -q
cd worker  && .venv/Scripts/python scheduler.py

docker compose up -d              # mysql8(3307) + backend(8008, sql) + worker
```

Windows 控制台加 `PYTHONIOENCODING=utf-8`（或 `python -X utf8`），否则中文日志是乱码。

瘦库一次性构建（只有要跑 `DATA_PROVIDER=sql` 才需要；dump 与瘦库**都不进 git**）：

```bash
cd worker && .venv/Scripts/python -m jobs.import_dump --dump "<本地 SSD 上的 dump 路径>"
cd worker && .venv/Scripts/python -m jobs.etl
```

默认落在 `%LOCALAPPDATA%\futu-radar\radar.db`（**仓库外**，5.3 GB），改位置设 `RADAR_DB_URL`；
理由见 `radar_db/__init__.py`。

前端**不在** compose 里，本地 `npm run dev` 即可；理由与其余定案见 [plan.md](plan.md) §6。

## 仓库结构

| 目录 | 说明 |
|---|---|
| `design/` | Claude Design 项目的**逐字节只读镜像**。永远不要手改。 |
| `frontend/` | React + Vite 移植版，由该镜像移植而来。日常前端开发都在这里。 |
| `backend/` | Flask API。**口径公式的唯一实现处在 `backend/core/`**。 |
| `worker/` | dump 导入、`raw_json` ETL、采集调度。只落原始数据，不做任何口径计算。 |
| `radar_db/` | 瘦库 schema（一份 `MetaData`，backend 与 worker 共用）。本地 SQLite，生产 MySQL 8。 |
| `docs/` | 需求规格、客户确认文档与 `docs/adr/`。 |

`frontend/` 内部：`src/screens/`（五个屏，`productMonitor/`、`sectorOverview/` 因体量拆成目录）、`src/components/`（Shell、DcLink）、`src/lib/`（`dc.js` 语法垫片、`routes.js` 文件名→路由映射）、`src/data/radar.js`、`src/styles/tokens.css`。

`backend/` 内部：`app.py`（`create_app()`）、`api/v1/`（蓝图，22 个端点）、`core/`（口径）、`providers/`（`demo` 读 fixture／`sql` 读瘦库，见 [ADR-0001](docs/adr/0001-dual-provider.md)）、`fixtures/`、`tests/`。PRD 第 5 章 23 组函数→端点的完整映射在 [plan.md](plan.md) §2.1。

`sql` provider 下，**能数出来的字段都是真的；要 AI 标注的随库里有没有标注走；要行情源的一律 `null`**（K 线、日线价格）。标注管线见 [ADR-0017](docs/adr/0017-ai-annotation-pipeline-production.md)（取代 [ADR-0010](docs/adr/0010-annotations-and-ai-pipeline.md)）与 [ADR-0020](docs/adr/0020-llm-only-90d-pilot.md)（90 天只用 LLM 的试点）。写入方分两层：**Layer A** 逐条事实 —— 评论 v2 一次调用七维（相关性／态度／aspect／市场方向／合规×3／证据）、帖子类型／摘要／方向、KOL 评论观点；**Layer B** 产品×区间生成物（`synthesis_outputs`：热议总结／舆情总结／主题命名／负面类别／阶段观点／话题／竞品原因），模型只写字，数全部在 `backend/core/`（`themes`／`lifecycle`／`stages`／`topics`）。入口是 `worker/jobs/extract.py`（按 ETF×时间段抽取＋五条规则预过滤）→ `worker/jobs/pipeline.py --scope`；常驻编排是 `worker/jobs/full_own.py --watch`（默认 61 只自家，`--all` 120 只全池）；本机命令见 [docs/llm-90d-operations.md](docs/llm-90d-operations.md)，从人工核对表到五页全部有数的收尾顺序见 runbook §25。前端的空值适配**已完成**（2026-09-11，[runbook](docs/ai-data-integration-runbook.md) §16 Gate 1）：五页在真实库上不再抛错，缺失态逐块按 PRD §3.6 渲染。

标注怎么跑、跑到哪一步、每道闸口卡在什么上，都在 [docs/ai-data-integration-runbook.md](docs/ai-data-integration-runbook.md) §16（Gate 0–6），那里是这条管线唯一的操作文档。要记住的是它的出口（[ADR-0019](docs/adr/0019-ai-auto-publish-no-human-gate.md)）：**模型写下即发布，没有人工批准门槛**。现行结论 = 链末（没被任何一行 supersede）且不是 `rejected`；同一链末多行取 `created_at` 最新。唯一实现处是 `radar_db/annotations_read.py` 的 `current_annotations()`（backend 与 worker 共用；`sql.py._current_annotations()` 只是转发），`worker/jobs/review.py --reject` 是仅剩的下线通道。

代价是页面上的 AI 结论**没有经过任何人工验证**，所以三处必须一起如实说出来：`/meta` 的 `aiValidation`（恒为 `none`）、板块总览 S6、产品监控 P7。**不许出现「已核验」「准确率 xx%」或任何暗示人工确认过的表述。**

改这一带时注意两件事。一是 `annotations` 里**没有**模型自报的 `confidence`，只有 `calibrated_confidence` 且当前全为 NULL —— 徽章由 `review_state` 驱动，「待确认」的唯一触发是 `needs_review`（**模型自己举手**，不是「还没人看过」），`lowConfidence = 0.7` 在校准概率存在之前不生效。二是这里的缺失**经常是整块容器为 `null`**，不是字段为 `null`（热议总结整池一份、主题聚类整个双极对象）；字段判空一条都拦不住它们。防线是 `src/lib/view.js` 的 `naBox()` ＋ 屏内显式 null 分支，回归靠 `cd frontend && npm run real-data-check`（opt-in，需要本机瘦库）。

设计变更一律**从设计源重新拷贝，不要照着新设计手推一遍**；再导入流程见 [README.md](README.md) 的 *Re-importing from Claude Design*。

## 铁律

**1. 口径公式只在后端实现一份，前端只承接字段。**
[docs/PRD.md](docs/PRD.md) 第 3 章整章即「全局口径（全系统唯一定义，禁止在前端重复实现）」：最常被误犯的是讨论热度 §3.3、提及去重 §3.2、基准区间 §3.1；情绪净值、赞踩比、环比百分比、阶段合并、样本阈值判定同属该章，一律只承接后端字段，不在屏内另算一遍。
代码化汇总：热度＝`design/radar-data.js` 的 `HEAT_FORMULA`／`HEAT_NOTE`／`heatOf()`；基准区间与时间桶＝`buildRange()`（PRD §5：「后端下发，前端不自行算桶」）；评论去重没有独立常量，逐字口径在 PRD §3.2 与 `design/sector-overview.dc.html` 的 S6 口径说明块。**别引 `ETF_MENTION_RULE`**——那是账号域「提及 ETF」口径（按出现次数累加），与市场域评论去重语义相反。
接缝在 `frontend/src/data/radar.js`，公式与演示期现状说明 → [docs/PRD.md](docs/PRD.md) 第 3 章。后端侧唯一实现处是 `backend/core/`，`worker/` 不得重算。

**2. 字段级 `null` 一律走「暂不可用」状态，绝不落成 0。**
`0` 是「已取得数据且确实为零」的专用值，用它代替未知就是撒谎。六态标记与接口 status 枚举（`ok/empty/unavailable/low_sample/na`）→ [docs/PRD.md](docs/PRD.md) §3.6，实现见 `design/radar-data.js` 的 `STATUS_LEGEND`。
**两套文案并存、不可互换**：状态图例与短徽章用 `暂不可用`／`暂无内容`（PRD §3.6 `STATUS_LEGEND` 逐字，镜像 `delta().short`）；数值位与环比位渲染长文案 `数据暂不可用`（PRD §3.1、§5 `delta()` 契约，镜像 `num()`／`delta().text`）；空态用 `暂无相关内容`（PRD §4.1 逐字）。页面级专用文案（价格、传播关系、阶段观点等）按字段单独对齐 PRD 第 4 章。

**3. 全市场排名由后端算好，前端只过滤显示。**
这里的排名特指「全市场评论量排名」：基于完整活跃 ETF 池计算，板块筛选不重算，同一产品的名次必须稳定。PRD §4.1 筛选总原则逐字：「板块、范围、搜索与开关只改变可见范围；**排名与色阶标尺始终来自全市场**」——热力图色阶标尺同样不得按筛选后的可见集重算。见 [docs/PRD.md](docs/PRD.md) §3.8 与 §4.1，契约为 `ranks(range)`（返回全市场评论量排名 map ＋ total）。
例外：KOL 声量排名是另一套指标，不受此条约束 → [docs/PRD.md](docs/PRD.md) §4.3 M5。

## 干活前先读

- **需求／口径／页面模块清单** → [docs/PRD.md](docs/PRD.md)（v2.0，2026-09-09，效力最高，已对历史文档的 17 处冲突逐条裁决；文中「」内文字为逐字文案，**具约束力**；历史文档效力顺序见其 §0）。
- **架构** → `docs/architecture.md`（**待补**，尚未编写；在它落地前，工程约定看 [README.md](README.md)，数据契约看 PRD 第 5 章）。

## Agent skills

- 问题追踪：issue 以 markdown 存放于 `.scratch/<feature-slug>/` → [docs/agents/issue-tracker.md](docs/agents/issue-tracker.md)
- 分诊标签：五个默认标签 → [docs/agents/triage-labels.md](docs/agents/triage-labels.md)
- 领域建模：single-context，根目录 `CONTEXT.md` + `docs/adr/` → [docs/agents/domain.md](docs/agents/domain.md)
