# 重构计划：frontend / backend / worker 三段式

状态：**2026-09-09 已批注 —— §6 八条决策全部按建议采纳，三个步骤已实施**。
落地现状（哪些是成品、哪些是骨架）见 [design.md](design.md) 的「当前落地结构」表。

参照：`P:/NIKI/ChatInsight-main`（同团队上一个项目，只读参考其目录组织与 `docker-compose.yml`）。
需求与口径来源：[docs/PRD.md](docs/PRD.md) v2.0，尤其第 5 章「数据契约」。

---

## 0. 目标结构

```
futu-radar/
├── design/                  # 不动：Claude Design 逐字节只读镜像
├── docs/                    # 不动：PRD 与客户确认文档
├── frontend/                # ← 由 app/ 改名，React + Vite，五个页面
├── backend/                 # ← 新增骨架，Flask API，本次只落 GET /api/v1/meta
├── worker/                  # ← 新增骨架，采集/同步调度，本次只落空跑的 scheduler
├── docker-compose.yml       # ← 新增：clickhouse + backend + worker
├── requirements.md          # ← 新增：指针型，指回 docs/PRD.md
├── design.md                # ← 新增：指针型，指回 README.md + PRD 第 5 章
├── plan.md                  # 本文件
├── README.md / CLAUDE.md    # 随改名同步更新
└── .gitignore / .gitattributes
```

与 ChatInsight 的差异（有意为之）：

| 项 | ChatInsight | 本仓库 | 原因 |
|---|---|---|---|
| 同步任务位置 | `backend/sync/`，靠 `sync-worker.Dockerfile` 单独起 | 独立顶层 `worker/` | 采集节奏与 API 无关，独立部署、独立扩缩、独立 fail |
| 根目录需求文档 | `requiremnets.md`（含拼写错误）＋ `design.md` 承载全部内容 | 同名文件但**只做指针** | 需求唯一来源是 `docs/PRD.md`，第二份副本必然漂移 |
| 前端容器 | `frontend/Dockerfile` 打进 nginx | 本次**不做**（见 Q5） | P: 网络盘下容器构建极慢，开发期 `npm run dev` 足够 |
| 数据库端口 | 8123 / 9007 | 8124 / 9008（见 Q3） | 让两个项目能同时在本机跑 |

---

## 1. 步骤一：`app/` → `frontend/`

### 改什么

1. `git mv app frontend`。
   - `git mv` 会连同未跟踪的 `node_modules/`、`dist/` 一起做目录级 rename；若 git 拒绝，退回 `mv app frontend && git add -A`。
   - **不要**删掉 `node_modules` 重装：P: 盘上 `npm install` 需数分钟，rename 是秒级。
2. **源码零改动**。已核对全部跨目录引用，改名后相对深度不变：
   - `frontend/src/data/radar.js` → `../../../design/radar-data.js` ✔
   - `frontend/src/styles/tokens.css` → `../../../design/_ds/…/colors_and_type.css` ✔
   - `frontend/vite.design.config.js` → `root: '../design'` ✔
   - `vite.env.js` 的网络盘探测基于 `import.meta.dirname`，与目录名无关 ✔
3. 文档同步（纯文本替换 `app/` → `frontend/`，`cd app` → `cd frontend`）：
   - `CLAUDE.md`：第 7、23、29、33、41、44、53 行
   - `README.md`：第 11、24、27、58、61、63、64、65、69、70 行
   - `docs/agents/domain.md`：第 22 行的目录树
4. `.gitignore` 追加 Python 段：`__pycache__/`、`*.py[cod]`、`.venv/`、`.pytest_cache/`、`.env`（`.env.example` 要保留，用 `!.env.example`）。

### 怎么验证

```bash
cd frontend && npm run build          # 期望：构建通过；只有已知的 @import 告警（README「Known warnings」）
cd frontend && npm run dev            # → http://localhost:5173
cd frontend && npm run design         # → http://localhost:5174/official-activity.dc.html
```

逐条勾：五个路由都能打开且渲染出内容 —— `/official`、`/kol`、`/kol/detail?kol=…`（从 KOL 影响力「详情 →」点进去）、`/product?code=…&range=…`、`/sector`；`/` 与随机路径重定向到 `/official`。
`git status` 应显示为 rename 而非 delete+add（`git log --follow frontend/src/App.jsx` 能追到改名前历史）。

---

## 2. 步骤二：`backend/` 与 `worker/` 骨架

