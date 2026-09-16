# futu-radar 数据、Hugging Face 模型与 AI API 接入执行手册

> 版本：v1.2（v1.0 初版；v1.1 记录 Gate 0–2 执行结果于 §16；v1.2 新增 §20 重点舆情识别专项、§21 无人工金标路线、§22 公开数据集清单）  
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

### 2.2.1 状态更新（2026-09-11 下午）

§2.2 是本手册 v1.0 写作时的快照。同日 Gate 0、Gate 1、Gate 2 已执行完毕（逐项记录见 §16），
现状变为：

| 项 | 现状 |
|---|---|
| 瘦库 | 本机默认路径已有 5.27 GB 真库，Alembic 迁移至 `0002 (head)` |
| SQL Provider | 五页在真库上稳定渲染，AI／行情区域显示准确缺失态 |
| GPT 接入 | CSOP 网关 `gpt-5.6-luna`，`/responses` ＋ strict JSON Schema，129 条影子运行 0 失败 |
| 标注表 | 五张表已建：runs / jobs / annotations / evidence / review_decisions |
| 已有任务 | `comment_product`（相关性／态度／aspect）、`post_annotation`（类型／方向／摘要） |
| 尚无任务 | **`compliance_signal`（重点舆情五类）**、主题聚类、热议总结、竞品候选 —— 见 §20 |
| 人工复核 | `worker/jobs/review.py` 已可用；129 条全部 `pending`／`needs_review`，0 条 approved。[ADR-0019](adr/0019-ai-auto-publish-no-human-gate.md) 起它是**可选工具**，不再是闸口 |
| 页面 AI 输出 | 当时为「暂不可用」：`SqlProvider` 只读 approved／corrected，而没有人批过。**该门槛已由 [ADR-0019](adr/0019-ai-auto-publish-no-human-gate.md) 取消并实施** —— 现行结论＝链末且非 `rejected`，`pending` 与 `needs_review` 一样可读 |
| 未答复 | 供应商数据治理四项（区域、日志保留、训练使用、删除）；OpenD 行情权限；社区增量源 |

结论：管线**通了**，页面**没亮**，卡在「谁来批准」。项目负责人已裁决**不设批准门槛、不做人工复核与抽检**，规则见 [ADR-0019](adr/0019-ai-auto-publish-no-human-gate.md)；§21 保留为该裁决之前的方案记录。

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

前端工具链**不能在 P: 上跑**（`npm install` 在 SMB 网络盘上装不上，见
[ADR-0018](adr/0018-local-mirror-for-npm.md)）。源码留在 P:，`node_modules` 与 `vite`
只在本地镜像 `%USERPROFILE%\.futu-radar\mirror\frontend` 里存在。

首次（或镜像不存在时）：

```powershell
Set-Location P:\NIKI\futu-radar\frontend
npm run mirror                                   # P: → C:\Users\<你>\.futu-radar\mirror，只同步源码

Set-Location "$env:USERPROFILE\.futu-radar\mirror\frontend"
npm install                                      # 只在镜像里装一次
```

每次预览（**在 P: 上改过源码就必须先重新 `npm run mirror`**，否则跑的是旧代码）：

