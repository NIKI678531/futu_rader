# futu-radar 数据、Hugging Face 模型与 AI API 接入执行手册

> 版本：v1.0  
> 日期：2026-09-11  
> 适用环境：Windows 11、PowerShell、Python 3.11、SQLite（本地）/ MySQL 8（生产）  
> 目标：先恢复真实数据展示，再建立可追溯的 AI 标注、Hugging Face 本地模型、行情与在线采集链路。

## 0. 安全红线

**不要把真实 API Key 写进本文件、README、代码、Issue、聊天记录或任何会提交到 Git 的文件。**

本手册中的密钥字段必须永久保持为空。真实值只应写入：

- 本地开发：已被 `.gitignore` 排除的 `worker/.env`；
- 生产环境：Docker/Kubernetes Secret、云 Secret Manager 或 CI/CD 加密变量；
- 临时验证：当前 PowerShell 进程的 `$env:*` 环境变量。

提交前必须确认：

```powershell
git status --short
git diff -- . ':!*.env'
```

任何以 `VITE_` 开头的环境变量都会被前端打包并发送到浏览器，**API Key 绝不能使用 `VITE_` 前缀**。

---

## 1. 执行结论

### 1.1 目标技术组合

第一版目标组合如下：

| 层 | 选定方案 | 用途 |
|---|---|---|
| 原始数据 | `dump-market_insight-*.sql` → 本地 SQLite 瘦库 | 历史帖子、评论、提及和用户事实 |
| 真实查询 | `DATA_PROVIDER=sql` | 让五个页面读取真实统计 |
| 外部生成式模型 | OpenAI-compatible API，实际模型 ID 待供应商确认 | 冷启动标注、复杂抽取、摘要、难例 |
| 本地分类器 P0 | `Langboat/mengzi-bert-base-fin` 微调 | 评论相关性、产品态度、帖子分类 |
| 本地向量模型 P0 | `BAAI/bge-m3` | 近重复、候选召回、主题聚类、证据检索 |
| 本地重排模型 P0 | `BAAI/bge-reranker-v2-m3` | 产品候选消歧、Top-K 相关性重排 |
| 本地分类挑战者 P1 | `Qwen/Qwen3-1.7B-Base` 微调 | 当 Mengzi 质量不达标时比较替换 |
| 本地教师/隐私兜底 P1 | `Qwen/Qwen3.5-4B` | 外部 API 不可用时做难例、抽取和摘要 |
| 行情 | Futu OpenAPI/OpenD | 港股 ETF 小时线、日线、最新价格 |
| 在线社区采集 | 客户授权 API/数据库增量源 | Futu OpenAPI 不提供社区帖子和评论 |

### 1.2 关于“GPT-5.6 Luna”