### 2.1 后端要提供哪些接口（PRD 第 5 章 → REST 映射）

PRD §5 逐字规定：「生产实现时每个函数对应一个（组）REST 端点，**响应形状与函数返回一致**」。下表是全量映射，**本次只实现最后一行**，其余留空目录与 TODO：

| PRD §5 函数 | 建议端点（`/api/v1` 前缀） | 消费页面 |
|---|---|---|
| `buildRange(key)` | `GET /ranges/{key}` | 全部 |
| `observe(code, range, salt?)` | `GET /products/{code}/observe?range=&salt=` | 总览/监控 |
| `pool(range)` | `GET /pool?range=` | 总览 |
| `ranks(range)` | `GET /ranks?range=` | 总览/监控 |
| `benchmark(code, range)` | `GET /products/{code}/benchmark?range=` | 总览/监控 |
| `summaryFor` / `hotSummaryFor` | `GET /products/{code}/summary`、`…/hot-summary` | 总览/监控 |
| `themesFor(code, range, polarity)` | `GET /products/{code}/themes?polarity=` | 总览/监控 |
| `negCatsFor` | `GET /products/{code}/negative-categories` | 总览抽屉 |
| `topicsFor` | `GET /products/{code}/topics` | 监控 |
| `competitorsFor` | `GET /products/{code}/competitors` | 总览/监控 |
| `evidenceFor(code, ctxKey, polarity, n)` | `GET /products/{code}/evidence?ctx=&polarity=&n=` | 证据侧栏 |
| `complianceFor` | `GET /products/{code}/compliance` | 总览/监控 |
| `kolMentionsFor` | `GET /products/{code}/kol-mentions` | 监控 |
| `candlesFor` | `GET /products/{code}/candles` | 监控 |
| `stagesFor` | `GET /products/{code}/stages` | 监控 |
| `heatSeriesFor` | `GET /products/{code}/heat-series` | 监控 |
| `dailyFor(code\|'ALL')` | `GET /products/{code}/daily` | 账号域沿用 |
| `kolImpact(range)` | `GET /kols/impact?range=` | KOL 两页 |
| `kolProfile(name, …)` | `GET /kols/{name}/profile?range=` | KOL 两页 |
| `kolOpinions(kol, range)` | `GET /kols/{name}/opinions?range=` | KOL 详情 |
| `officialPosts(range)` | `GET /officials/posts?range=` | 官号 |
| `etfMentionsFor(account, range)` | `GET /officials/{account}/etf-mentions?range=` | 官号 |
| **常量集合** | **`GET /meta` ← 本次实现** | 全部 |

`delta(cur, base)` 不是端点：它是环比字段的**形状**（`{text,short,abs,pct,dir}`），由各端点内嵌返回。前端不得自己算环比（铁律 1）。

三条铁律在接口层的落点，骨架里就要用注释钉住：
- 热度、去重、基准区间、情绪净值、赞踩比、环比、阶段合并、样本阈值 —— **只在 `backend/core/` 实现一份**，`worker/` 与前端都不得重算。
- 字段级 `null` ＝「暂不可用」，绝不落成 `0`；数据缺失一律返回带 status 的 **200**，不返回 5xx（PRD §3.6）。
- 全市场评论量排名由 `/ranks` 算好，前端只过滤显示（PRD §3.8、§4.1）。

### 2.2 `backend/` 目录

```
backend/
├── Dockerfile              # python:3.11-slim，非 root 用户，HEALTHCHECK 打 /health（照 ChatInsight 的写法）
├── requirements.txt        # flask / flask-cors / python-dotenv / gunicorn / clickhouse-driver / pytest
├── .env.example            # APP_PORT / CLICKHOUSE_* / ROOT_PATH（反代子路径，见 Q6）
├── app.py                  # create_app()：注册 v1 蓝图 + /health + 统一错误处理
├── api/
│   ├── __init__.py
│   └── v1/
│       ├── __init__.py     # Blueprint('v1', url_prefix='/api/v1')
│       └── meta.py         # GET /api/v1/meta ← 本次唯一有实现的端点
├── core/                   # 口径唯一实现处；本次只放 __init__.py + 一份说明注释
│   └── __init__.py
├── fixtures/
│   └── meta.json           # 静态示例响应，逐字对齐 PRD §3.1/§3.3/§3.5/§3.6/§3.7
└── tests/
    ├── conftest.py         # app fixture（pytest，风格照 ChatInsight backend/tests）
    └── test_meta.py
```