```powershell
Set-Location P:\NIKI\futu-radar\frontend
npm run mirror

Set-Location "$env:USERPROFILE\.futu-radar\mirror\frontend"
$env:VITE_API_BASE = 'http://localhost:8008/api/v1'   # 指向 4.3 起的那个后端
npm run dev                                          # → http://localhost:5173
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

这一步是 AI 接入前置门槛。**2026-09-11 已全部完成**，逐项落点见 §16 Gate 1；
下表保留为「改之前是什么样」的记录。

需要留意的是：表里列的是**字段级**缺失，而实测抓到的两个硬 TypeError 都是
**整块容器**为 `null`（`hotSummaries` 整池一份、`themes` 整个双极对象）。
字段判空一条都拦不住它们 —— 容器不在字段里。

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
| `null` / `unavailable` | 暂不可用（数值位与环比位是长文案「数据暂不可用」） |
| 空数组 / `empty` | 暂无相关内容（「暂无内容」只用于状态图例，见 PRD §3.6） |
| `low_sample` | 样本不足 |
| `na` | — |
| `review_state = needs_review` | 待确认（[ADR-0019](adr/0019-ai-auto-publish-no-human-gate.md) §2：**唯一**触发。不是「低置信」，也不是「还没人复核」——`calibrated_confidence` 整列为 NULL，`lowConfidence` 在校准概率存在之前不生效） |

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
| 合规信号（重点舆情） | 词表高召回＋GPT 五类判定＋命中依据（§20） | COLD 微调的攻击性检测器做预筛；专用分类器 | `compliance` | 板块 S7/S8/S10、产品 P10；恒为「AI 识别 · 待人工确认」 |
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
      comment_product_v1.py      # 已落地
      post_annotation_v1.py      # 已落地
      compliance_signal_v1.py    # §20，待建
      topic_summary_v1.py        # 待建
    lexicon/
      compliance_zh.py           # §20.3 词表（简／繁／粤／英），待建
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
    review.py
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
- `worker/jobs/review.py`：人工复核，**可选工具**（[ADR-0019](adr/0019-ai-auto-publish-no-human-gate.md)：
  模型写下即发布，没有批准门槛）。`--reject` 是把一条错结论从页面上拿下来的唯一通道；
  `--correct` 留下 Gate 3 训练金标所需的人工对照；`--approve` 只在库里留痕，不改变显示；
- `worker/models/*`：本地 Hugging Face 推理；
- `backend/providers/sql.py`：读取事实和 annotations；
- `backend/core/*`：把原子标签聚合成页面指标；
- 前端：只渲染后端结果和六态，不自行重新算口径。

---

## 13. 人工标注和训练

> 本章是**有人力做金标**时的标准路线。若不想或暂时无法组织人工标注，改走 §21 的
> 弱监督路线；重点舆情（合规关注）按 §20 实现，它在 PRD 里本来就是「AI 识别 · 待人工确认」，
> **不需要金标就能上页面**。两条路线共用 §10 的表结构与 §11 的 Job 规则。

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

> **本轮执行范围（2026-09-11）：Gate 0 ＋ Gate 2，另补 Gate 1。** 前两者由项目
> 负责人明确选定；Gate 1 原不在范围内，但执行过程中发现它并不阻塞（本机瘦库就在
> 默认路径上），八项全部可做，遂一次做完。Gate 3–6 仍未执行，各自的阻塞原因逐条
> 记在下面 —— 没有勾的框，下面一定有一行说明它为什么没勾。

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

> **本轮补做（2026-09-11）。** 原本不在选定范围内，但做 Gate 2 时确认本机
> `radar.db` 就在默认路径上（5.27 GB，350,399 条评论 / 504,400 篇帖子 /
> 927,071 条提及，迁移后逐项复核未丢数据），八项因此全部可做，遂一次做完。

- [x] 找到 `radar.db` 或从 dump 重建 —— 已存在，无需重建。
- [x] 只读检查关键表和 `meta_kv` —— 以 `mode=ro` URI 打开，不靠自觉。
- [x] backend 明确使用 `DATA_PROVIDER=sql` —— 端到端跑通（HTTP，非进程内）。
- [x] `/pool` 返回约 120 只并有真实非零计数 —— d7 下 120 只、45 只非零。
- [x] 修复官号 `etfMentionsFor` 返回契约 —— 由 smoke test 钉住返回**列表**不是计数。
- [x] 修复五页 null/unavailable 渲染 —— 见下。
- [x] 修复错误边界误报 —— `api.js` 已带 `kind`（network/http/envelope/none），
      六态里有屏级断言；smoke test 另钉一条：有效产品的 `/evidence` 必须 200 而不是
      404，因为 404 会让 `ScreenBoundary` 把「这一栏还没标注」误报成「后端连不上」。
- [x] 增加 SQL Provider 五页 smoke test —— `backend/tests/test_real_db_smoke.py`，
      15 条。本机没有那份库时**自动跳过**（`skipif`），所以它进得了常规 `pytest`。
      三条硬约束写在模块 docstring 里：只读打开、不写死任何真值（产品代码与名称
      运行时现查）、断言里不出现 PII。

完成标准「`annotations=0` 时五页仍稳定显示真实事实和准确缺失态」：**达成。**

验证方式是 `frontend/scripts/real-data-check.mjs`（`npm run real-data-check`，
opt-in，不进 `npm test`）：把五页放进真浏览器、对着 `DATA_PROVIDER=sql` 的后端跑，
断言①页面一条 `pageerror` 都没有、②正文里不出现 `NaN`／`undefined`／`null`／
`[object Object]`／`Infinity` 与两句错误边界文案。板块总览还会点开产品抽屉 ——
摘要／主题／负面归类／竞品／合规五块整块 `null` 全在抽屉里，首屏一个都碰不到。

现有三道关口盖不住这件事，所以必须单独有这一条：`guards` 是静态 grep；
`six-state` 打的是**手工构造**的六态 fixture；`diff` 比的是演示数据下的逐字一致。
三道都在演示供数下跑，而 `sql` 的缺失面大得多 —— 实测抓到两个硬 TypeError
（`hotSummaryFor` 的 `null[code]`、`themesFor` 的 `null['positive']`），
两处都是**整块容器**为 `null`，不是字段为 `null`，三道关口全绿。

修掉的两类：
- **取数层容器判空**（`frontend/src/data/radar.js`）：`hotSummaryFor` / `themesFor`
  先判容器再取键，取不到发 `null` 而不是 `{}` —— 六态判定不在取数层。
- **屏内 null 与空集分家**（`productMonitor` / `sectorOverview` 及其子组件）：
  `null` 与 `[]` 在这里是两句不同的话。`[]` 是「聚过类了，这一极没有主题」
  （暂无相关内容），`null` 是「还没聚过」（暂不可用）。合成一个空列表，页面会
  言之凿凿地说某只产品没有负面主题 —— 那是铁律 2 与 PRD §3.6 都禁止的谎。
  新增 `lib/view.js` 的 `naBox(res, keys)`（整块缺失 → 该契约形状的 `unavailable` 态，
  空集合只为让 `.map` 有东西可遍历，**永远配着 `status !== 'ok'` 出现**）。

顺带修掉的一个更要命的：**`updatedAt` 是假的**。它原来手写在
`backend/fixtures/meta.json` 里当口径常量，于是接真库时页面拿演示锚点
`2026-09-02 09:00 HKT` 给真数据落款 —— 而真库数据到 `2026-08-25` 就断了，
整整虚报一周。这不是缺失，是**说谎**，比空着更难发现。已改为随主数据下发
（那才是它的本相：这批数据最后一条帖子的时间），取不到时该键**不出现**，
前端 `stamp()` 渲染「数据暂不可用」。演示侧的值逐字取设计源的 `R.UPDATED`，
逐字比对不受影响。三条后端测试 ＋ 六态第 ⑤ 条断言钉住。

#### Gate 1 补齐项（同一轮，验收之后）

两处是跑真库时才暴露、`real-data-check` 也照不到的：

- [x] **Provider 会认一次死理** —— `SqlProvider` 在构造时读一次 `meta_kv` 就再不回头。
      compose 先起 backend、库还是空的，`_anchor` 就永远是 `None`，**每个端点从此
      恒久「暂不可用」，直到有人重启容器**；ETL 重跑换掉事实表，`_scan` 的缓存也
      照旧不知道。新增 `SqlProvider.refresh()`，由 `get_provider()` 每次取用时调用，
      `meta` 没变就直接返回、一个查询都不多发。配套 `etl.stamp()` 往 `meta_kv` 写
      `etl_generation` —— 值是**行数不是时间戳**，故意的：同一份 `src_*` 幂等重跑
      产出同样的行，缓存本就该留着；写时间戳会把「又跑了一次」误报成「数据变了」。
      `demo` provider 也实现了空的 `refresh()` —— 在接缝处写 `isinstance(p, SqlProvider)`
      会把 provider 的实现细节漏回调用方（ADR-0001）。
- [x] **热力图会悄悄少画格子** —— 面积＝讨论热度，热度是 `null` 就画不出格子；
      而少画几个格子**看不出来**，于是这张图会冒充全市场的全貌。d30 下真有 8 只落在
      这里（其中一只带 46,049 条评论），根因是 0.09% 的 `raw_json` 被截断、转发数没采到。
      现在图下多一行写明「另有 N 只讨论热度暂不可用，未参与面积分配」，榜单里那一格
      也带上悬浮解释。读的是字段（`o.shares == null`），**不在前端重算热度公式**（铁律 1）；
      demo 下 N 恒为 0、整块不渲染，逐字比对因此不受影响。

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

#### Gate 2 补齐项（同一轮，影子运行之后）

影子运行只跑了评论任务，而 §11.2 的帖子任务、§13.2 的人工复核这两条路径当时**一行
代码都没有**。补齐的三项：

- [x] **帖子排队** —— `annotate.enqueue_posts()`。判定单元是帖子本身，不按标的展开：
      一篇挂三只标的的行情解读仍然只是**一篇**行情解读，展开会让它被判三次，还可能
      判出三个不同的类型。过滤条件是「标题与正文至少一个非空」——只看正文会把整类
      只有标题的帖子悄悄排除，而官号动态那一屏主要就靠它们。
- [x] **人工复核 CLI** —— `worker/jobs/review.py`。裁决只追加（`review_decisions` 每次
      插新行），改正另起一条 annotation 用 `supersedes_id` 链回旧行、**不原地改旧值**
      （模型当初判的是什么，是 §13.2 训练金标时的对照）。否决必须给受控词表里的理由。
      这也是 ADR-0017 §4 的落点：「待确认」由 `review_state` 驱动 —— 那是一件事实
      （有没有人看过），不是模型自报的伪概率。
- [x] **「没有」也要写下来** —— 帖子任务原先在 `summary` 或 `direction` 为 null 时
      **不写行**。但 schema 里这两个键必填，模型必须显式写 null，而 Prompt 给了它们
      各自的含义（「帖子没有可读正文」「帖子没有表达任何操作」）——那是**结论**，
      不是没回答。不写的后果不是少一行数据，是「已标注、确实没有」和「这帖压根没
      标注过」在库里变成同一个样子，而页面上它们相反。现在落 `false` 占位：类型与
      摘要字符串、方向枚举都不同，读取方一眼能分开。

```sh
cd worker
.venv/Scripts/python -m jobs.annotate --task post_annotation --limit 200   # 排队并跑
.venv/Scripts/python -m jobs.review --queue                                # 看待复核队列
.venv/Scripts/python -m jobs.review --next                                 # 队首一条的详情与证据
.venv/Scripts/python -m jobs.review --id 42 --approve --reviewer <你的名字>
.venv/Scripts/python -m jobs.review --id 42 --reject  --reviewer <你的名字> --reason hallucinated_evidence
.venv/Scripts/python -m jobs.review --id 42 --correct '"positive"' --reviewer <你的名字>
```

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

#### Gate 3-alt：零金标冷启动（已按 [ADR-0019](adr/0019-ai-auto-publish-no-human-gate.md) 定案）

> 项目负责人 2026-09-11 裁决：**不做人工金标、不做复核、不做抽检、不设批准门槛**。
> 上面 Gate 3 的金标／训练／校准各项因此**挂起**（本地模型改为直接用 GPT 标签自训练，
> 没有校准集 ⇒ `calibrated_confidence` 长期 NULL）。发布规则、徽章映射与实施清单
> 全部以 ADR-0019 为准，此处只列与本手册其余章节的衔接项。

- [x] 落地 §20 的合规词表 `worker/ai/lexicon/compliance_zh.py`（简／繁／粤／英四套写法）——
      **角色变了**（ADR-0020 §3）：不再是预筛闸门，是 `jobs/audit.py --lexicon-recall` 的召回审计。
- [x] ~~新增 `compliance_signal` 任务~~ → 合规五类**并入** `comment_product` v2 的单次调用
      （`compliance_tags` / `compliance_rationale` / `compliance_evidence`，kind=`compliance`，
      空数组也落库）。每条评论本来就要发一遍，再为合规发一遍是两倍请求。
- [ ] ~~用 §22 可商用数据集预热攻击性预筛器~~ —— 挂起：ADR-0020 试点只用 LLM，不引入本地模型。
- [x] 按 ADR-0019 实施清单第 1–9 项改 `SqlProvider`／`core`／`/meta`／前端徽章。
- [x] `/meta.aiValidation = "none"`，面板写明「AI 结论由模型自动生成，未经人工验证」。

完成标准：合规关注、态度、帖子三件套在页面上有真实输出；每条都带「AI 生成／AI 识别」
徽章与原文证据；`/meta` 与面板如实写明未经人工验证。

### Gate 4：annotations 驱动页面

> **读路径已通，数据还没到 —— 阻塞在第 10 项。**
>
> [ADR-0019](adr/0019-ai-auto-publish-no-human-gate.md)（2026-09-11 定案）取消了批准门槛：
> **现行结论 = 链末（没被任何一行 supersede）且 `review_state != 'rejected'`**，同一链末
> 多行取 `created_at` 最新；`pending` 与 `needs_review` 一样可读。`review.py` 随之降级为
> 可选工具，`--reject` 是仅剩的下线通道。这条规则的唯一实现处是
> `backend/providers/sql.py` 的 `_current_annotations()`。
>
> 它的实施清单第 1–9 项**已完成**（读取规则、态度聚合、帖子三件套、证据定位、合规四态、
> `/meta.aiValidation`、徽章与如实声明、测试、文档）——后端测试全绿，有标注即出真值、
> 无标注仍是缺失态。
>
> 卡住的是第 10 项**全量排队与运行**：它等 §6.1 数据治理四项答复与负责人授权，不是技术
> 问题。在它跑完之前，库里只有 Gate 0–2 留下的 129 个影子判定单元，覆盖不到页面上的
> 观察窗口，所以**界面上的 AI 模块多数仍显示缺失态** —— 那是「还没标」，不是「读不出」。
>
> 页面上出现的是**未经人工验证**的结论，`/meta.aiValidation="none"`、板块总览 S6 与
> 产品监控 P7 三处必须一起如实标明；不许出现「已核验」「准确率 xx%」。


- [x] SqlProvider 按 ADR-0019 第 1 条读取现行 annotations（非 `rejected` 链末行）。
- [x] 后端聚合 attitude（正／负／中按判定单元计数，阈值判定走 `core/attitude.py`）。
- [x] 接帖子类型、方向和摘要。
- [x] 接原文证据（`evidenceIdx`／`typeEvidence`／`evidenceFor`，引文可在原文逐字定位）。
- [x] 接合规候选的**读路径**（`na`／`unavailable`／`empty`／`ok` 四态）；写入方
      `compliance_signal` 任务仍缺，见 §20 与 Gate 3-alt。
- [x] 接主题与命名 —— **不是聚类**：主题＝极性 × aspect 桶（`backend/core/themes.py` 计数），
      名字与摘要由 Layer B 的 `theme_label` 生成物给（[ADR-0020](adr/0020-llm-only-90d-pilot.md) §5）。
- [x] 接动态负面（`neg_category`）：负面 aspect 桶＋`core/lifecycle.py` 的新增／持续／消退与关注程度。
- [x] 接热议总结／舆情总结／话题情绪／阶段观点／关联竞品候选／产品相关 KOL／KOL 其他产品观点
      （`synthesis_outputs` 七种 kind ＋ `kol_comment_opinion` 任务；读路径 `sql.py`，三态分明）。
- [x] 前端按 `review_state` 渲染徽章，`/meta` 与面板写明未经人工验证。
- [ ] **全量排队与运行**（ADR-0019 第 10 项 → ADR-0020 §8 的 90 天试点）——
      命令见 [docs/llm-90d-operations.md](llm-90d-operations.md)；负责人 2026-09-14 已授权，
      §6.1 四项与网关价格仍应索取。

完成标准：AI 模块从 unavailable 逐项切换为真实输出，任何结论可回到原文。

#### Gate 4-alt：90 天 LLM 试点（ADR-0020，2026-09-14）

> 入口不再是 `annotate --enqueue`，而是 **`jobs/extract.py`**（按 ETF × 时间段抽取、五条规则预过滤、
> 打 `scope_id`）→ **`jobs/pipeline.py --scope`**（评论 → KOL 评论 → 帖子 → Layer B → 报表，幂等续跑）。
> 放量前先跑 `scripts/probe_gateway.py`（flex／batches／缓存／限流）与 `scripts/calibrate.py`
> （b=1 vs b=30、v1 vs v2 一致率、仅个股规则误杀抽查）。

- [x] 词表：`worker/ai/lexicon/{product_aliases,offpool_stocks,compliance_zh}.py`
- [x] 抽取＋预过滤：`worker/jobs/extract.py`、`worker/ai/prefilter.py`
- [x] 评论 v2 七维、Prompt 针对富途评论区改写、产品别名进 payload、帖子正文上下文
- [x] 并发、scope 领取、`--dry-run`、`--budget-requests`
- [x] Layer B：`worker/ai/synth.py`、`worker/jobs/synthesize.py`、`synthesis_outputs`（Alembic 0003）
- [x] 读路径九个方法；parity 测试按六态与扩展键比对
- [x] 报表：`worker/jobs/audit.py`
- [ ] 本机：切库、探测、实验、全量运行、`real-data-check` —— 见操作单。

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
- 合规信号恒为「AI 识别 · 待人工确认」，只标信号与原文，不判真伪（ADR-0019：不设人工门槛）；
- 行情只来自授权行情源；
- 在线采集只来自合法授权源；
- API Key、用户身份字段和数据库凭据不泄露到前端或 Git。

---

## 20. 重点舆情（需合规关注）识别专项

### 20.1 这一项为什么可以不等人工金标

PRD §4.2 P10 与 §3.4 对这一模块的定义本身就是：**「AI 识别 · 待人工确认」**，系统只标记
风险信号并给出原文与命中依据，不判定言论真伪、是否违法或产品是否违规，不生成风险分数，
不触发通知。也就是说，这一模块在设计上**从来没有要求模型结论被人批准后才能展示**，
「待人工确认」就是它的正常展示态。

因此它是所有 AI 模块里最适合先上页面的一项：需要的是**高召回＋可追溯的命中依据**，
不是经校准的准确率。人工只需在页面上看到之后决定要不要跟进 —— 那是业务动作，不是标注动作。

### 20.2 五类信号：定义、正例、反例

标签键与中文名逐字取自设计源 `design/radar-data.js` 的 `RISK_TAG`（PRD §4.2 P10 冻结）。
一条内容可同时命中多类；同业产品（`ownership=peer`）**不纳入识别**，接口返回 `status: na`。

| 键 | 中文 | 判定要点 | 正例（改写自设计源 `RISK_BANK`） | 不算（反例） |
|---|---|---|---|---|
| `regulatory_complaint` | 监管举报 | 表达**已经**或**打算**向监管／投诉机构（证监会／SFC、消委会、金管局、港交所、12386、警方）投诉、举报、报案；或声称已提交 | 「交易记录我已经整理好了，下周直接去证监会投诉{name}」 | 「这种产品早该被监管」——是评论监管，不是举报意图 |
| `serious_allegation` | 严重指控 | 对发行人、做市商、平台作出**违法／违规性质**的指控：操纵、利益输送、内幕、欺诈、虚假宣传、挪用、洗钱、割韭菜（作指控用时） | 「做市商和发行人之间肯定有利益输送」「明显是有人在操纵盘口」 | 「跟踪误差太大」——是产品负面，不是指控 |
| `unverified_claim` | 疑似未经证实指控 | 传播**未附依据**的重大事实断言：清盘、停牌、被查、跑路、暴雷；常带「听说／据说／内部消息／有人说」 | 「听说{name}下季度要清盘，还没卖的赶紧走」 | 引用公告、新闻链接或明确数据来源的陈述 |
| `mobilization` | 煽动扩散 | 号召他人**集体行动或转发扩散**：集体投诉、留名、刷一星、转发提醒、一起去 | 「凑够 50 个人一起去 SFC 和消委会集体投诉」「大家一起去各个平台刷一星」 | 「建议大家看清风险再买」——是提醒，不是组织行动 |
| `compliance_concern` | 合规质疑 | 对**销售／披露／适当性环节**提出合规疑问：宣传表述、风险提示位置、KYC／风险测评与产品风险等级不匹配、费率披露 | 「开户问卷是保守型，却能直接买到两倍杠杆产品，适当性评估是怎么过的？」 | 对费率高低本身的不满（那是产品负面 aspect=fee） |

**三层负面必须分开存**（PRD §3.4）：消极观点（`attitude=negative`）、负面舆情类别
（`neg_category`）、重点舆情（`compliance`）是三个 kind。「跟踪误差太大」只落前两者；
「跟踪误差就是虚假宣传」同时落 `attitude=negative` 与 `compliance=[serious_allegation]`。

### 20.3 三层识别管线

```text
评论／帖子正文（仅 own 产品；peer → na）
  ├─ L1 词表高召回（worker/ai/lexicon/compliance_zh.py）
  │     命中任一类词表 ⇒ 排队；未命中的抽样 5% 也排队（防词表漏召回）
  ├─ L2 GPT 结构化判定（task=compliance_signal，strict JSON Schema）
  │     输出：tags[]、evidence（原文片段）、rationale（命中依据 ≤40 字）、needs_review
  │     evidence 由 ai/evidence.py 定位；定位不到 ⇒ needs_review，但结论保留
  └─ L3 展示：backend/core/narrative.compliance_for 聚合
        每条恒带「AI 识别 · 待人工确认」；review.py 的 approve/reject 只影响
        「是否已有人看过」这一栏，不改变展示资格
```

L1 词表种子（实现时**按四种写法各写一份**，并允许词表版本进入 `taxonomy_version`）：

| 类 | 机构／对象 | 动作／指控词 | 传闻／号召词 |
|---|---|---|---|
| 监管举报 | 证监会、證監會、SFC、消委会、消委會、金管局、HKMA、港交所、HKEX、12386、警方、報警、报警 | 投诉、投訴、举报、舉報、报案、報案、告、起诉、起訴、集体诉讼、集體訴訟、维权、維權 | — |
| 严重指控 | 发行人、發行人、做市商、莊家、庄家、平台、券商 | 操纵、操縱、利益输送、利益輸送、内幕、內幕、欺诈、詐騙、骗、騙、老千、割韭菜、虚假宣传、虛假宣傳、误导、誤導、挪用、洗钱、洗錢、黑箱 | — |
| 疑似未经证实 | 清盘、清盤、停牌、退市、被查、跑路、爆雷、暴雷、资不抵债 | — | 听说、聽說、據說、据说、内部消息、內幕消息、有人说、有人講、传、傳、小道 |
| 煽动扩散 | — | 集体、集體、一起、大家、留名、报名、刷一星、差评、差評 | 转发、轉發、帮转、幫轉、扩散、擴散、提醒身边、提醒身邊、快走、赶紧卖、趕緊賣 |
| 合规质疑 | 风险提示、風險提示、招股书、招股書、宣传页、宣傳頁、KYC、风险测评、風險評估、适当性、適當性 | 合规吗、合規嗎、合法吗、能这样卖、可以咁賣、没披露、冇披露、藏在、点进三层 | — |

词表只做**召回**，不做判定；命中率、每类命中量、GPT 否决率按周记入 `annotation_runs`
附表，用于迭代词表。

### 20.4 数据契约

**Schema（`worker/ai/schemas.py` 新增 `ComplianceAnnotation`）**

```json
{
  "item_id": "comment:123|product:3033",
  "tags": ["regulatory_complaint", "mobilization"],
  "evidence": "凑够 50 个人一起去 SFC 和消委会集体投诉",
  "rationale": "号召集体投诉并点名监管机构",
  "needs_review": false
}
```

- `tags` 为空数组 ＝ 「查过了，不是重点舆情」，**必须落库**（kind=`compliance`，
  value `{"tags": []}`），否则「已扫描无命中」与「未扫描」在库里无法区分（同 §16 Gate 2
  「没有也要写下来」）。
- `rationale` 是 PRD 的「AI 命中依据」，≤40 字，允许是模型的话；`evidence` 必须是原文。
- `tags` 非空但 `evidence` 定位不到 ⇒ `needs_review`，仍展示，徽章不变。

**排队**：`enqueue_compliance(engine, cfg, codes=own_codes)`，评论按 `(comment_id, subject_code)`、
帖子按 `(feed_id, NO_SUBJECT)`；只排 `ownership=own` 的产品。

**读取**：`SqlProvider.compliance_for(code, range)` 读 kind=`compliance` 且
`review_state != 'rejected'` 的链末行（**全部**，因为展示态本身就是待确认；与 ADR-0019 第 1 条同一规则）；`peer` 直接 `na`；区间内无命中 ⇒ `empty`；
该产品区间内一条都没扫过 ⇒ `unavailable`。`pool().complianceCount` 同源。

### 20.5 边界（PRD §4.2 P10 逐字约束）

- 不输出「属实／不属实」；不输出风险分数、P0/P1 等级；不触发通知或处置。
- 每条必须能回到原文（证据侧栏 kind=`risk`）。
- 页面免责文案逐字沿用设计源：「系统只识别风险信号并提供原文，不判定言论真伪、是否违法或产品是否违规」。
- 发往外部模型前仍走 §11.4 脱敏；`rationale` 不得包含作者身份。

---

## 21. 不依赖人工金标的替代路线（弱监督）

> **本章状态**：写于 2026-09-11 上午，作为"如何在没有金标的情况下仍保留一道机器放行门槛"的
> 方案。同日项目负责人裁决**连这道门槛也不要**——不做一致性放行、不做抽检、不加 `auto_approved`，
> 全部 AI 结论直接发布。现行规则见 [ADR-0019](adr/0019-ai-auto-publish-no-human-gate.md)；
> 本章保留为决策记录，§21.2 中的手段可作为**可选**质量信号使用，但都不是发布条件。

### 21.1 先说清代价

没有人工金标，就**没有**可对外陈述的准确率、召回率或校准概率。这不是工程能绕过的：
准确率是「模型答案 vs 人类答案」的比值，分母不存在，比值就不存在。所以走这条路线时：

- `calibrated_confidence` 继续全为 NULL；
- `/meta` 增加 `aiValidation: "none" | "spot_check" | "gold"` 字段，页面口径面板照实显示；
- 所有 AI 模块徽章为「AI 生成 · 待确认」或「AI 识别 · 待人工确认」，不出现「已核验」。

这条路线换来的是：**页面今天就能亮**，而不是等 3,000 条金标排期。

### 21.2 用什么代替金标

| 替代手段 | 做什么 | 代替的是什么 |
|---|---|---|
| 双 Prompt／双模型一致性 | 同一输入跑两套独立 Prompt（或 GPT ＋ 本地模型）；一致 ⇒ `auto_approved`，不一致 ⇒ `needs_review` | 代替「人批准」作为放行条件 |
| 证据定位率 | 模型引文必须在原文逐字定位（已实现） | 代替「引文是否可信」的人工检查 |
| 词表×模型交叉表 | 词表命中而模型否决、模型命中而词表未召回，两格按周复盘 | 代替错误分析 |
| 公开数据集 warm-start | 用 §22 中**许可允许**的数据集先微调本地分类器，再用 GPT 标签自训练 | 代替金标训练集（仅限任务同构的部分） |
| 规则硬约束 | `irrelevant ⇒ attitude=null`、`peer ⇒ compliance=na` 等已在 Schema 与 Provider 层强制 | 代替口径层面的人工复核 |
| 200 条抽检（建议，非必须） | 分层抽 150 评论＋50 帖子，只算错误率，不回流训练 | 用最小人力换一个可陈述的数字 |

### 21.3 需要的一条新决策（ADR-0019 —— 已写，且**未采纳**下列 `auto_approved` 方案）

> [ADR-0019](adr/0019-ai-auto-publish-no-human-gate.md) 的裁决是**不加** `auto_approved`、
> **不设**任何放行条件；下面 1–3 条是被否决的原提案，第 4、5 条在 ADR-0019 中以另一种形式保留。

原提案：现行 [ADR-0017](adr/0017-ai-annotation-pipeline-production.md) §4 规定 `SqlProvider` 只读
`approved`／`corrected`。零金标路线需要：

1. `review_state` 增加 `auto_approved`（机器按 §21.2 一致性规则放行）；
2. `SqlProvider` 读 `auto_approved`，前端徽章「AI 生成 · 待确认」；
3. `compliance` 类不适用 `auto_approved`——它按 §20.4 全部展示、恒为「AI 识别 · 待人工确认」；
4. `review.py` 的 approve／reject 仍有效：人批过的覆盖机器放行，人否决的立即下线；
5. PRD §3.5／§3.9 的 `lowConfidence=0.7` 改为**仅当 `calibrated_confidence` 非 NULL 时生效**，
   否则「待确认」完全由 `review_state` 驱动 —— 这同时解决了 §16 里悬着的「0.7 还算不算数」。

不写这条 ADR 就改 Provider，等于悄悄推翻 ADR-0017；写了它，历史决策与现行决策都可追溯。

### 21.4 执行顺序（已被 ADR-0019 实施清单取代）

原顺序中的「第二套 Prompt 跑一致性」「200 条抽检」两步按裁决取消。现行顺序：

1. 落 §20 合规词表与 `compliance_signal` 任务。
2. ~~按 ADR-0019 实施清单第 1–9 项改 `SqlProvider`／`core`／`/meta`／前端徽章。~~
   **已完成（2026-09-11）**：读取规则、态度聚合、帖子三件套、证据、合规四态、
   `aiValidation`、徽章与声明、测试、文档。这九项不发请求、不花钱，所以先做完。
3. **排队与运行（第 10 项）等 §6.1 数据治理四项答复与负责人授权**。在此之前
   一条请求都不发 —— 包括「只跑 own 产品近 30 天」这类缩小版，那仍然是把评论正文
   送出去，治理问题一个字都没少。

---

## 22. 公开数据集清单（按功能分组，含许可证判断）

> 访问日期 2026-09-11。**许可证是使用前提，不是脚注**：标「可商用」的才能进训练集；
> 标「需确认」的先联系作者或法务；标「仅研究」的只能做离线对照，不进生产模型。
> 所有公开数据都与本项目的「ETF 产品态度／重点舆情」口径不完全同构，只能做
> warm-start、词表挖掘或对照，不能替代本项目文本上的验证。

### 22.1 重点舆情（合规关注）相关

| 数据集 | 链接 | 规模／标签 | 许可 | 对应信号 | 用法 |
|---|---|---|---|---|---|
| COLDataset（清华 CoAI） | [GitHub](https://github.com/thu-coai/COLDataset) · [HF 数据集](https://huggingface.co/datasets/thu-coai/cold) | 37,480 条中文评论，二分类 offensive；测试集细分「攻击个人／攻击群体」 | **Apache-2.0，可商用** | 煽动扩散、攻击性指控的预筛 | 直接微调预筛器；或用官方开箱模型 [`thu-coai/roberta-base-cold`](https://huggingface.co/thu-coai/roberta-base-cold)（macro-F1 82.39） |
| ToxiCN（大连理工） | [GitHub](https://github.com/DUT-lujunyu/ToxiCN) · [HF](https://huggingface.co/datasets/JunyuLu/ToxiCN) | 12k 条知乎／贴吧，细粒度毒性 | **CC BY-NC-ND 4.0，禁商用** | 同上 | 仅离线对照，**不进训练** |
| 中文谣言数据集 + CED（清华 THUNLP） | [GitHub](https://github.com/thunlp/Chinese_Rumor_Dataset) | 31,669 条微博不实信息举报平台谣言；CED 子集含 1,538 谣言／1,849 非谣言及转发评论 | 仓库未声明许可，**需确认** | 疑似未经证实指控（「听说／据说」类传闻表达） | 挖掘传闻表达词表；许可确认后可作 warm-start |
| Ma et al. 2016 Weibo 谣言集（rumdect） | [下载](http://alt.qcri.org/~wgao/data/rumdect.zip) | 4,664 条微博事件，rumor／non-rumor | 学术发布，未声明许可，**需确认** | 同上 | 对照 |
| Weibo21 | [GitHub](https://github.com/kennqiang/MDFEND-Weibo21) | 9,128 条，含「财经」域 | **需申请**，学术用途 | 同上 | 仅研究对照 |
| MCFEND（WWW 2024） | [官网](https://trustworthycomp.github.io/mcfend/) · [GitHub](https://github.com/TrustworthyComp/MCFEND) | 23,974 条多源中文假新闻，14 家事实核查机构 | 学术用途，**不可再分发** | 同上 | 仅研究对照 |
| CCF BDCI 2019 金融信息负面及主体判定（国家互联网应急中心出题） | [DataFountain](https://www.datafountain.cn/competitions/353/datasets) · [Heywhale 镜像](https://www.heywhale.com/mw/dataset/5e09a9eb2823a10036b126c0/file) | 金融文本是否含实体负面信息 ＋ 负面主体 | 竞赛条款，**需确认** | 严重指控（对发行人／平台的负面主体判定） | 与本项目「对指定 ETF 的指控」最同构；许可确认后 warm-start |
| FinChina-SA（FinLLM@IJCAI'23） | [GitHub](https://github.com/YerayL/FinChina-SA) · [论文](https://arxiv.org/abs/2306.14096) | 11,036 篇新闻、21,272 实体情感、**190 类预警类型** | 仓库 Apache-2.0，但新闻正文权利未说明，**需确认** | 严重指控 taxonomy 参考 | 用其 190 类预警名做本项目「严重指控」子类词表来源，不直接训练 |
| BBT-CFLEB FinNSP | [GitHub](https://github.com/supersymmetry-technologies/BBT-FinCUGE-Applications) | 4,800／600／600，负面消息及其主体 | 仓库未声明许可，**需确认** | 严重指控 | 对照 |
| DuEE-fin（百度） | [AI Studio](https://aistudio.baidu.com/competition/detail/46) | 1.17 万篇公告，13 类金融事件、92 论元 | 需注册 AI Studio，**需确认** | 事件抽取结构参考 | 事件类型偏公告（收购、质押、亏损等），**无监管举报类**；只借用 trigger／argument 标注格式 |
| CCL2023 电信网络诈骗案件分类（哈工大） | [GitHub](https://github.com/GJSeason/CCL2023-FCC) | 82,210 训练／12 类，公安反诈平台脱敏笔录 | CodaLab **需申请** | 「骗／诈骗／老千」类表达 | 仅用于挖掘欺诈指控表达词表 |
| Telecom_Fraud_Texts_8 | [GitHub](https://github.com/ChangMianRen/Telecom_Fraud_Texts_8) | 八分类诈骗文本 | GPL-3.0 且声明**禁商用** | 同上 | 仅研究对照 |
| 黑猫投诉数据集（新浪） | [说明页](https://textdata.cn/blog/2025-03-05-consumer-complaint-dataset/) | 1,531 万条投诉：标题、问题、要求、对象、进度 | 整理方声明**科研用途**，原平台条款未核 | 监管举报（投诉意图表达） | 挖掘「投诉／维权／要求退款」表达；**不进生产训练** |
| CnOpenData 消费者在线投诉 | [数据页](https://www.cnopendata.com/data/m/Platform_Eco/xf-tousu.html) | 投诉内容、对象、状态、金额 | 商业数据商，**需采购** | 同上 | 可选 |
| CFPB Consumer Complaint Database（美国） | [官方 API](https://cfpb.github.io/api/ccdb/) · [TFDS 说明](https://www.tensorflow.org/datasets/community_catalog/huggingface/consumer-finance-complaints) | 约 798 万条金融投诉，`Product / Issue / Sub-issue` 分类 | 美国政府公开数据，**可用** | 投诉分类体系参考 | **英文**；只借用 Issue taxonomy 设计合规质疑子类，不训练中文模型 |
| CCF BDCI 2021 产品评论观点提取（中原银行） | [DataFountain](https://www.datafountain.cn/competitions/529/) | 7,528 条银行产品评论，情感 ＋ BIO 实体（产品／指标／评价词） | 竞赛条款，**需确认** | 合规质疑／产品负面 aspect | 金融产品投诉式短评最接近本项目文体 |

### 22.2 评论态度／相关性相关（补充 §13 与 research 文档）

| 数据集 | 链接 | 规模／标签 | 许可 | 用法 |
|---|---|---|---|---|
| FinFE（BBT-CFLEB） | [GitHub](https://github.com/supersymmetry-technologies/BBT-FinCUGE-Applications) · [HF 指令版](https://huggingface.co/datasets/Maciel/FinCUGE-Instruction) | 股吧／雪球三分类情感，训练 8,000 | 原仓库未声明；HF 整理版标 Apache-2.0，**以原仓库为准需确认** | 中文股票社区语体最接近；许可确认后 warm-start |
| Eland Entity Sentiment（繁体） | [HF](https://huggingface.co/datasets/p988744/eland-entity-sentiment-zh) | 433 条实体级三分类，含多实体／隐含／反讽标记 | **Apache-2.0，可商用** | 繁体、目标级、量小；作 smoke test 与 few-shot 示例来源 |
| Eland Sentiment（繁体） | [HF](https://huggingface.co/datasets/p988744/eland-sentiment-zh) | 台股文本整体／实体／观点三任务 | **Apache-2.0，可商用** | 繁体 warm-start |
| ASAP（美团） | [GitHub](https://github.com/Meituan-Dianping/asap) | 46,730 条点评，18 aspect × 四态 | **Apache-2.0，可商用** | 非金融；只学 aspect 结构与多任务训练 |
| CFLUE 股票评论 500 条 | [GitHub](https://github.com/aliyun/cflue) | 5 位分析师标注对象级三分类 | **CC BY-NC-SA 4.0，禁商用** | 仅作标注指南与协议参考 |
| C-STANCE | [GitHub](https://github.com/chenyez/C-STANCE) | 48,126 微博—目标对，favor/against/neutral | 未声明，**需确认** | target-aware 立场任务设计参考 |
| SMP2020-EWECT | [官网](https://smp2020ewect.github.io/) | 微博六类情绪 | 未声明，**需确认** | 情绪辅助任务 |
| OpenRice 粤语情感 | [GitHub](https://github.com/toastynews/openrice-senti) | 港式粤语餐厅评论，smile/ok/cry 三类平衡 | **CC BY 4.0，可商用** | 粤语、繁体语言适配（非金融） |
| HK Content Corpus | [Zenodo](https://doi.org/10.5281/zenodo.16882351) · [HF](https://huggingface.co/datasets/SolarisCipher/hk_content_corpus) | LIHKG、OpenRice 等港式繁体语料，无标签 | 公开网页来源，**版权需逐源审查** | 仅继续预训练／词表，不作标签 |
| FinGPT sentiment-train | [HF](https://huggingface.co/datasets/FinGPT/fingpt-sentiment-train) | 76,772 条金融情感指令（多来源聚合） | 聚合许可不一，**逐源确认** | 英文为主，弱监督对照 |
| Financial PhraseBank | [HF](https://huggingface.co/datasets/takala/financial_phrasebank) | 4,840 条英文金融新闻句 | **CC BY-NC-SA 3.0，禁商用** | 仅标注一致性方法参考 |

### 22.3 可直接商用的最短清单

如果只想先动手，且不想碰任何许可灰区，今天就能用的是：

1. **COLDataset ＋ `thu-coai/roberta-base-cold`**（Apache-2.0）：攻击性／煽动预筛器；
2. **Eland Entity Sentiment ＋ Eland Sentiment**（Apache-2.0）：繁体目标级情感 smoke test；
3. **ASAP**（Apache-2.0）：aspect 多任务结构；
4. **OpenRice 粤语**（CC BY 4.0）：粤语适配；
5. **CFPB**（美国政府公开）：投诉 taxonomy 参考。

其余全部以「需确认」处理。**本项目自己的富途评论**仍然是唯一与业务口径完全同构的数据 ——
公开数据只能让模型「见过中文金融社区」，不能让它知道「3033 的点差」算产品负面而
「恒指要跌」不算。

## 23. 全自家产品与 FMP 行情自动链路（2026-09-14）

### 23.1 运行入口与边界

本节是全量运行的现行入口。此前两产品试跑记录保留，但不代表全 61 只已经完成。
首次导入的完整日仍为 `2026-08-25`；主月度窗口 `mtd` 为 `08-01..08-25`。
六个日期档位及其基准窗口的并集起于 `2026-06-27`。这些额外数据只用于必要基准，
不得称为完整 8 月，8 月 26 日不完整及 27..31 日缺失仍保留原状。

仓库根目录执行：

```powershell
uv pip install --python backend\.venv\Scripts\python.exe -r backend\requirements.txt
uv pip install --python worker\.venv\Scripts\python.exe -r worker\requirements.txt
worker\.venv\Scripts\python.exe -X utf8 -m alembic -c radar_db\alembic.ini upgrade head
worker\.venv\Scripts\python.exe -X utf8 worker\jobs\full_own.py --watch --max-items 300
```

- `--watch` 是持久 worker，会持续调用已配置的模型；没有模型费用上限，但继续遵守供应商限流、退避和错误检查。
- 默认按主数据选择恰好 61 只自家产品，逐产品、逐轮处理；不扩展到同业全量分析。
- 当前使用逐条推理，批量稳定性未达标不自动切回 30 条合批。模型结论未经人工验证，`aiValidation=none`。
- 无 `--anchor` 时跟随源声明的完整日；带 `--anchor 2026-08-25` 可固定试验，源锚点变化时暂停。
- 旧 scope 的同输入任务通过 `analysis_scope_jobs` 关联复用，不重复收费；任务完成后才生成该产品最终汇总。
- 数据库租约阻止第二个全产品 worker；失去租约或遇到永久模型错误时暂停，不能把暂停当完成。
- 中断后运行同一入口恢复；`failed/dead` 需要排查，不自动清空、假设成功或无上限重发。
- 页面显示后端进度。`complete` 表示该产品候选已经处理完（包括确实没有候选），不是全部产品都有足够样本生成结论。
- 本轮现场备份为 `%LOCALAPPDATA%\futu-radar\backups\radar-before-full-own-20260914-191830.db`，已通过 SQLite `quick_check`。
- 数据库迁移至 `0007`：新增 scope 关联、运行租约、行情事实、来源快照和现行标注查询索引。不得重复导入/重建已有瘦库。

### 23.2 FMP 行情

密钥只放在被忽略的 `worker/.env` 或环境变量 `FMP_API_KEY`，禁止 `VITE_` 前缀、日志、URL导出、测试fixture保存密钥。
`FMP_BASE_URL` 必须为 `https://financialmodelingprep.com/stable`，不要带 Markdown 链接括号。

```powershell
worker\.venv\Scripts\python.exe -X utf8 worker\jobs\sync_prices.py --from 2026-06-27 --to 2026-08-25
```

默认全自家；`--codes 3033,2802` 可定向；成功窗口幂等跳过，`--force` 重新同步而非删除旧数据。
全产品 worker 在存在 FMP 配置时每小时同步一次。API 查询只读本地行情表，不现场调用 FMP。

- 先用 profile 验证标的、港股交易所、HKD及ETF身份；不能单凭补零拼代码就使用结果。
- 日线使用 `historical-price-eod/full`，小时图由 `historical-chart/30min` 聚合；默认补齐最近两个完整数据日的分钟线。
- `price_bars.timestamp` 是标准化的港交所本地时间，`session_date` 为 HKT 交易日；不与 UTC 朴素时间混用。
- `price_instruments` 保存验证映射，`price_syncs` 保存窗口状态和原因，`price_bars` 保存 OHLCV 与拆股调整口径。
- 本轮已实测 3033、2802、7226 的月度与小时 K；7709 日线有值，分钟历史返回空。
- 全池初轮约 19 只日线、17 只分钟线同步成功；其他产品分别为映射未验证或空响应，后续同步结果以 `price_syncs` 为准，不能声称 61 只行情均已覆盖。
- 空响应不等同于休市或确认没有该标的；权限错误、无历史、缺分段均不补零。失败不覆盖已存在的有效价格。
- XHKG 日历使用 `exchange-calendars==4.11.2`、`pandas==2.3.3`；不要自行升级至 Pandas 3，已实测会错误识别合法交易日。

### 23.3 转发与热度

```powershell
worker\.venv\Scripts\python.exe -X utf8 worker\jobs\repair_feed_metrics.py
```

默认仅审计；只有结构完整、路径明确、非负整数且不冲突的记录才允许 `--apply`。
报告在 `%LOCALAPPDATA%\futu-radar\metric-repair-report.json`，不含正文。

本轮实测 167 条转发缺失均为坏 JSON。60 条虽有 `share_count` 文本，但保守结构校验
没有找到可安全恢复的完整计数对象，因此**写回 0 条**。源 dump 第 14 列已核实为
`detail_updated_at`，不是额外的转发列。缺失尾部只能由完整历史导出或获授权源API补采。
不得用AI、当前累计转发、平均数或0替代当时的未知计数；FMP不提供社区互动数据。
`backend/core/heat.py` 公式及未知传播保持不变，因此仍可能有热度暂不可用。
**2026-09-15 更新**：ADR-0022 改为按已知项计算热度并披露转发数未知的帖子数（`heatUnknownPosts`），见 [ADR-0022](adr/0022-heat-lower-bound-disclosure.md)。

### 23.4 新来源规范化入口

```powershell
worker\.venv\Scripts\python.exe -X utf8 worker\jobs\ingest.py --source futu-export --file C:\data\futu-normalized.jsonl
```

每行一个 `FeedRecord`，schema 位于 `worker/jobs/ingest.py`。必需字段：`feed_id`、`code`、
`posted_at`、`observed_at`、`feed_type`、`like_count`、`comment_count`、`image_count`。
可提供正文、作者、转发/浏览、`comments` 数组与 `mentioned_codes`。未知数显式 `null`，
不由适配器补0；稳定 Futu ID 跨导出/API来源去重，逐记录事务更新，不删除整个事实库。
导入是更新/追加语义，不是删除未出现在文件里的评论或提及。

已有历史记录的计数修复要求同一个 `observed_at`，拒绝把当前回抓的累计数静默混入历史快照。
只有上游明确声明一个自然日已完整收集时，才传 `--complete-through 2026-08-25`；
部分导出必须省略此参数，不得根据最大一条帖子日期就宣称当天完整。

源版本改变后 worker 建立新的范围检查，旧输入不变的任务复用，变化的输入重新分析；
源或标注变化后旧汇总标记为待更新，完整汇总成功后才解除。`/version` 通知可见页面
重取数据，保留产品和日期筛选；隐藏页面暂停检查，重新聚焦恢复。

这提供规范化接入契约，并不代表任意格式无需适配，也没有凭空实现富途在线采集API。
直接重跑旧的 `jobs.etl` 仍是一次性全量重建流程，日常增量不得用它替代 `ingest`。

### 23.5 验证与排查

```powershell
backend\.venv\Scripts\python.exe -X utf8 -m pytest backend\tests -q
worker\.venv\Scripts\python.exe -X utf8 -m pytest worker\tests -q
npm --prefix frontend run mirror
npm --prefix "$env:USERPROFILE\.futu-radar\mirror\frontend" run build
npm --prefix "$env:USERPROFILE\.futu-radar\mirror\frontend" run real-data-check
node "$env:USERPROFILE\.futu-radar\mirror\frontend\scripts\live-data-check.mjs"
```

`real-data-check` 已禁止空白页面假通过；自动刷新测试只在隔离浏览器里模拟版本，不写真实库。
全量运行状态读取 `/api/v1/version` 的 `analysisProgress`，逐产品含 scope、队列和完成状态。
审计某 scope：`worker\.venv\Scripts\python.exe -X utf8 worker\jobs\audit.py --report --scope <id>`。
用量按包含该scope结果的运行归集；共享运行未按比例拆分，不能当成严格独占成本。
验证字段完整性、证据可定位与链路正确，不得把HTTP200或任务done称为准确率。

## 24. 蒸馏漏斗与 30 分钟运行（2026-09-15）

本节是 [ADR-0021](adr/0021-student-funnel.md) 的操作面：规则与近重复折叠（L0）→ 学生模型（L1）→ Luna 难例（L2，与 L1 并行）→ Layer B 逐区间并行（L3）。§23.1 的入口 `full_own.py --watch` 不变，内部顺序变了；§16 的 Gate 结论、§23 的边界与备份要求继续有效。**模型结论仍未经人工验证**：跑完 §24.4 的人工核对之前 `aiValidation=none`，之后是 `spot_check`（量尺，不是门槛，ADR-0019 不变）。

### 24.1 本机操作顺序

仓库根目录执行，前后顺序不能换（每步的验收数字在 §24.3）：

```powershell
git pull
copy %LOCALAPPDATA%\futu-radar\radar.db %LOCALAPPDATA%\futu-radar\backups\radar-before-0008-<日期>.db
worker\.venv\Scripts\python.exe -X utf8 -m alembic -c radar_db\alembic.ini upgrade head          # 到 0008
uv pip install --python worker\.venv\Scripts\python.exe -r worker\requirements-ml.txt            # torch CPU 约 200 MB
cd worker
..\worker\.venv\Scripts\python.exe -X utf8 -m models.dataset                                     # 训练集 → <数据目录>\datasets\student-v1
..\worker\.venv\Scripts\python.exe -X utf8 -m models.train                                       # CPU 1–2 小时；--model rbt3 约 3 倍快
..\worker\.venv\Scripts\python.exe -X utf8 -m models.export                                      # ONNX → int8，校验与 fp32 一致
..\worker\.venv\Scripts\python.exe -X utf8 -m scripts.probe_gateway --concurrency 16             # 再试 24
..\worker\.venv\Scripts\python.exe -X utf8 -m scripts.calibrate --batch 5 --skip-v1 --n 300
..\worker\.venv\Scripts\python.exe -X utf8 -m jobs.annotate --reprioritize
..\worker\.venv\Scripts\python.exe -X utf8 jobs\full_own.py --watch --max-items 300              # 加 --all 排 120 只全池（§25）
..\worker\.venv\Scripts\python.exe -X utf8 -m scripts.gold_sample                                # → gold-400.xlsx（填表）
..\worker\.venv\Scripts\python.exe -X utf8 -m scripts.evaluate_gold --file gold-400.xlsx         # → meta_kv.ai_validation
```

学生还没训出来、先想量 Luna：`gold_sample --llm-only` 抽的是有 Luna 现行结论的判定单元（自家／竞品 × Luna 极性 ×
简繁粤，没有置信带），产出 `gold-llm-400.xlsx` ＋ `gold-llm-400-model-labels.xlsx`；`evaluate_gold --file gold-llm-400.xlsx`
读它时 `by_system.student` 三项为 null、`combined` 等于 `llm`。标签表丢了或想按库里**现在**的结论算，加 `--from-db`：
按 `(产品代码, 评论正文)` 回库找判定单元，帖子标题与父评论消歧，匹配不到的行如实记 `n_unmatched`，不猜。

- 迁移 0008 加 `annotation_jobs.stage`（默认 `student`；已有的帖子与 KOL 评论任务置 `llm`）与 `worker_events`。合成库（37.8 万帖／19 万评论）上 0007→0008 耗时 0.6 秒；真库同量级，不需要停服务。
- `models.train` 之后看 `<STUDENT_MODEL_DIR>\calibration.json`：`heads.relevance.agreement` 与 `heads.attitude.agreement` 是对照集上学生与 Luna 的一致率。**两者 ≥0.90 才进下一步**；不到就先把 `STUDENT_ROUTE_THRESHOLD` 抬到 0.90（多送 Luna），或换 `--model roberta-wwm` 重训。
- `models.export` 之后看 `export.json`：每头 `use` 为 `int8` 且 `int8_vs_fp32_agreement ≥0.99`；不达标它会自动留 fp32 并在 `note` 里写明，推理慢约一倍，不影响正确性。
- `probe_gateway --concurrency N`：报 `n_429`、`retry_after_headers`、`p50/p95`。无 429 的最大 N 写进 `AI_CONCURRENCY`（上限 24）。**N <24 时 30 分钟目标退为 40 分钟，如实写在下方表格旁**。
- `calibrate --batch 5`：报告键 `b1_vs_b5`（随 `--batch` 走，不再叫 `b1_vs_b30`）。`attitude_agreement ≥0.90` 才把 `AI_MICRO_BATCH_SIZE` 从 1 改到 5；否则批留 1，L2 的预算按 5 倍请求数重估。
- `annotate --reprioritize`：按 `recency_tier`（距锚点 ≤7 天 +30、≤14 +20、≤30 或 mtd 窗内 +10）＋ own +2 ＋ current +1 重算全部 pending 任务；幂等，第二次跑应打印「0 条改动」。
- `full_own.py --watch`：学生通道是独立线程（按产品轮转 `classify.run`），调度器 tick 只跑 `pipeline.run(stage=llm)`；两个通道领不相交的任务集。不带 `--watch` 时每个 tick 顺序 classify → pipeline，跑一遍 61 只退出。`--no-student` 把全部评论任务放行给 Luna（没有学生模型时的退路；有模型但想临时关掉也用它）。
- 进度看 `GET /api/v1/progress`（队列按 stage × status、Layer B 脏标与产出、近 5 分钟吞吐、最近 200 条事件）与 `/api/v1/progress/events?after=<id>` 增量；前端侧栏由另一位工程师接。这两个端点只读，没有任何启动／停止作业的控制。

### 24.2 30 分钟预算（14 万条积压；每日增量约 3–5 千条则 <5 分钟）

| 阶段 | 目标吞吐 | 预算 | 依赖什么 |
|---|---|---|---|
| L0 规则五条＋近重复折叠＋排队 | ≥2,000 候选/秒 | 2–3 min | 纯 Python；simhash 按 `(产品, 帖子日)` 分桶 |
| L1 学生推理（`classify`） | ≥200 条/秒 | 10–12 min | ONNX int8、批 64、`intra_op_threads=核数`；不够换 `hfl/rbt3` |
| L2 Luna 难例 | ≥20 条/秒 | 20–25 min，**与 L1 并行** | 批 5 × 并发 24（探测通过）；路由份额 ≤20% |
| L3 Layer B | 8 线程 | 随区间就绪滚动，尾部 5–10 min | `low_sample`／指纹相同不发请求 |

网关并发上限 <24 时：L2 按比例拉长（并发 16 ≈ 30–35 min），端到端目标改为 40 分钟。一次性成本不计入：学生训练 CPU 1–2 小时、`probe_gateway`／`calibrate` 各几分钟、填 400 条核对表 2–3 小时。

### 24.3 每一步的验收数字

| 步骤 | 看哪里 | 通过线 |
|---|---|---|
| 迁移 | `alembic current` | `0008`；`SELECT stage, COUNT(*) FROM annotation_jobs GROUP BY 1` 里帖子与 KOL 评论任务全在 `llm` |
| 训练集 | `datasets\student-v1\meta.json` | `n_units` 与库里 Luna 现行判定单元数一致；`groups` ≥ 数百；`aspect_head` 为 true 时 `aspect_positive_train ≥5000` |
| 训练 | `calibration.json` | relevance／attitude 对照集一致率 ≥0.90；每头温度写了非 1 的值 |
| 导出 | `export.json` | 每头 `int8_vs_fp32_agreement ≥0.99`、`use=int8` |
| 推理吞吐 | `classify` 的 L1 事件间隔 | 12 层模型 ≥200 条/秒；否则换 rbt3 |
| 路由份额 | `classify` 输出的 `routed / input` | ≤20%；高了先看 `routes` 里哪条规则在贡献 |
| 网关 | `probe_gateway --concurrency` | 选定 N 下 `n_429=0` |
| 合批 | `calibrate --batch 5` 的 `b1_vs_b5` | `attitude_agreement ≥0.90` |
| 端到端 | `/api/v1/progress` 的 `queue.llm.pending` 归零到最后一条 L3 事件 | ≤30 min（并发 24）；每日增量 <5 min |
| 覆盖 | 板块总览／产品监控 | 61 只 d7 全部 ≥1 条态度、多数 ≥10；脏产品显示「待更新」而不是消失 |
| 人工核对 | `/api/v1/meta` 的 `aiValidation` | `level=spot_check`、`n=400`、三套系统各有 relevance／attitude 准确率与 macro-F1 |

### 24.4 人工核对集

流程与判定规则见 [docs/gold-labeling-guide.md](gold-labeling-guide.md)。要点：只打开 `gold-400.xlsx`，不看同目录的 `gold-400-model-labels.xlsx`；一条 20–30 秒，可两人各 200；`evaluate_gold.py` 写 `meta_kv.ai_validation` 并 bump annotation 版本号让后端缓存失效。两个 xlsx 都在数据目录（仓库外），含评论原文，不进 git；`gold-eval-*.json` 只有计数，进 `.scratch/llm-90d/`。

评估的分母：**每套系统只在它判过的行上算**，报告里 `n_relevance`／`coverage` 写明分母；`combined`（页面上那套）两边都没结论的行照记为错。整套系统一行都没判过 ⇒ 三个指标 null，不是 0。只核对 Luna 的那份表（`--llm-only`）跑出来 `student` 三项为 null，前端那句尾巴随之从「学生模型蒸馏自 Luna 标注」改成「结论由 Luna 判定」（`frontend/src/lib/view.js` 按 `by_system.student` 判）。

### 24.5 路由阈值起点：真库上要数的四个数

ADR-0021 §4 的阈值 0.85／0.15 是起点，本机 `alembic upgrade head` 后先数这四个数并回填到 ADR-0021 §4：

```sql
-- 1. Luna 现行标注 relevance 三值占比（分母＝Luna 判过的判定单元）
SELECT json_extract(a.value_json, '$') AS relevance, COUNT(*) FROM annotations a
  JOIN annotation_runs r ON r.run_id = a.run_id
 WHERE a.kind = 'relevance' AND r.provider = 'openai_compatible'
   AND NOT EXISTS (SELECT 1 FROM annotations s WHERE s.supersedes_id = a.annotation_id)
 GROUP BY 1;
-- 2. attitude 分布（同上换 kind='attitude'）
-- 3. 规则剔除占比：kind='text_quality' 且 provider='rule' 的判定单元数 / 候选总数（extract 报告里的「候选」）
-- 4. 同产品同日近重复率：extract 报告「近重复折叠」/「候选」
```

规则剔除与近重复两项直接看 `python -m jobs.extract --own --range d30 --with-baseline --dry-run` 的报告；不写库。

### 24.6 合成库实测（2026-09-15，4 核 Xeon、16 GB、Python 3.12、onnxruntime 1.30 / torch 2.14 CPU）

合成库 `gen.py`：37.8 万帖／19.4 万评论／36 万标注，`create_all` 到 0007 后 `alembic upgrade head` 到 0008。**文本只有 10 个不同的句子**，所以近重复折叠率（50%）与规则剔除率毫无代表性，只看耗时；学生用 `--tiny`（随机初始化的一层 BERT），数字只证明管线通、**不代表任何准确率**。Luna 用假 provider（每次调用 2 ms），L2 的数字量的是写库路径，不是网络。

| 步骤 | 输入 | 耗时 | 折算 | 备注 |
|---|---|---|---|---|
| alembic 0007→0008 | 37.8 万帖／19.4 万评论 | 0.6 s | — | 加列＋建表＋回填 stage |
| L0 `extract --own --range d30 --with-baseline` | 74,918 候选 | 47.2 s | **约 1,590 候选/秒，0.63 s/千条** | 含五条规则、simhash 折叠 37,578 成员、写 22,589 规则单元（×2 行）＋37,578 簇行（×2 行）＋排队 14,751 任务。目标 ≥2,000/秒未到，差在写库；机器是 4 核 |
| `models.dataset` | 67,565 现行判定单元 | 3.3 s | — | 853 个 `(产品, ISO 周)` 组，对照 6,635 |
| `models.train --tiny --max-steps 40` | 3,000 条 | 8.1 s | — | 只测管线 |
| `models.export` | 3 头 | 4.6 s | — | optimum 导出；int8 与 fp32 一致 1.0（随机权重下平凡） |
| L1 推理 `infer.Student`（tiny，2 头，ONNX int8，批 64） | 6,400 条 | 4.3 s | **约 1,500 条/秒，0.67 s/千条** | 随机权重一层模型；12 层 mengzi 每条算力约为它的数十倍，真吞吐要在本机用 §24.3 的方法量 |
| L1 `classify --scope`（端到端） | 14,751 任务 | 79.3 s | **5.4 s/千条** | 含加载、推理、每条写 2 行学生标注、全部路由到 Luna（随机权重必然低置信）、8 条 L1 事件 |
| `annotate --reprioritize` ×2 | 14,751 pending | 0.4 s | — | 两次都是「0 条改动」（extract 已按同一函数排） |
| L2 `pipeline.run(stage=llm)`（假 Luna，批 5 × 并发 4） | 14,751 任务／2,951 请求 | 121 s | **约 122 条/秒** | 写库路径上限：`_write_batch` 整批一事务、Luna 行 supersede 学生行 23,634 条、成员传播 |
| L3 `synthesize`（8 线程，假 Luna） | 366 对 `(code, range)`／2,034 次调用 | 48 s | — | 写 4,889 条生成物；226 次 `low_sample` 不发请求；366 对全部清脏 |
| `GET /api/v1/progress` | 3,324 条事件在表 | 9 ms | — | `/progress/events?after=` 2 ms |

跑完后的库状态与契约一致：`annotation_jobs` 14,751 条全部 `stage=llm, status=done`；`annotation_runs.provider` 出现 `rule`／`local_model`／`propagated`／`openai_compatible` 四种；学生行 `calibrated_confidence` 全部非空、Luna 行与规则行全部 NULL；每个近重复成员的 relevance 链末只有一行（第二次传播 supersede 第一次）；`synthesis_outputs` 覆盖 61 只 × 6 档；`meta_kv` 里没有残留的 `synth_dirty_*=1`。

## 25. 从「400 条填完」到「五页全部有数」的本机收尾顺序（2026-09-16）

前提：`gold-llm-400.xlsx`（`gold_sample --llm-only` 抽的、只核对 Luna 的那份）已经填好，放回数据目录
`%LOCALAPPDATA%\futu-radar\`；同目录最好还有抽样时一起写出的 `gold-llm-400-model-labels.xlsx`，没有也行（回库匹配）。
下面每一步都要**本机**的瘦库、`worker\.env` 里的 Luna Key 与 `FMP_API_KEY`，云端环境没有这三样，所以只能在本机跑；每步幂等，
中断了从那一步重跑。

```powershell
$env:PYTHONIOENCODING = "utf-8"
git pull
Copy-Item "$env:LOCALAPPDATA\futu-radar\radar.db" "$env:LOCALAPPDATA\futu-radar\backups\radar-before-25-<日期>.db"
worker\.venv\Scripts\python.exe -X utf8 -m alembic -c radar_db\alembic.ini upgrade head     # 到 0008；已在 0008 则无事
cd worker
$py = "..\worker\.venv\Scripts\python.exe"

# ① 抽检 → /meta.aiValidation = spot_check（不花钱，几秒）
& $py -X utf8 -m scripts.evaluate_gold --file gold-llm-400.xlsx                             # 标签表不在就自动回库匹配
                                                                                             # 想按库里现在的结论算：加 --from-db

# ② 学生模型（一次性，CPU 1–2 小时；跳过则全部评论都送 Luna，能跑但慢且贵）
uv pip install --python .venv\Scripts\python.exe -r requirements-ml.txt
& $py -X utf8 -m models.dataset; & $py -X utf8 -m models.train; & $py -X utf8 -m models.export

# ③ 网关与合批（各几分钟；结论写 .env：AI_CONCURRENCY、AI_MICRO_BATCH_SIZE）
& $py -X utf8 -m scripts.probe_gateway --concurrency 16                                     # 无 429 再试 24
& $py -X utf8 -m scripts.calibrate --batch 5 --skip-v1 --n 300                              # attitude ≥0.90 才把批改成 5

# ④ 全池运行：自家 61 ＋ 同业 59，学生 ∥ Luna，Layer B 逐区间就绪，FMP 每小时同步 120 只
& $py -X utf8 -m jobs.annotate --reprioritize
& $py -X utf8 jobs\full_own.py --all --watch --max-items 300                                # 常驻；进度看 /api/v1/progress 或页面右上「处理进度」

# ⑤ 行情一次性回填（--watch 下每小时也会做；想立刻有 K 线就先跑一遍）
& $py -X utf8 jobs\sync_prices.py --all --from 2026-06-27 --to 2026-08-25

# ⑥ 看页面
cd ..\backend; .\.venv\Scripts\python.exe app.py                                             # 8008，DATA_PROVIDER 默认 sql
cd ..\frontend; npm run dev; npm run real-data-check                                         # 五页零 pageerror
```

每一步看什么：

| 步 | 看哪里 | 通过线 |
|---|---|---|
| ① | 命令输出的 JSON；`/api/v1/meta` 的 `aiValidation` | `level=spot_check`、`n≈399`（填了相关性的行数）、`by_system.llm` 有三个数、`by_system.student` 三项 null（只核对了 Luna）；板块总览 S6／产品监控 P7 出现「人工核对 N 条…结论由 Luna 判定」 |
| ② | `calibration.json`／`export.json` | §24.3 同一行 |
| ③ | probe／calibrate 报告 | §24.3 同一行 |
| ④ | `/api/v1/progress`；`/api/v1/version` 的 `analysisProgress.text` | 文案是「全池分析 x/120」；`queue.llm.pending` 归零、`synthesis.dirtyProducts=0` 即一轮结束；14 万条积压按 §24.2 预算 30–40 分钟，之后每日增量 <5 分钟 |
| ⑤ | `price_syncs` 表、产品监控 K 线块 | 有映射且 FMP 有数的产品出 OHLC；`unavailable` 的原因在 `price_syncs.reason`，不补零 |
| ⑥ | 五页 | 缺失态只剩下面这张表里的几种 |

跑完之后**仍然**会是缺失态、且不能靠再跑一遍消掉的地方（都是数据源的边界，不是管线没跑完）：

| 页面位置 | 显示 | 为什么 |
|---|---|---|
| 任何区间里评论 <10 条的产品的态度结论 | 样本不足 | PRD §3.5 阈值；小产品在 d1／d2 里常见 |
| 区间内没有帖子／评论的产品 | 暂无内容 | 真零，不是没标 |
| 同业产品的重点舆情（合规） | — | PRD §4.2 P10：识别范围只有自家 |
| FMP 没有映射或返回空的产品的 K 线／日线价 | 暂不可用 | `price_syncs.reason`；§23.2 实测首轮约 19 只日线可得 |
| 转发数未知的帖子所在区间的热度 | 数值＋「n 帖转发数未知 · 下限」 | ADR-0022；坏 JSON 行占 0.03% |
| 官号 5/20、KOL 14/32 | 空列表 | 真库里按名字没匹配到（§「三条实测」第 1 条），是「没发过帖」，不是缺数 |
| 「数据截至」 | 2026-08-25 | dump 到此为止；在线增量采集（Gate 6）仍无合法接口 |
| AI 结论的验证程度 | 「人工核对 N 条…」 | 是量尺不是门槛（ADR-0019／0021）；**不会**变成「已核验」 |