截至 2026-09-11，OpenAI 官方[模型文档](https://platform.openai.com/docs/models)和[价格页](https://openai.com/api/pricing/)没有可核验的 `GPT-5.6 Luna` 型号或正式 `model` ID；官方价格页当前可核验到 GPT-5.5 系列。

因此本项目不能凭名称猜测：

```text
gpt-5.6-luna
```

若该名称来自第三方网关、企业私有部署或尚未公开的供应商版本，接入前必须取得：

| # | 需要取得 | 2026-09-11 状态 |
|---|---|---|
| 1 | OpenAI-compatible `base_url` | ✅ `https://amao-prd.csopasset.com/llm/v1` |
| 2 | API Key | ✅ 由项目负责人提供，只存在于环境变量 |
| 3 | `/models` 返回的准确 `model.id` | ✅ `gpt-5.6-luna`，逐字存在 |
| 4 | Chat Completions 或 Responses API 协议说明 | ⚠️ 无文档，由实测反推（§6.4） |
| 5 | JSON Schema/结构化输出支持情况 | ✅ `json_schema` + `strict` 可用 |
| 6 | 上下文和最大输出限制 | ❌ 未确认 |
| 7 | 并发、限流、价格和余额查询方式 | ❌ 未确认 |
| 8 | 数据存储地区、日志保留、是否用于训练及删除政策 | ❌ 未确认 |

**结论已经改变**：这个名称现在**有**一个可核验的落点 —— 它不是 OpenAI 官方型号，
而是 CSOP 内部网关 `amao-prd` 上的一个模型 ID。上表第 3 行不再是猜测。
但第 6–8 行仍是空的，接入范围因此受限（见 §16 Gate 0 末尾）。

本手册预留通用 OpenAI-compatible 配置，但不把未经核实的名称写死为生产型号。DeepSeek 不再作为目标运行时；现有 DeepSeek 内容仅保留在研究文档中作为历史对照。

### 1.3 当前真正的实施顺序

```text
确认瘦库位置和完整性
  → 强制后端使用 sql provider
  → 修复真实 provider 下的前端空值/契约
  → 确认 GPT API 的真实 base_url 与 model ID
  → 建 AI 标注表、任务和统一模型客户端
  → GPT 影子标注 + 人工抽查
  → 接入本地 Hugging Face 分类/向量模型
  → SqlProvider 读取 annotations
  → 接行情
  → 接授权后的在线社区增量源
```

不要在五个真实页面仍会崩溃时先跑全量 AI；否则即使 annotations 已写入，也无法区分模型问题和前端契约问题。

---

## 2. 当前代码事实

完整证据见[架构审计](architecture.md)，模型和数据集背景见[模型与数据集调研](research/sentiment-models-and-datasets.md)。

### 2.1 已完成

- React 五个页面已通过 `frontend/src/data/radar.js` 走 Flask API。
- 后端已有 `demo/sql` 两个 Provider。
- `worker/jobs/import_dump.py` 能流式处理约 10.3 GB dump。
- `worker/jobs/etl.py` 能生成 `feeds/comments/mentions/users`。
- SQL Provider 能返回真实计数、帖子、评论、热度、排名、环比。
- 数据文档记录的瘦库基准为：
  - `feeds`: 504,400
  - `comments`: 350,399
  - `mentions`: 927,071
  - `users`: 18,292
  - `anchor`: 2026-08-25
  - `data_max_ts`: 2026-08-26 03:00

### 2.2 未完成

- 后端默认仍是 `DATA_PROVIDER=demo`。
- `worker/jobs/collect.py` 尚未实现在线采集。
- scheduler 只运行 heartbeat。
- 没有 AI API Client、Prompt、结构化输出 Schema 或 annotation job。
- `annotations` 表没有实际 Writer，SqlProvider 也不读取该表。
- `annotations` 缺少 `subject_code`、版本、证据和人工复核字段。
- SQLite 下当前 `BigInteger autoincrement` 定义存在自增风险。
- 没有行情事实表和同步 Job。
- SQL 模式下部分前端直接访问 `null`，五个页面不能稳定完成渲染。
- 官号 `etfMentionsFor` 的 demo/sql 返回形状不一致。

### 2.3 当前没有一段可替换的 DeepSeek 运行代码

仓库内的 DeepSeek、Qwen、Claude 和 Hugging Face 型号目前都只存在于 `docs/` 研究材料中。`worker/requirements.txt` 也没有 `openai`、`transformers` 或 `torch`。

因此“把 DeepSeek 换成 GPT-5.6 Luna”的工程含义是（**五项 2026-09-11 已全部完成**）：

1. ✅ 新建供应商无关的 AI 模型接入层 —— `worker/ai/providers/base.py`；
2. ✅ 第一位实现 OpenAI-compatible Provider —— `worker/ai/providers/openai_compatible.py`；
3. ✅ 用供应商返回的实际模型 ID 配置 —— 且落库的是**网关返回的**模型名，不是请求里发的别名；
4. ✅ 不新增 DeepSeek Provider；
5. ✅ 新建 ADR 取代 ADR-0010 中原定的 Claude Haiku 4.5，而不是修改历史 ADR ——
   [ADR-0017](adr/0017-ai-annotation-pipeline-production.md)。

### 2.4 当前模型引用位置

| 文件 | 当前引用 | 性质 | 后续处置 |
|---|---|---|---|
| `docs/adr/0010-annotations-and-ai-pipeline.md` | `claude-haiku-4-5-20251001` | 已接受的旧模型决策，但没有运行代码 | ✅ 已由 [ADR-0017](adr/0017-ai-annotation-pipeline-production.md) 取代；正文保留不改 |
| `docs/research/sentiment-model-selection.md` | DeepSeek、Qwen、Claude、GPT-5.6 Luna | 探索记录 | 保留为研究背景；不能当运行配置 |
| `docs/research/better-nlp-models.md` | Qwen3/Qwen3.5、BGE、MacBERT、XLM-R | 本地模型专项比较 | 作为离线 benchmark 候选来源 |
| `docs/research/sentiment-models-and-datasets.md` | Mengzi、BGE、Qwen、DeepSeek 等 | 当前较完整选型研究 | P0/P1 模型清单的主要依据 |
| `docs/architecture.md` | 模型职责与 AI 缺口 | 架构审计 | 作为接入前置问题清单 |
| `worker/requirements.txt` | 无 AI 依赖 | 当前运行代码 | ✅ 已加 `pydantic`、`alembic`；未用官方 `openai` SDK（理由见 provider 模块头） |
| `worker/jobs/` | 无 annotate/embed/model job | 当前运行代码 | ✅ `worker/jobs/annotate.py` 已建；`embed`/本地模型 job 待 Gate 3 |
| `backend/providers/sql.py` | AI 字段固定 `None` | 当前运行代码 | annotations 可用后逐项接通 |

结论：现阶段“模型在哪里引用”分成两类——现有引用全部在文档，未来实际引用应集中在 `worker/models/registry.py` 和 `worker/ai/providers/`，业务模块不得各自硬编码模型名。

---

## 3. 数据源和所需资源清单

| 资源 | 必需 | 获取方 | 用途 | 当前检查 |
|---|---:|---|---|---|
| `dump-market_insight-*.sql` | 是 | 客户/现有数据团队 | 历史瘦库重建 | 文件不进 Git |
| 本地 `radar.db` | 是 | dump 导入生成 | 本地真实 Provider | 需确认实际路径 |
| 社区增量 API 或数据库账号 | 上线必需 | 客户/ChatInsight/数据团队 | 在线帖子评论更新 | 当前未提供 |
| 120 只产品正式主数据与别名 | 是 | 客户 | 产品绑定、候选召回 | 当前有 fixture，需业务核验 |
| 官号/KOL 真实 UID 映射 | 是 | 客户 | 替代不稳定的名称匹配 | 当前只部分命中 |
| OpenAI-compatible API 资料 | AI P0 必需 | 模型供应商 | 冷启动、摘要、难例 | `GPT-5.6 Luna` 待确认 |
| Hugging Face 权重 | AI P0 必需 | Hugging Face | 本地分类、向量、重排 | 公开模型可下载 |
| 人工金标 | AI P0 必需 | 产品/标注团队 | 选型、阈值、校准 | 建议评论 3,000、帖子 1,000 |
| Futu OpenD 账号和行情权限 | 行情必需 | Futu | 港股 ETF K 线 | 需确认 120 只配额 |

### 3.1 社区在线数据不能从 Futu OpenAPI 获得

Futu OpenAPI 官方能力是行情与交易，没有社区帖子/评论接口。在线社区数据必须来自以下之一：

1. 客户授权的 MarketInsight 增量 API；
2. 客户授权的 MarketInsight 数据库只读账号；
3. 已合法运行的 ChatInsight 数据连接器；
4. 经书面授权和法务审查的采集器。

没有合法增量源时，只能展示 dump 截止日之前的历史数据，不能把它描述成实时监控。

---

## 4. 本地瘦库“已经有但不显示”的排查与恢复

历史审计记录曾验证一份约 5.27 GB 的 SQLite 瘦库，但数据库不在仓库中。换机器、换 Windows 用户、设置了自定义 `RADAR_DB_URL`、移动文件或只启动了 demo backend，都会出现“库好像导入了、页面却没有真实数据”。

### 4.1 第一步：检查默认路径

```powershell
Set-Location P:\NIKI\futu-radar

$DbPath = Join-Path $env:LOCALAPPDATA 'futu-radar\radar.db'
$DbPath
Test-Path $DbPath

if (Test-Path $DbPath) {
    Get-Item $DbPath | Select-Object FullName, Length, LastWriteTime
}
```

通过标准：

- `Test-Path` 为 `True`；
- 文件约 5 GB，但不同导入窗口可以不同；
- 不应位于 `P:\NIKI\futu-radar` 仓库目录内。

若为 `False`：

- 检查你启动 backend 的 PowerShell 是否设置了 `RADAR_DB_URL`；
- 检查 `backend/.env` 和 `worker/.env` 是否使用了不同数据库；
- 搜索你曾指定的 SSD 目录；
- 找不到时按 4.6 使用 dump 重建到一个新路径。

### 4.2 第二步：只读检查 SQLite 内容

以下命令以只读方式打开数据库；路径错误时不会创建空库：

```powershell
@'
import os
import sqlite3
from pathlib import Path

path = Path(os.environ["LOCALAPPDATA"]) / "futu-radar" / "radar.db"
if not path.exists():
    raise SystemExit(f"数据库不存在: {path}")

conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
try:
    print("database:", path)
    print("size:", path.stat().st_size)
    for table in ("src_feeds", "feeds", "comments", "mentions", "users", "annotations"):
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"{table}: {n:,}")
        except sqlite3.Error as exc:
            print(f"{table}: ERROR {exc}")
    print("meta_kv:")
    for row in conn.execute("SELECT k, v FROM meta_kv ORDER BY k"):
        print(" ", row)
finally:
    conn.close()
'@ | py -3.11 -
```

若系统没有 `py -3.11`，将上一段命令最后的 `| py -3.11 -` 替换为
`| .\worker\.venv\Scripts\python.exe -`，其余 Python 内容保持不变。

关键通过标准：

- `feeds/comments/mentions` 均大于 0；
- `meta_kv` 包含 `anchor`；
- `annotations=0` 是当前预期，说明 AI 尚未运行，不代表事实库损坏。

### 4.3 第三步：让后端明确连接该库

不要先修改模板文件。先在一个 PowerShell 终端临时验证：

```powershell
Set-Location P:\NIKI\futu-radar\backend

$DbPath = Join-Path $env:LOCALAPPDATA 'futu-radar\radar.db'
$DbUrlPath = $DbPath.Replace('\', '/')

$env:PYTHONIOENCODING = 'utf-8'
$env:DATA_PROVIDER = 'sql'
$env:RADAR_DB_URL = "sqlite:///$DbUrlPath"

.\.venv\Scripts\python.exe app.py
```

不要在同一端口同时运行一个 demo backend 和一个 sql backend。环境变量只对当前 PowerShell 进程及其子进程生效。

### 4.4 第四步：验证后端，不先打开前端

另开 PowerShell：

```powershell
$health = Invoke-RestMethod 'http://localhost:8008/health'
$meta = Invoke-RestMethod 'http://localhost:8008/api/v1/meta'
$range = Invoke-RestMethod 'http://localhost:8008/api/v1/ranges/d7'
$pool = Invoke-RestMethod 'http://localhost:8008/api/v1/pool?range=d7'

$health
$meta.status
$range.status
$pool.status
$pool.data.list.Count
$pool.data.list |
    Select-Object -First 5 code, name, comments, mentions, discussionHeat, updatedAt
```

通过标准：

- `/health` 正常；
- range/pool 为 200；
- `pool.data.list` 约 120 只产品；
- 至少部分产品 `comments/mentions` 非零；
- `updatedAt` 与真实锚点一致。

`attitude=null` 目前是正常缺口，因为 AI 标注未实现。

### 4.5 第五步：验证前端指向正确后端

```powershell
Set-Location P:\NIKI\futu-radar\frontend

$env:VITE_API_BASE = 'http://localhost:8008/api/v1'
npm run dev
```

然后依次访问：

- `http://localhost:5173/sector`
- `http://localhost:5173/product?code=3033&range=d7`
- `http://localhost:5173/kol`
- `http://localhost:5173/kol/detail`
- `http://localhost:5173/official`

如果 API 已有真实计数但页面崩溃，数据库已经接通，问题属于前端空值或 Provider 契约，不要重复导入 10 GB dump。

### 4.6 找不到瘦库时，从 dump 安全重建

先使用新文件验证，不直接覆盖旧库：

```powershell
Set-Location P:\NIKI\futu-radar\worker

$env:PYTHONIOENCODING = 'utf-8'
$env:DUMP_PATH = 'D:\data\dump-market_insight-202608261101-2.sql'
$env:RADAR_DB_URL = 'sqlite:///D:/futu-radar-data/radar-rebuild.db'

.\.venv\Scripts\python.exe -m jobs.import_dump --dump $env:DUMP_PATH
.\.venv\Scripts\python.exe -m jobs.etl
```

注意：

- dump 应放在本地 SSD，不放 OneDrive 或 `P:` 网络盘；
- 目标目录不能在 Git 仓库内；
- 两个命令必须使用同一个 `RADAR_DB_URL`；
- 完成后先执行 4.2 的只读表计数；
- 再让 backend 指向这个新文件；
- 验证通过后才决定是否替换默认库。

---

## 5. 前端真实数据适配清单

这一步是 AI 接入前置门槛。

| 页面/模块 | 当前问题 | 必须改成 |
|---|---|---|
| `sectorOverview/index.jsx` | 直接读取 `o.attitude.positive` | `attitude=null` 时局部显示“暂不可用” |
| `productMonitor/index.jsx` | 同样假定态度存在 | 数量 KPI 正常展示，AI 卡片独立缺失态 |
| `KolActivity.jsx` | `postType=null` 仍查类型映射 | 显示“类型待分析”，不整页失败 |
| `KolDetail.jsx` | `kolOpinions=null` 和 `confidence=null` 未防护 | 区域级 unavailable；置信度显示“—” |
| `OfficialActivity.jsx` | SQL `etfMentionsFor` 与 demo 形状不同 | 先统一后端契约，再渲染 |
| `api.js` | `readStatus()` 无消费者 | 数据门面应同时暴露 `status/data` |
| `ScreenBoundary.jsx` | 渲染异常误报“后端服务连不上” | 区分网络、HTTP、契约和 React 渲染错误 |

六态必须保持：

| 后端值/状态 | UI |
|---|---|
| `0` | 确实为零 |
| `null` / `unavailable` | 暂不可用 |
| 空数组 / `empty` | 暂无内容 |
| `low_sample` | 样本不足 |
| `na` | — |
| 低置信或未复核 | 待确认 |

禁止在 API Client 中使用全局 `null ?? 0` 或 `null ?? []`。

验收门槛：

1. SQL Provider + `annotations=0` 时五页全部可以打开；
2. 真实计数、帖子和排名可以展示；
3. AI/行情区域各自显示准确缺失原因；
4. 页面不能再出现“演示数据”文案；
5. 增加一条 SQL Provider 浏览器 smoke test，覆盖五个路由。

---

## 6. OpenAI-compatible AI API 接入

### 6.1 供应商确认表

**已于 2026-09-11 逐项实测填写。** 表中「实测」一列是程序发请求量出来的，
「未确认」一列是**供应商侧没有公开、也没有人答复**的 —— 两者不能混为一谈。

| 项目 | 值 | 来源 |
|---|---|---|
| 供应商法定名称 | CSOP（`csopasset.com` 域名下的自建网关） | 域名 |
| 官方/第三方网关 | 内部网关 `amao-prd`，上游是 OpenAI 直连还是 Azure **未确认** | — |
| 控制台地址 | `https://kni9ht.csopasset.com/dashboard` | 项目负责人提供 |
| API 文档地址 | **未提供**；协议形状由实测反推 | — |
| OpenAI-compatible Base URL | `https://amao-prd.csopasset.com/llm/v1` | 实测 |
| `/models` 是否支持 | 是，HTTP 200，返回 6 个模型 | 实测 |
| 实际 `model.id` | `gpt-5.6-luna`（`/models` 里逐字存在，不是宣传名） | 实测 |
| Chat Completions/Responses | **只能用 `/responses`**。`/chat/completions` 存在但 `json_object` 坏（见 §6.4） | 实测 |
| JSON Schema | 支持 `text.format.json_schema` + `strict: true`，含 `$defs`/`$ref` | 实测 |
| 最大上下文 | **未确认**（`/models` 不返回；实测单批 30 条约 3.6k input token 无问题） | — |
| 最大输出 | **未确认**；已按 `status: "incomplete"` 做截断检测 | — |
| 并发与 RPM/TPM | **未确认**；实测 129 条 / 5 批顺序请求未触发 429 | — |
| 数据处理地区 | **未确认** | — |
| 日志保留天数 | **未确认**；已统一发 `store: false` | — |
| 是否用于供应商训练 | **未确认** | — |
| 删除机制 | **未确认** | — |
| 价格与预算上限 | **未确认**；用量已逐 run 落 `annotation_runs`，可随时换算 | — |

补充实测：`gpt-5.6-luna` 是**推理模型** —— 响应的 `output[0]` 是 `reasoning` 项而不是
`message`，`reasoning_tokens` 计费但不出现在输出文本里。解析必须按 `type` 找
`message`，不能取 `output[0]`。

原文这里写的是：「任何一项影响安全或协议兼容的字段不清楚时，只能在脱敏样本上做
PoC，不能发送真实用户评论。」

上表里**数据处理地区、日志保留、是否用于训练、删除机制四项至今未确认**。项目负责人
在 2026-09-11 明确授权「发脱敏后的真实评论」，据此已执行 129 条影子运行。这是一个
知情的决策，不是这条红线被满足了 —— 记在这里，是为了让后面接手的人知道它仍然欠着。
补齐这四项之前，不应扩大到全量 350k 条评论。

### 6.2 `worker/.env` 配置模板

真实配置应写入 `worker/.env`，以下值在本手册中保持为空：

```dotenv
# AI 主供应商：OpenAI-compatible
AI_PRIMARY_PROVIDER=openai_compatible
AI_PRIMARY_BASE_URL=
AI_PRIMARY_API_KEY=
AI_PRIMARY_MODEL=

# 调用控制
AI_REQUEST_TIMEOUT_SECONDS=60
AI_MAX_RETRIES=3
AI_MICRO_BATCH_SIZE=30
AI_MAX_INPUT_TOKENS=8000
AI_CONCURRENCY=4

# 数据与版本
AI_PROMPT_VERSION=comment-product-v1
AI_TAXONOMY_VERSION=v1
AI_SCHEMA_VERSION=v1

# 只有供应商明确支持时启用
AI_STRUCTURED_OUTPUT=true
AI_THINKING_ENABLED=false

# Hugging Face；公开模型通常不要求 Token
HF_TOKEN=
HF_HOME=D:\futu-radar-models\cache
```

生产环境不应直接从 `.env` 读取长期密钥，应由 Secret Manager 注入同名变量。

### 6.3 接口身份验证

如果供应商是 OpenAI 官方：

1. 在 [OpenAI Platform](https://platform.openai.com/) 创建 Project；
2. 配置 Billing/预算；
3. 在 [API Keys](https://platform.openai.com/api-keys) 创建 Project Key；
4. Base URL 使用 `https://api.openai.com/v1`；
5. 调用 `/models` 确认该 Project 实际可用的型号。

如果 `GPT-5.6 Luna` 来自第三方，则必须使用第三方自己的控制台、Base URL 和准确 Model ID，不能把第三方 Key 发送到 OpenAI 官方地址。

先由对应供应商控制台创建 Key，再在临时 PowerShell 中验证：

```powershell
$env:AI_PRIMARY_BASE_URL = ''
$env:AI_PRIMARY_API_KEY = ''
$env:AI_PRIMARY_MODEL = ''

$headers = @{
    Authorization = "Bearer $env:AI_PRIMARY_API_KEY"
    'Content-Type' = 'application/json'
}

$base = $env:AI_PRIMARY_BASE_URL.TrimEnd('/')
Invoke-RestMethod "$base/models" -Headers $headers
```

必须从返回结果复制精确 `id`，不要根据产品宣传名称猜测。

若供应商不实现 `/models`，必须让其提供一段可复现的官方调用样例和准确 `model` 参数。

### 6.4 最小结构化输出测试

> **2026-09-11 按实测改写。** 本节原先写的是 Chat Completions ＋ `temperature = 0`
> ＋ `response_format = json_object`。在目标网关上这三项里有两项直接 400 ——
> 原样照抄会得到两个 HTTP 400 和一个错误的结论。原写法留在下面「实测不通过的写法」
> 里，是为了让后来的人不要再试一遍。

#### 能用的写法：`/responses` + `text.format.json_schema`

```powershell
$headers = @{
    'Authorization' = "Bearer $env:AI_API_KEY"   # 只从环境变量取，不写进文件
    'Content-Type'  = 'application/json'
}

$schema = @{
    type = 'object'
    additionalProperties = $false
    required = @('item_id', 'relevance', 'evidence')
    properties = @{
        item_id   = @{ type = 'string' }
        relevance = @{ type = 'string'; enum = @('relevant', 'irrelevant', 'needs_context') }
        evidence  = @{ type = 'string' }
    }
}

$payload = @{
    model = $env:AI_PRIMARY_MODEL
    input = @(
        @{ role = 'system'
           content = '只输出JSON。判断评论是否评价指定ETF产品；市场涨跌不等于产品态度。' },
        @{ role = 'user'
           content = '{"item_id":"test-1","product_code":"3033","comment":"这只ETF点差太大"}' }
    )
    text = @{ format = @{ type = 'json_schema'; name = 'probe'; strict = $true; schema = $schema } }
    reasoning = @{ effort = 'low' }
    store = $false
} | ConvertTo-Json -Depth 20

$r = Invoke-RestMethod "$base/responses" -Method Post -Headers $headers -Body $payload

# output[0] 是 reasoning 项，不是答案 —— 必须按 type 找 message。
($r.output | Where-Object { $_.type -eq 'message' }).content[0].text
$r.model            # 网关实际用的模型，可能不等于请求里的别名
$r.usage            # input/output/reasoning/cached token
$r.status           # 必须是 completed；incomplete 表示被截断
```

通过标准（逐条已验证，除注明外）：

- HTTP 200 —— ✅；
- 返回合法 JSON —— ✅，且因为 `strict: true`，是**符合 schema** 的 JSON，比原标准更强；
- `item_id` 没有丢失或改变 —— ✅，批量场景按 item_id 对齐而不是按位置，缺项／幻觉项／
  重复项都会让整批失败（`worker/tests/test_ai_schemas.py`）；
- 证据来自原文 —— ✅，但**不靠模型自觉**：证据由程序在原文里定位，定位不到就不存
  （129 条影子运行，0 条非逐字引用，见 §11.3）；
- 市场方向和产品态度没有混淆 —— ✅，实测 `'大笨象扮演的是蓄水池的角色…'`（讲汇丰，
  不是讲这只 ETF）判为 irrelevant；
- 响应中可获得实际模型名和 token usage —— ✅，两者都逐 run 落进 `annotation_runs`；
  usage 缺失时写 NULL 而不是 0（铁律 2）。

#### 实测不通过的写法

| 写法 | 结果 |
|---|---|
| `POST /chat/completions` + `temperature: 0` | **400** `Unsupported parameter: 'temperature' is not supported with this model` |
| `POST /chat/completions` + `response_format: json_object` | **400** `Response input messages must contain the word 'json'` —— 提示词里有「JSON」也照报，小写 `json` 也照报 |

`/models` 给出的 `supported_endpoint_types` 是 `["openai-response", "openai"]`：网关把
Chat Completions 内部翻译成 Responses API，而那条 `json_object` 的校验在翻译后查错了
字段（报错里的 `'***.format'`、`param: "input"` 都是 Responses API 的形状）。结论是
`json_object` 在这个网关上**事实上不可用**，不是提示词写法的问题。

`temperature` 不支持是推理模型的常态。确定性靠的是 `strict` schema 把输出空间锁死，
不是靠 `temperature = 0`。

### 6.5 GPT 在项目中的职责

| 任务 | 是否调用 GPT | 输入 | 输出 | 页面 |
|---|---:|---|---|---|
| 空文本/精确重复/产品代码 | 否 | 原始字段 | 规则结果 | 全部 |
| 冷启动评论相关性 | 是 | 评论、目标产品、必要上下文 | relevant/irrelevant/needs_context | 板块、产品 |
| 冷启动产品态度 | 是 | 已相关评论×产品 | positive/neutral/negative | 板块、产品 |
| 评论 aspect | 是 | 评论×产品 | 费用、流动性、跟踪等多标签 | 产品 |
| 帖子类型和操作方向 | 是 | 帖子正文 | 固定枚举＋证据 | KOL、官号 |
| 帖子摘要 | 是 | 帖子正文 | ≤60 字＋证据句 | KOL、官号 |
| 主题命名 | 是 | 聚类代表样本 | 名称、摘要、代表证据 | 板块、产品 |
| 产品/热议摘要 | 是 | 已聚类证据和计数 | ≤30/60 字总结 | 板块、产品 |
| 合规关注解释 | 是 | 候选信号和原文 | 类别、依据、待确认 | 板块、产品 |
| 竞品固定关系 | 否 | 客户 CMAP | confirmed | 产品 |
| 竞品 AI 候选 | 是 | 产品对和比较证据 | auto_candidate | 产品 |
| 情绪比例、排名、热度 | 否 | annotations/事实计数 | 后端确定性公式 | 板块、产品 |
| K 线 | 否 | 行情 API | OHLC | 产品 |

GPT 不能：

- 直接估算正负面比例；
- 生成不存在的原文证据；
- 判断言论真假、违法或产品违规；
- 代替人工确认合规关注信号；
- 代替行情源生成价格；
- 接收昵称、UID、IP 地区、个人简介等非必要信息。

---

## 7. Hugging Face 模型总表

### 7.1 P0 必须接入

| 模型 ID | 类型 | 许可证 | 项目用途 | 计划引用位置 | 是否直接可用 |
|---|---|---|---|---|---|
| [`Langboat/mengzi-bert-base-fin`](https://huggingface.co/Langboat/mengzi-bert-base-fin) | 中文金融 Encoder | Apache-2.0 | 评论相关性、三分类态度、帖子类型/方向的本地学生模型 | `worker/models/registry.py`、`worker/models/classifier.py`、`worker/jobs/annotate.py` | 否，必须用项目金标微调 |
| [`BAAI/bge-m3`](https://huggingface.co/BAAI/bge-m3) | 多语 Embedding | MIT | 产品/别名召回、近重复、主题聚类、证据检索 | `worker/models/registry.py`、`worker/jobs/embed.py`、`worker/ai/retrieval.py` | 可直接产向量，但阈值/聚类需项目校准 |
| [`BAAI/bge-reranker-v2-m3`](https://huggingface.co/BAAI/bge-reranker-v2-m3) | Cross-encoder Reranker | Apache-2.0 | 对 Top-K 产品候选或证据候选做相关性重排 | `worker/models/registry.py`、`worker/ai/rerank.py` | 可打相关性分，不会输出情感 |

P0 不应下载和同时运行十个模型。以上三个模型加一个外部 GPT Provider 足够建立首个闭环。

### 7.2 P1 挑战者和兜底

| 模型 ID | 角色 | 何时启用 | 计划引用位置 |
|---|---|---|---|
| [`Qwen/Qwen3-1.7B-Base`](https://huggingface.co/Qwen/Qwen3-1.7B-Base) | 更强本地分类挑战者 | Mengzi 在繁体、粤语、否定、比较句或未见产品切片不达标时 | `worker/models/classifier_qwen.py` |
| [`Qwen/Qwen3.5-2B-Base`](https://huggingface.co/Qwen/Qwen3.5-2B-Base) | 新架构分类实验 | Qwen3-1.7B 后仍需比较；先验证 Transformers 版本和显存 | 离线 benchmark，不先接生产 |
| [`Qwen/Qwen3.5-4B`](https://huggingface.co/Qwen/Qwen3.5-4B) | 本地生成式教师/隐私兜底 | 外部 GPT 不获数据治理批准，或需本地难例和摘要 | `worker/ai/providers/local_qwen.py` |
| [`Qwen/Qwen3-Embedding-0.6B`](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) | BGE-M3 挑战者 | 同一金标集显著提高繁简/港式召回或主题稳定性时 | 离线 benchmark → 可替换 registry |
| [`Qwen/Qwen3-Reranker-0.6B`](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B) | BGE Reranker 挑战者 | 在 hard negatives 上显著更好时 | 离线 benchmark → 可替换 registry |

Qwen3.5 需要较新的 Transformers/serving 版本，必须锁定可复现版本，不能长期跟随 `main/nightly`。

### 7.3 只用于基线评测，不接生产

| 模型 ID | 用途 | 不作为生产默认的原因 |
|---|---|---|
| [`hfl/chinese-macbert-base`](https://huggingface.co/hfl/chinese-macbert-base) | 通用中文分类基线 | 没有金融社区标签知识；已有项目反馈效果不佳 |
| [`hfl/chinese-roberta-wwm-ext`](https://huggingface.co/hfl/chinese-roberta-wwm-ext) | 回归基线 | 通用 MLM，必须微调 |
| [`yiyanghkust/finbert-tone-chinese`](https://huggingface.co/yiyanghkust/finbert-tone-chinese) | 开箱中文金融三分类 smoke test | 训练语料是私有分析师报告，不是社区产品态度 |
| [`IDEA-CCNL/Erlangshen-Roberta-330M-Sentiment`](https://huggingface.co/IDEA-CCNL/Erlangshen-Roberta-330M-Sentiment) | 通用中文正负二分类下限 | 没有中性、产品目标和 ETF 口径 |
| `FacebookAI/xlm-roberta-base` | 固化旧错误集 | 已有漏识别/误判反馈，不继续投入主线 |

### 7.4 暂不使用

| 模型 | 原因 |
|---|---|
| `ProsusAI/finbert` | 英文金融文本，不适配中文/繁体/粤语 |
| `nghuyong/ernie-3.0-base-zh` | 社区转换权重许可不清晰 |
| `Qwen3.8-27B` | 对本项目批量短文本分类过重 |
| 任意 Embedding 单独做情感 | 相似度不等于正负态度 |
| 任意 Reranker 单独做三分类态度 | 只输出 query-document 相关性 |

---

## 8. Hugging Face 获取、存储与加载

### 8.1 获取方式

上述 P0 模型是公开权重，不需要购买 API。可注册 [Hugging Face](https://huggingface.co/)账号并创建只读 Token，以提高下载稳定性和审计能力；Token 同样只能写入环境变量。

模型缓存不要放在 `P:` 网络盘或 Git 仓库。建议：

```powershell
$env:HF_HOME = 'D:\futu-radar-models\cache'
$env:HF_TOKEN = ''
```

安装下载工具：

```powershell
py -3.11 -m venv D:\futu-radar-models\.venv
& D:\futu-radar-models\.venv\Scripts\python.exe -m pip install --upgrade pip
& D:\futu-radar-models\.venv\Scripts\python.exe -m pip install 'huggingface_hub[cli]'
```

下载 P0 模型：

```powershell
& D:\futu-radar-models\.venv\Scripts\hf.exe download `
    Langboat/mengzi-bert-base-fin `
    --local-dir D:\futu-radar-models\mengzi-bert-base-fin

& D:\futu-radar-models\.venv\Scripts\hf.exe download `
    BAAI/bge-m3 `
    --local-dir D:\futu-radar-models\bge-m3

& D:\futu-radar-models\.venv\Scripts\hf.exe download `
    BAAI/bge-reranker-v2-m3 `
    --local-dir D:\futu-radar-models\bge-reranker-v2-m3
```

下载后记录：

- 完整模型 ID；
- commit revision；
- 下载日期；
- License 文件；
- 文件 SHA256；
- Transformers/FlagEmbedding 版本。

生产配置必须锁定 revision，不能只写会变化的 `main`。

### 8.2 P0 Python 依赖

建议新增独立 AI requirements/lock 文件，而不是立即污染 backend：

```text
torch
transformers
sentence-transformers
FlagEmbedding
accelerate
datasets
scikit-learn
pydantic
openai
tenacity
```

具体版本应在目标 GPU/CPU 完成 smoke test 后锁定。AI 依赖属于 worker，不应安装到浏览器或 Flask 查询进程。

### 8.3 推荐模型注册表

未来 `worker/models/registry.py` 应是型号唯一入口：

```python
MODEL_REGISTRY = {
    "classifier_primary": {
        "model_id": "Langboat/mengzi-bert-base-fin",
        "revision": "",
        "task": "sequence_classification",
    },
    "embedding_primary": {
        "model_id": "BAAI/bge-m3",
        "revision": "",
        "task": "embedding",
    },
    "reranker_primary": {
        "model_id": "BAAI/bge-reranker-v2-m3",
        "revision": "",
        "task": "reranking",
    },
}
```

业务代码不能在多个文件中散落硬编码模型 ID。

---

## 9. 任务到模型、数据库和页面的完整映射

| 原子任务 | P0 实现 | P1 实现 | annotations kind/事实表 | API/页面 |
|---|---|---|---|---|
| 文本有效性 | 规则 | 轻量分类器 | `text_quality` | 所有 AI 区域 |
| 精确重复 | SHA256/规则 | BGE 近重复 | `duplicate_cluster` | 聚合去重/降权 |
| 产品候选 | 结构化 mention＋别名词典 | BGE-M3 | `comment_subjects` | 市场/产品 |
| 产品候选消歧 | BGE Reranker Top-K | Qwen Reranker 挑战 | `relevance_candidate` | 市场/产品 |
| 评论相关性 | GPT 冷启动＋人工 | Mengzi/Qwen3 分类器 | `relevance` | 态度样本分母 |
| 产品态度 | GPT 冷启动＋人工 | Mengzi/Qwen3 分类器 | `attitude` | 板块、产品、KOL 主要态度 |
| 产品 aspect | GPT 结构化抽取 | 多标签分类器 | `aspect` | 观点主题、负面类别 |
| 市场方向 | GPT/分类器独立标签 | 本地分类器 | `market_direction` | 产品话题情绪 |
| 帖子类型 | GPT 冷启动 | Mengzi/Qwen3 分类器 | `post_type` | KOL、官号 |
| 操作方向 | GPT＋规则 | 本地分类器 | `direction` | KOL、官号 |
| 帖子摘要 | GPT | 本地 Qwen3.5 兜底 | `summary` | KOL、官号 |
| 主题聚类 | BGE-M3＋时间聚类 | Qwen Embedding 挑战 | `topic_cluster` | 板块、产品 |
| 主题命名 | GPT 基于代表证据 | 本地 Qwen3.5 兜底 | `topic_label` | 板块、产品 |
| 阶段观点 | 确定性时间分段＋GPT 分类/摘要 | 本地分类器＋GPT 摘要 | `stage` | 产品阶段观点 |
| 动态负面类 | aspect＋聚类＋GPT 命名 | 专用分类器 | `neg_category` | 板块、产品 |
| 合规信号 | 规则＋GPT 高召回 | 专用分类器＋GPT 解释 | `compliance` | 板块、产品 |
| 原文证据 | GPT 返回 span＋程序验证 | 本地 span 模型 | `annotation_evidence` | 证据侧栏 |
| 固定竞品 | 客户 CMAP 规则 | 不变 | 主数据 | 产品 |
| AI 竞品候选 | 共现/BGE＋GPT 理由 | 人工确认 | `competitor_candidate` | 产品 |
| 情绪比例/净值 | backend/core 聚合 | 不变 | 不写 annotation | 板块、产品 |
| 热度/环比/排名 | backend/core | 不变 | 事实表 | 板块、产品 |
| KOL styleTag | post_type 计数后规则生成 | 不变 | 不需要新模型 | KOL 详情 |
| K 线/价格 | Futu OpenAPI | 不变 | `market_candles` | 产品趋势 |

---

## 10. AI 标注数据模型

### 10.1 判定单元

评论态度必须以：

```text
(comment_id, subject_code)
```

作为唯一判定单元。一条评论可能同时评价两只 ETF，且对 A 积极、对 B 消极。

### 10.2 推荐表结构

现有 `annotations` 不能直接承担生产标注。建议新增或迁移为：

#### `annotation_runs`

```text
run_id
task
provider
model_id
model_revision
prompt_version
taxonomy_version
schema_version
started_at
finished_at
status
input_count
success_count
error_count
token_input
token_output
estimated_cost
```

#### `annotation_jobs`

```text
job_id
target_type
target_id
subject_code
task
input_hash
status
priority
attempts
claimed_at
lease_until
last_error
created_at
updated_at
```

#### `annotations`

```text
annotation_id
target_type
target_id
subject_code
kind
value_json
calibrated_confidence
run_id
input_hash
review_state
created_at
supersedes_id
```

唯一键至少覆盖：

```text
(target_type, target_id, subject_code, kind, input_hash, run_id)
```

#### `annotation_evidence`

```text
annotation_id
source_target_type
source_target_id
start_offset
end_offset
quote_text
quote_hash
```

#### `review_decisions`

```text
annotation_id
reviewer
decision
corrected_value_json
reason_code
reviewed_at
```

### 10.3 必须修复的 SQLite 主键问题

SQLite 自动行号要求精确的 `INTEGER PRIMARY KEY` 语义。迁移时不要继续依赖当前 `BigInteger primary_key autoincrement` 自动生成；可使用：

- SQLite variant 的 `Integer`；
- 应用生成的 UUID/ULID；
- 或显式序列策略。

应使用 Alembic 等显式迁移工具，`metadata.create_all()` 不能升级已有表。

---

## 11. AI 标注 Job 执行规则

### 11.1 评论任务

输入：

```json
{
  "item_id": "comment:123|product:3033",
  "product": {
    "code": "3033",
    "name": "恒生科技指数ETF",
    "aliases": ["3033", "恒科ETF"]
  },
  "comment": "跌下来正好继续加这只",
  "post_title": "可选",
  "parent_comment": "可选"
}
```

输出：

```json
{
  "item_id": "comment:123|product:3033",
  "relevance": "relevant",
  "attitude": "positive",
  "aspects": ["trading_intent"],
  "evidence": "继续加这只",
  "needs_review": false,
  "uncertainty_reasons": []
}
```

约束：

- `irrelevant` 时 `attitude=null`；
- `neutral` 不能表示无关；
- evidence 必须是输入原文连续片段；
- 无法仅凭评论判断时用 `needs_context`；
- 市场看跌不自动等于产品负面；
- 模型自报 confidence 不直接当 0.7 的概率。

### 11.2 帖子任务

输出至少包含：

```json
{
  "post_type": "行情解读",
  "direction": "持有观望",
  "summary": "认为恒科短期震荡，暂时继续持有",
  "evidence_spans": ["暂时不加仓，继续观察"],
  "needs_review": false
}
```

标签必须来自 PRD 冻结枚举；摘要不超过 60 字并随原文语言。

### 11.3 批处理和重试

- 每批从 20–50 条起压测，本项目默认 30；
- 同一批尽量属于同一产品，减少固定上下文；
- 每条必须携带不可变 `item_id`；
- 输出 ID 集合必须与输入完全一致；
- JSON/Schema 失败先重试；
- 连续失败后将批次二分；
- 429/5xx 使用带抖动的指数退避；
- 达到最大次数进入 dead-letter，不写伪默认值；
- 缓存键包括输入、上下文、产品、模型、Prompt、taxonomy 和 schema 版本；
- 模型失败不得覆盖旧的已确认结果。

### 11.4 脱敏

发送给外部 GPT 的内容只保留：

- 匿名 item ID；
- 产品代码/名称/必要别名；
- 评论或帖子正文；
- 必需的帖子标题/父评论。

不得发送：

- 昵称；
- 用户 UID；
- IP 地区；
- 个人简介；
- 粉丝、关注和访问数据；
- 原始数据库连接串；
- 内部 Token 或 URL 签名。

---

## 12. 计划新增的代码位置

以下是目标结构，不代表当前已存在：

```text
worker/
  ai/
    config.py
    schemas.py
    prompts/
      comment_product_v1.py
      post_annotation_v1.py
      topic_summary_v1.py
      compliance_v1.py
    providers/
      base.py
      openai_compatible.py
      local_qwen.py
    retrieval.py
    rerank.py
  models/
    registry.py
    classifier.py
    classifier_qwen.py
    calibration.py
  jobs/
    annotate.py
    embed.py
    cluster_topics.py
    sync_prices.py
  tests/
    test_ai_schemas.py
    test_annotation_job.py
    test_provider_contract.py

backend/
  providers/sql.py
  core/market.py
  core/narrative.py
  core/kol.py
  core/evidence.py

radar_db/
  schema.py
  migrations/

frontend/
  src/components/DataState.jsx
  src/lib/api.js
  src/screens/*
```

职责：

- `worker/ai/providers/*`：只负责调用模型；
- `worker/ai/schemas.py`：Pydantic 输出校验；
- `worker/jobs/annotate.py`：领取任务、调用、重试、写库；
- `worker/models/*`：本地 Hugging Face 推理；
- `backend/providers/sql.py`：读取事实和 annotations；
- `backend/core/*`：把原子标签聚合成页面指标；
- 前端：只渲染后端结果和六态，不自行重新算口径。

---

## 13. 人工标注和训练

### 13.1 第一批金标

建议：

- 评论×产品：3,000 条；
- 帖子：1,000 篇；
- 两名标注员独立标注；
- 冲突由第三人/产品负责人裁决；
- 测试集按帖子线程、产品和时间隔离。

必须覆盖：

- 简体、繁体、粤语、中英混合；
- 短回复、代词、表情；
- 否定、双重否定、反讽；
- 多产品比较；
- 市场看跌但产品正面；
- 市场看涨但产品负面；
- 费用、流动性、点差、跟踪、分红、杠反损耗；
- 图片/无文本；
- 评论分页截断的热门帖子。

### 13.2 训练顺序

1. GPT 在固定 Schema 下生成候选标签；
2. 人工复核抽样和高风险记录；
3. 人工结果成为 `gold_labels`；
4. 微调 Mengzi 相关性/态度分类头；
5. 单独校准概率；
6. 高置信本地结果自动通过；
7. 低置信/冲突/需上下文升级 GPT；
8. 合规信号始终进入人工确认；
9. 若 Mengzi 不达标，再比较 Qwen3-1.7B；
10. 定期以新金标重训，但冻结最终测试集。

### 13.3 评测指标

| 任务 | 主指标 |
|---|---|
| 相关性 | relevant recall、macro-F1、needs_context 占比 |
| 产品态度 | macro-F1、每类 precision/recall、负面召回 |
| 概率 | ECE、Brier、coverage-risk |
| 主题 | 聚类稳定性、重复主题率、代表证据覆盖 |
| 摘要 | 证据覆盖、无依据陈述率、人工偏好 |
| 合规 | 信号召回、误报率、人工确认率 |
| 工程 | schema 成功率、重试率、吞吐、p95、每千条有效成本 |

模型必须在同一金标、同一切分、同一阈值下比较，不能拿不同模型卡的公开指标直接排名。

---

## 14. 行情接入

行情与 AI 无关。推荐使用 Futu OpenAPI/OpenD：

1. 安装并登录 OpenD；
2. 确认香港股票/ETF 行情权限；
3. 确认历史 K 线配额至少覆盖 120 只产品；
4. 项目代码补成 5 位，例如 `3033 → HK.03033`；
5. 初次回填 `K_60M` 和 `K_DAY`；
6. 每小时补拉最近 1–2 天；
7. 每日收盘后校正完整日线。

新增事实表建议：

```text
market_candles
  code
  ts
  granularity
  open
  high
  low
  close
  volume
  turnover
  source
  fetched_at
```

唯一键：

```text
(code, ts, granularity)
```

非交易时段、休市和取不到的行情使用缺失/null，不补 0，不用指数或其他产品价格替代。

官方文档：

- [历史 K 线](https://openapi.futunn.com/futu-api-doc/en/quote/request-history-kline.html)
- [行情权限和配额](https://openapi.futunn.com/futu-api-doc/en/intro/authority.html)

---

## 15. 在线社区增量采集

在客户提供合法上游后，`worker/jobs/collect.py` 应实现：

1. 按产品/账号分片；
2. 从 `collector_checkpoints` 读取最后成功时间；
3. 每次重叠回看 2–24 小时；
4. 分页拉取；
5. 按稳定 `feed_id/comment_id` 幂等 upsert；
6. 保留原始 payload、源时间和抓取时间；
7. 记录评论分页是否完整；
8. 发布约 24 小时后回补赞/评/转/浏览；
9. 正文或上下文 hash 改变时重新排 AI Job；
10. 成功后推进 checkpoint；
11. 失败时指数退避和 dead-letter；
12. 每小时由 scheduler 触发。

在线路径不能使用当前一次性 ETL 的“清空事实表后重建”方案。一次性 dump 重建与在线 upsert 必须是两条独立路径。

---

## 16. 分阶段执行清单

> **本轮执行范围（2026-09-11）：Gate 0 ＋ Gate 2。** 由项目负责人明确选定。
> Gate 1 与 Gate 3–6 本轮未执行，各自的阻塞原因逐条记在下面 —— 没有勾的框，
> 下面一定有一行说明它为什么没勾。

### Gate 0：供应商身份确认

- [x] 明确 `GPT-5.6 Luna` Key 来自 OpenAI 官方还是第三方 ——
      **第三方**：CSOP 自建网关 `amao-prd.csopasset.com`。网关**上游**是 OpenAI 直连
      还是 Azure，仍未确认。
- [x] 获得 Base URL：`https://amao-prd.csopasset.com/llm/v1`。
- [ ] 获得 API 文档 —— **未提供**。协议形状（Responses API、`text.format.json_schema`、
      推理项、usage 字段）全部由实测反推，记在 §6.1 与 §6.4，
      并由 `worker/tests/test_provider_contract.py` 的 24 条断言钉住。
- [x] 用 `/models` 确认准确 Model ID —— `gpt-5.6-luna` 在 `/models` 返回的 6 个模型里
      逐字存在，不是宣传名。
- [x] 确认 JSON Schema —— 支持 `json_schema` + `strict: true`，含 `$defs`/`$ref`。
- [ ] 确认限流 —— **未确认**。实测 129 条 / 5 批顺序请求未触发 429；已实现
      `Retry-After` 与带抖动的指数退避，所以限流未知不阻塞运行，只阻塞并发调参。
- [ ] 确认区域和数据保留 —— **未确认**（数据处理地区、日志保留天数、是否用于训练、
      删除机制，四项全部未答复）。已统一发 `store: false` 作为单方面缓解。
- [x] 确认真实评论是否允许发送给该供应商 —— 项目负责人 2026-09-11 明确授权
      「发脱敏后的真实评论」。§11.4 脱敏在**发请求之前**执行，命中禁字段直接抛错，
      不是发完再补救（`worker/tests/test_provider_contract.py` 断言 PII 场景下
      HTTP 调用次数为 0）。
- [x] 保持本文档和 Git 内的 Key 为空 —— 全文无真实 Key；Key 只从 `AI_API_KEY`
      环境变量读取，`worker/.env` 在 `.gitignore` 内。

完成标准「脱敏测试请求可重复成功；供应商和型号可审计」：**达成。**
两轮影子运行共 129 条请求，129 成功 0 失败；每条结果都能回到
`annotation_runs` 里的 provider / model_id / prompt_version / taxonomy_version /
schema_version。注意 `model_id` 记的是**网关返回的**模型名，不是请求里发的别名 ——
网关做模型转发时，只有前者是可审计的事实。

**仍然欠着的**：上游供应商、API 文档、限流、以及数据治理四项。
在这四项补齐之前不应把标注扩大到全量 350k 条评论（理由见 §6.1 末尾）。

### Gate 1：真实 SQL 数据可见

> **本轮未执行**（不在选定范围内）。第一项在 Gate 2 的过程中顺带确认了：
> 本机 `radar.db` 存在且完整（5.27 GB，350,399 条评论 / 504,400 篇帖子 /
> 927,071 条提及，迁移后逐项复核未丢数据）。其余各项属于前端空值适配与
> SQL Provider 冒烟测试，未动。

- [x] 找到 `radar.db` 或从 dump 重建 —— 已存在，无需重建。
- [ ] 只读检查关键表和 `meta_kv`。
- [ ] backend 明确使用 `DATA_PROVIDER=sql`。
- [ ] `/pool` 返回约 120 只并有真实非零计数。
- [ ] 修复官号 `etfMentionsFor` 返回契约。
- [ ] 修复五页 null/unavailable 渲染。
- [ ] 修复错误边界误报。
- [ ] 增加 SQL Provider 五页 smoke test。

完成标准：`annotations=0` 时五页仍稳定显示真实事实和准确缺失态。

### Gate 2：AI 数据结构和 Provider

- [x] 引入数据库迁移 —— Alembic，`radar_db/alembic.ini` ＋ `radar_db/migrations/`。
      `0001` 是基线（冻结迁移引入前 `create_all()` 的产物），`0002` 建 AI 五张表。
      已对本机 5.27 GB 真库执行 `stamp 0001` → `upgrade head`，现处 `0002 (head)`，
      数据逐表复核无损。为什么 `create_all()` 不够：它只建缺失的表，**不改已存在的表，
      而且不报错** —— 见 `radar_db/migrations/README.md`。
- [x] 修复 annotation 主键（§10.3）—— `AUTO_PK = BigInteger().with_variant(Integer, "sqlite")`。
      这个 bug 是**静默**的：`BIGINT` 在 SQLite 下不是 rowid 别名，autoincrement 失效
      但不报错，插第二行才炸。所以测试不看类型，**真插两行**再断言主键互异
      （`worker/tests/test_migrations.py`）。
- [x] 增加 subject、版本、证据、任务和复核表 —— `annotation_runs` / `annotation_jobs` /
      `annotations` / `annotation_evidence` / `review_decisions`。判定单元是
      `(comment_id, subject_code)`（§10.1），一条评论可以对 A 正面、对 B 负面。
- [x] 新建 OpenAI-compatible Client —— `worker/ai/providers/openai_compatible.py`。
      走 `/responses`，不走 `/chat/completions`（原因见 §6.4）；跳过 reasoning 项取
      `message`；记网关返回的模型名。
- [x] 新建结构化输出 Schema —— `worker/ai/schemas.py`，Pydantic v2 同时作为
      线上 JSON Schema 与本地校验的**唯一真源**，避免两份定义漂移。
- [x] 新建 Prompt registry —— `worker/ai/prompts/`。版本以模块里的 `VERSION` 常量为准，
      不以环境变量为准 —— 环境变量能被改错，而它会污染缓存键。
- [x] 实现幂等、重试、dead-letter 和使用量记录 —— 幂等靠 `input_hash`（覆盖正文、
      上下文、prompt/taxonomy/schema 三个版本）；瞬时错误带抖动指数退避；
      schema 错误**二分**定位坏样本而不是判整批死刑；永久错误（400/401）立即中止整轮
      并释放任务，不烧 attempts；超过 max_retries 进 dead-letter。
      用量逐 run 落库，**未知时写 NULL 不写 0**（铁律 2）。
- [x] 对外发送前脱敏 —— `worker/ai/redact.py`。白名单制：只有 §11.4 允许的字段能进
      请求体，出现禁字段（昵称、UID、IP 地区、简介、粉丝数……）在**发请求之前**抛错。
      `worker/tests/test_ai_redact.py` 里有一条守卫断言：`users` 表的**每一列**都在
      `FORBIDDEN_KEYS` 里 —— 将来给 `users` 加列而忘了脱敏，这条会红。

完成标准「100 条脱敏样本影子运行可按 run/model/prompt 追溯」：**达成。**

| 项 | 值 |
|---|---|
| run | `20260911T023821-12c0aa6f`（100 条）、`20260911T024224-35ecc535`（29 条带上下文重判） |
| 成功率 | 129 / 129，0 错误 |
| 用量 | 14,491 input ／ 9,711 output（其中 2,206 reasoning） |
| 产出 | 129 个判定单元、225 行 annotation（180 行现行 ＋ 45 行被 supersede）、68 行证据 |
| 证据 | 全部由程序在原文里定位，**0 条非逐字引用**；定位不到则不存并标 `needs_review` |
| 置信度 | `calibrated_confidence` 129 条全为 NULL —— 模型自报的信心不是校准概率，不许冒充 |

抽样质量（粤语／繁体真实评论）：`'隔離無升幅依隻上年升咁多'` → positive/performance；
`'謝謝'` → irrelevant（没有错判成 neutral）；`'大笨象扮演的是蓄水池的角色…'` →
irrelevant（讲的是汇丰，不是这只 ETF）。

过程中发现并修掉的一个真问题：首轮 40% 判 `needs_context`，因为样本多是单词回复。
`redact.comment_payload` 本来就接受 `post_title` / `parent_comment`（两者都在 §11.4
允许清单内），但组装 payload 时没填。补上后重新入队**恰好**产生 29 个新任务
（21 条回复 ＋ 10 条带标题 − 2 条重叠）—— 这个数字本身就是指纹精度的证明：
正文没变的任务一个都没重复建。这 29 条里 `needs_context` 从 12 降到 8。

**明确没做的**：帖子正文作为上下文。它会让同一篇帖子的正文在一批 30 条里重复至多
30 次，且需要改 prompt。这是一个单独的决定，不在本轮里顺手扩大。

### Gate 3：P0 Hugging Face

> **本轮未执行 —— 阻塞。** 三个阻塞点：(1) 需要先建 3,000 条评论 ＋ 1,000 篇帖子
> 的人工金标，这是人力排期问题，不是代码问题；(2) 概率校准必须在**独立**校准集上做，
> 而校准集还不存在 —— 没有它就只能拿模型自报的 softmax 当概率，那正是
> `calibrated_confidence` 现在全为 NULL 的原因；(3) 模型权重下载与 revision 锁定
> 需要确认内网出口与存储位置。


- [ ] 下载并锁定 Mengzi revision。
- [ ] 下载并锁定 BGE-M3 revision。
- [ ] 下载并锁定 BGE Reranker revision。
- [ ] 建 3,000 条评论、1,000 篇帖子金标。
- [ ] 训练相关性和态度分类头。
- [ ] 完成概率校准。
- [ ] 建向量和 Top-K 重排。
- [ ] 比较本地模型与 GPT。

完成标准：达到业务确认的切片指标；低置信可正确升级 GPT/人工。

### Gate 4：annotations 驱动页面

> **本轮未执行 —— 阻塞在 Gate 2 的下游。** 现有 129 个判定单元全部是
> `pending` / `needs_review`，**没有一条 approved**；而 Gate 4 第一项就是
> 「SqlProvider 读取**已批准** annotations」。要有可批准的量，就得跑全量标注；
> 而全量标注的前置是 §6.1 那四项数据治理确认（区域、日志保留、是否用于训练、
> 删除机制）—— 它们至今未答复。顺序是：补齐治理确认 → 全量标注 → 人工复核 →
> Gate 4。跳过中间任何一步，页面上出现的就是没人担保过的结论。


- [ ] SqlProvider 读取已批准 annotations。
- [ ] 后端聚合 attitude。
- [ ] 接帖子类型、方向和摘要。
- [ ] 接原文证据。
- [ ] 接主题聚类和命名。
- [ ] 接动态负面和合规候选。
- [ ] 前端展示模型版本/AI 待确认语义。

完成标准：AI 模块从 unavailable 逐项切换为真实输出，任何结论可回到原文。

### Gate 5：行情

> **本轮未执行 —— 阻塞在外部权限。** 没有 OpenD 与 HK 行情权限，
> 没有权限就没有 OHLC，而铁律 2 不允许拿任何东西顶替它。K 线字段保持 `null` →
> 「暂不可用」，这是当前唯一诚实的状态。


- [ ] 获得 OpenD 和 HK 行情权限。
- [ ] 建 `market_candles`。
- [ ] 回填小时线和日线。
- [ ] 实现增量补拉。
- [ ] 接通 `candles` 与日度价格字段；阶段观点仍按 Gate 4 的 AI 链路实现。

完成标准：有数据时显示真实 OHLC，休市/缺失时不补造。

### Gate 6：在线采集

> **本轮未执行 —— 阻塞在外部接口。** 富途社区增量数据无法从 Futu OpenAPI 取得
> （§3.1 已述），合法增量接口尚未获得。当前全部数据来自一次性 dump，
> 因此没有新鲜度可言 —— 这一点应当在页面上如实呈现，而不是让用户以为看到的是实时舆情。


- [ ] 获得合法社区增量接口。
- [ ] 建 checkpoint 和 ingestion run。
- [ ] 实现分页、upsert、回补和重放。
- [ ] 新内容自动进入 AI 队列。
- [ ] 缓存随数据版本失效。
- [ ] 建数据新鲜度和失败监控。

完成标准：每小时新增数据可追踪地进入页面，重复运行不重复计数。

---

## 17. 建议验收命令

### 17.1 数据层

```powershell
Invoke-RestMethod 'http://localhost:8008/health'
Invoke-RestMethod 'http://localhost:8008/api/v1/meta'
Invoke-RestMethod 'http://localhost:8008/api/v1/pool?range=d7'
Invoke-RestMethod 'http://localhost:8008/api/v1/officials/posts?range=d7'
Invoke-RestMethod 'http://localhost:8008/api/v1/kol/impact?range=d7'
```

### 17.2 预期 AI 缺失态

在 AI 尚未接入时，下列请求应 HTTP 200 + `status=unavailable`，而不是页面或服务崩溃：

```powershell
Invoke-RestMethod 'http://localhost:8008/api/v1/products/3033/summary?range=d7'
Invoke-RestMethod 'http://localhost:8008/api/v1/products/3033/themes?range=d7'
Invoke-RestMethod 'http://localhost:8008/api/v1/products/3033/compliance?range=d7'
Invoke-RestMethod 'http://localhost:8008/api/v1/products/3033/candles?range=d7'
```

### 17.3 模型接入后

- annotations 行数增加；
- 同一输入和版本重跑不产生冲突重复；
- 每条结果带 run/model/input_hash；
- evidence 可在原文中精确找到；
- API 失败不写入默认 neutral/other；
- 页面仍可在模型暂停时展示旧的有效结果或 unavailable。

---

## 18. 需要项目负责人提供的最小信息

开始实现前只需要补齐以下非密钥信息（2026-09-11 逐项更新）：

| # | 需要的信息 | 状态 |
|---|---|---|
| 1 | `GPT-5.6 Luna` 的供应商名称和官方文档链接 | ⚠️ CSOP 内部网关 `amao-prd`；**无文档**，上游供应商未确认 |
| 2 | OpenAI-compatible Base URL | ✅ 已提供 |
| 3 | `/models` 返回的实际 Model ID | ✅ `gpt-5.6-luna` |
| 4 | 是否允许去标识后的真实评论发送给该供应商 | ✅ 已授权（2026-09-11） |
| 5 | 当前 `radar.db` 的实际路径，或 dump 的本地 SSD 路径 | ✅ 本机库存在且完整 |
| 6 | 社区在线增量数据从 API、数据库还是 ChatInsight 获得 | ❌ 未答复 —— 阻塞 Gate 6 |
| 7 | Futu OpenD 账号是否具备 120 只 HK ETF 的行情权限 | ❌ 未答复 —— 阻塞 Gate 5 |
| 8 | 人工标注负责人和合规信号确认人 | ❌ 未答复 —— 阻塞 Gate 3 与 Gate 4 |

**新增、且优先级高于上面第 6–8 项的四个问题**（阻塞把标注扩大到全量 350k 条评论）：

9. 网关把请求转发给谁，数据在哪个地区处理；
10. 网关与上游的日志保留多少天；
11. 数据是否用于供应商训练；
12. 删除请求怎么提、多久生效。

第 9–12 项未答复期间，标注只在脱敏样本上做。这不是流程洁癖 —— 发出去的是真实用户
写的话，答不上「存在哪、留多久、会不会拿去训练」这三个问题，就不该发第 130 条。

API Key 不需要也不应写入本手册。实现时由项目负责人在本机 `worker/.env` 或生产 Secret 中配置。

---

## 19. 完成定义

项目完整接入不是“某个 API 请求成功”，而是同时满足：

- 五个页面在真实 SQL Provider 下稳定渲染；
- 真实计数与底库抽查一致；
- 未完成 AI/行情区域准确展示 unavailable，不伪造 0；
- 每个 AI 结论具有目标产品、模型版本、Prompt/标签版本和原文证据；
- 模型失败、限流和更换供应商可恢复、可重放；
- 高频分类由本地模型承担，复杂任务才调用外部 GPT；
- 合规信号保持 AI 待确认并有人工作最终确认；
- 行情只来自授权行情源；
- 在线采集只来自合法授权源；
- API Key、用户身份字段和数据库凭据不泄露到前端或 Git。

