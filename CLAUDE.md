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
cd frontend && npm run design   # 设计源静态站 → http://localhost:5174/official-activity.dc.html
cd frontend && npm run build    # 产出 frontend/dist
```

- `npm run design` 直接把 `design/` 当静态目录服务，用于与移植版并排比对；该目录没有 `index.html`，必须访问具体 `.dc.html` 路径。
- 网络盘（P: 盘）首启慢／HMR 需轮询的原因与适配，见 `frontend/vite.env.js` 顶部注释。
- `npm run build` 的 `@import` 告警属预期，原因见 [README.md](README.md)。

后端与采集端（Python 3.11，各自独立虚拟环境）：

```bash
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
cd backend && .venv/Scripts/python -m pytest -q
cd backend && DATA_PROVIDER=demo .venv/Scripts/python app.py   # fixture 供数，不需要库
cd backend && DATA_PROVIDER=sql  .venv/Scripts/python app.py   # 瘦库真实供数
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

`sql` provider 下，**能数出来的字段都是真的，要 AI 标注或行情源的一律 `null`**（态度、主题、摘要、合规、K 线、日线价格 —— 标注管线见 [ADR-0017](docs/adr/0017-ai-annotation-pipeline-production.md)，取代 [ADR-0010](docs/adr/0010-annotations-and-ai-pipeline.md)）。前端目前假定这些字段存在（如 `p.confidence.toFixed(2)`），空值适配尚未做 —— 注意 `annotations` 里**没有**模型自报的 `confidence`，前端这处要改的不止是空值判断。

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