`GET /api/v1/meta` 本次返回 `fixtures/meta.json` 原样（`status: "ok"`，HTTP 200），内容取 PRD §5 常量行里前端启动就要用的那部分：

```jsonc
{
  "status": "ok",
  "data": {
    "presets": [ { "key": "d7", "days": 7, "label": "近 7 天", "bucket": "day", "buckets": 7,
                   "benchLabel": "较上一等长区间", "chartTitle": "区间舆情与价格趋势" } ],  // d1/d2/d7/d14/d30 五档全给，PRD §3.1
    "defaultRange": "d7",
    "sectors": [ { "key": "hk", "name": "港股", "hue": "#2361AD", "tint": "#EAF1FB" } ],   // 8 板块，PRD §3.7
    "statusLegend": [ { "key": "暂不可用", "text": "字段应有值，但当前数据源未提供或尚未核验。" } ], // 六态逐字，PRD §3.6
    "heat": { "formula": "讨论热度 ＝ 评论量 ＋ 0.3 × 点赞 ＋ 1 × 转发",
              "note": "点赞含帖子获赞与评论获赞，转发为相关帖子的转发数。",
              "weights": { "like": 0.3, "share": 1 } },                                    // PRD §3.3
    "thresholds": { "lowSample": 10, "stageHalfDay": 5, "kolAttitude": 3,
                    "rankBench": 5, "heatmapBench": 20, "lowConfidence": 0.7, "newDays": 30 }, // PRD §3.5
    "updatedAt": "2026-09-02 09:00 HKT"
  }
}
```

示例值直接从 `design/radar-data.js` 的 `PRESETS` / `SECTORS` / `STATUS_LEGEND` / `HEAT_FORMULA` / `HEAT_NOTE` / `LOW_SAMPLE` / `NEW_DAYS` 抄，保证与设计源、PRD 三方逐字一致。
**本次前端不接线**：`frontend/src/data/radar.js` 仍读 `window.RADAR`，`/api/v1/meta` 只作为契约样板存在，避免半接线状态下两套口径并存。

### 2.3 `worker/` 目录

```
worker/
├── Dockerfile              # 同 base 镜像；CMD ["python", "scheduler.py"]
├── requirements.txt        # apscheduler / requests / clickhouse-driver / python-dotenv / pytest
├── .env.example            # 采集源 token、轮询间隔、CLICKHOUSE_*
├── scheduler.py            # APScheduler 入口，注册一个 heartbeat 任务，打日志即返回
├── jobs/
│   ├── __init__.py
│   └── collect.py          # 占位：富途社区帖子/评论采集，含 TODO 与断点续拉思路
└── tests/
    └── test_scheduler.py   # 断言任务注册成功、间隔取自环境变量
```

断点续拉沿用 ChatInsight 已验证的做法：按数据源分片查库里最新一条的时间戳，有则增量、无则从头，`limit/offset` 分页至不足一页为止。骨架里写成注释，不实现。
**worker 不做口径计算**，只落原始数据；聚合与口径归 `backend/core/`（铁律 1）。若后续证明 worker 必须预聚合，走 Q2 决策，别在两边各写一份。

### 怎么验证

```bash
# 本机（不进容器）
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
cd backend && .venv/Scripts/python -m pytest -q            # 期望：test_meta 全绿
cd backend && .venv/Scripts/python app.py                  # → http://localhost:8008
curl -s http://localhost:8008/health                       # {"status":"ok"}
curl -s http://localhost:8008/api/v1/meta                  # 200 + 上面的 JSON
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8008/api/v1/nope   # 404，且 body 是 JSON 不是 HTML

cd worker && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
cd worker && .venv/Scripts/python -m pytest -q
cd worker && .venv/Scripts/python scheduler.py             # 期望：启动日志 + heartbeat 周期打印，Ctrl-C 干净退出
```

`test_meta.py` 至少断言三条：HTTP 200；`data.presets` 五档且默认 `d7`；`data.statusLegend` 六条且文案与 PRD §3.6 逐字相等（这条测试就是铁律 2 的护栏）。

---

## 3. 步骤三：根目录 `docker-compose.yml` 与两份指针文档

### 3.1 `docker-compose.yml`

服务与端口（左为宿主机，选值见 Q3）：

| 服务 | 镜像/构建 | 端口 | 说明 |
|---|---|---|---|
| `clickhouse` | `clickhouse/clickhouse-server:latest` | 8124→8123、9008→9000 | 命名卷 `clickhouse-data`（Windows bind mount 有权限坑，照抄 ChatInsight 的规避）；挂 `./init_db.sql` 到 `/docker-entrypoint-initdb.d/` |
| `backend` | `build: { context: ., dockerfile: backend/Dockerfile }` | 8008→8008 | `depends_on: clickhouse`；env 从 `backend/.env` 读 |
| `worker` | `build: { context: ., dockerfile: worker/Dockerfile }` | — | `depends_on: clickhouse`；`restart: unless-stopped` |

要点：
- **build context 必须是仓库根**，不能是各自子目录 —— 前端引用 `../../../design/`，将来若把 frontend 也打进镜像，context 在 `frontend/` 会直接构建失败；backend/worker 现在就统一成根 context，省得以后再改。
- `init_db.sql` 本次给一个**空壳**（建 database、留建表 TODO），表结构等数据管道设计定稿再补。
- 不写 `version:` 字段（Compose V2 已废弃，ChatInsight 的 `version: '3.8'` 是旧写法，别照抄）。

### 3.2 `requirements.md`（指针型）

一页纸，明确「本文件不承载需求」，只给入口：需求与口径 → `docs/PRD.md` v2.0（效力最高，「」内文字逐字具约束力）；页面模块清单 → PRD 第 4 章；数据契约 → PRD 第 5 章；开放项 → PRD 第 6 章；负面清单 → PRD 第 7 章；历史文档效力顺序 → PRD §0 与第 8 章。
用正确拼写 `requirements.md`，不沿用 ChatInsight 的 `requiremnets.md`。

### 3.3 `design.md`（指针型）

同样只做指引 + 一张「当前落地结构」表：架构 → `docs/architecture.md`（**待补**，未落地前工程约定看 `README.md`）；数据契约 → PRD 第 5 章 ＋ 本文件 §2.1 的端点映射表；前端移植规则与再导入流程 → `README.md`；三条铁律 → `CLAUDE.md`。
另加一段「已实现 vs 骨架」现状表，随实施进度更新，避免读者把骨架当成品。

### 怎么验证

```bash
docker compose config                       # 期望：解析通过，端口/卷/context 与上表一致
docker compose up -d clickhouse
curl -s 'http://localhost:8124/?query=SELECT%201'          # 返回 1
docker compose up -d --build backend worker
curl -s http://localhost:8008/api/v1/meta                  # 容器内同样 200，与本机响应逐字节一致
docker compose logs worker --tail 20                       # 看到 heartbeat 日志
docker compose down                                        # 干净停止，卷保留
```

文档验证：`requirements.md` 与 `design.md` 里每个相对链接都能点开（`docs/architecture.md` 例外，需显式标注「待补」）；两份文件都不含任何 PRD 正文的复制粘贴 —— 有副本就是埋了第二个真相源。

---

## 4. 全量验收清单

2026-09-09 实测结果（本机无 Python，用 `uv` 拉 3.11；Docker daemon 未启动）：

| # | 检查 | 通过标准 | 结果 |
|---|---|---|---|
| 1 | `cd frontend && npm run build` | 通过，仅已知 `@import` 告警 | ✅ 连跑 6 次全过（修掉一个 50% 概率的既有 flake，见下） |
| 2 | 五个路由 + 重定向 | 全部渲染，无空白页、无 console error | ⚠️ dev server 编译并服务了全部路由与模块图（`/src/main.jsx`、`@fs` 下的 `design/radar-data.js` 均 200），**浏览器内的渲染未人工核对** |
| 3 | `npm run design` | `:5174/*.dc.html` 正常，设计镜像干净 | ✅ 五个 `.dc.html` 全 200；顺带修掉了它把缓存写进 `design/.vite` 的问题 |
| 4 | `git log --follow frontend/src/App.jsx` | 能追到改名前提交 | ⏳ 索引里已是 `R`（100% 相似），**提交后**才追得到；当前 `git log -- app/src/App.jsx` 仍可见 `b3089b1` |
| 5 | `backend`/`worker` 的 `pytest` | 全绿 | ✅ 10 passed / 3 passed |
| 6 | `GET /api/v1/meta` | 200，六态文案逐字对齐 PRD §3.6 | ✅ 含 `/health` 200、未知路由 JSON 404 |
| 7 | `docker compose config` | 解析通过 | ✅ |
| 8 | 容器内 `GET /api/v1/meta` | 与本机响应一致 | ❌ **未跑**：Docker daemon 未启动（`npipe:////./pipe/dockerDesktopLinuxEngine` 连不上）。镜像从未构建过，Dockerfile 属未验证代码。 |
| 9 | `design/` 与 `docs/` | 本次改动零触碰 | ✅ `design/` 逐字节未动；`docs/` 只改了 `agents/domain.md` 的目录树（原文写着 `app/`，不改就是错的） |

实施中发现并修掉的既有问题（与本次重构无关，改名前就在）：

1. **Vite 配置解析 50% 概率失败** —— `Failed to resolve entry for package "@vitejs/plugin-react"`，`dev` 与 `build` 都中招。不是装坏了：Vite 打包配置文件时硬编码 `preserveSymlinks: false`，会把每个包 realpath 成 `P:` 背后的 UNC 路径，再靠解析 `net use` 反查盘符 —— 这个竞态偶尔会输。两个配置现在都不用裸包名（插件走 `createRequire`，`defineConfig` 换成 JSDoc 类型），解析根本不发生。首启也从约 40 秒降到约 2 秒。
2. **`npm run design` 污染只读镜像** —— `design/` 没有 `package.json`，Vite 的缓存目录就落在 `design/.vite`。已改为 `frontend/node_modules/.vite-design`，并删掉了已生成的那份。
3. **中文被转义** —— Flask 默认 `ensure_ascii=True`，逐字文案在响应里全变 `\uXXXX`，不可读也没法 grep 比对。已关掉，并补了一条断言守住。
4. **worker 日志乱码** —— Windows 控制台代码页（本机 cp936）吞掉 UTF-8 中文日志。已在入口钉死 `stdout/stderr` 编码。

---

## 5. 明确不做（本次）

- 前端接线到 `/api/v1/*`：仍走 `design/radar-data.js`，避免两套口径并存。
- 任何真实数据采集、ClickHouse 表结构、AI 标注管线。
- `/meta` 以外的 22 组端点（只留目录与 TODO）。
- 前端容器镜像与 nginx 配置（见 Q5）。
- CI（本仓库暂无 `.github/`）。

---

## 6. 已定决策（2026-09-09 全部按建议采纳）

| # | 问题 | 定案 | 影响 |
|---|---|---|---|
| Q1 | 后端框架 Flask 还是 FastAPI | **Flask** —— 与 ChatInsight 一致，团队现成经验、部署脚本可复用 | 步骤二全部代码 |
| Q2 | `worker/` 与 `backend/` 是否共享 Python 包 | **暂不共享**：worker 只落原始数据，口径全在 `backend/core/`。若将来必须预聚合，再抽 `common/` 顶层包，绝不两边各写一份（铁律 1） | 目录结构、Dockerfile context |
| Q3 | 端口 | ClickHouse 8124/9008、backend 8008；前端 5173/5174 不动 —— 让本仓库与 ChatInsight 能同时开着 | compose、`.env.example` |
| Q4 | 存储选型 | ~~**ClickHouse**，同 ChatInsight~~ ⚠️ **已被 [ADR-0002](docs/adr/0002-mysql-over-clickhouse.md) 推翻，改为 MySQL 8** —— 该决定是在不知道客户会提供 `market_insight` MySQL dump 时做的 | compose、`init_db.sql` |
| Q5 | compose 是否包含 frontend 服务 | **不包含**：P: 盘容器构建慢，开发期 `npm run dev` 更快；上线前再补多阶段 node→nginx 镜像（context 必须是仓库根） | compose、是否新增 `frontend/Dockerfile` |
| Q6 | 反代子路径 | 预留 `ROOT_PATH` 环境变量（ChatInsight 的做法），本次不启用 | `app.py` |
| Q7 | 响应信封 | `{"status": ..., "data": ...}`，`status` 用 PRD §3.6 的 `ok/empty/unavailable/low_sample/na`，缺数据也返回 200；**不用** ChatInsight 的 `{"success": true}`（布尔表达不了六态） | 所有端点，越早定越好 |
| Q8 | `frontend/` 内是否顺带把 `vite.env.js` 的网络盘适配保留 | 保留原样，改名不触碰 | 无 |

Q7 落地时补了一条原表没写的区分：五态 `status` 只用于 200 响应描述**数据可得性**，传输层错误（404/405/500）走 `{"error": {...}}` 信封、不带 `status`。混用会让前端分不清「没这个产品」和「没这条路由」。实现见 `backend/app.py` 的 `json_errors`。
