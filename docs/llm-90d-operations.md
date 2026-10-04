# 90 天 LLM 试点 · 本机操作单（Windows PowerShell）

> 配套 [ADR-0020](adr/0020-llm-only-90d-pilot.md)。本单只列**要在你本机跑**的命令：它们要 dump、瘦库或 API Key，云端环境没有。
> 每一步都是幂等的，跑错了直接重跑。凡是花钱的步骤前面都有一个不花钱的 `--dry-run`。

所有命令在仓库根执行；`$py = "worker\.venv\Scripts\python"`。Windows 控制台先 `$env:PYTHONIOENCODING = "utf-8"`。

## 0. 前置

```powershell
cd worker; .\.venv\Scripts\pip install -r requirements.txt; cd ..
# worker\.env：AI_PRIMARY_BASE_URL / AI_PRIMARY_API_KEY / AI_PRIMARY_MODEL 三项必填；
# AI_PROMPT_VERSION=comment-product-v3、AI_TAXONOMY_VERSION=v2、AI_SCHEMA_VERSION=v2（模板已改）。
# AI_FILL_MISSING_ONLY=false：v3 问题区间必须 supersede 重标，不能只补缺失项。
# AI_MICRO_BATCH_SIZE 先按 provider 能力设置：普通 LLM 校准通过后可用 5；System One 固定为 1。
# AI_CONCURRENCY 先 4；探明限流后再调。
```

## 1. 切 90 天数据到本地瘦库（不花钱；约 10 分钟）

```powershell
# dump 先拷到本机 SSD（P: 盘 9.4 MB/s，别在网络盘上读 10 GB）
Copy-Item "P:\NIKI\dump-market_insight-202608261101-2\*.sql" "C:\data\dump\"

& $py -m jobs.import_dump --dump "C:\data\dump\<文件名>.sql" --days 90
& $py -m jobs.etl
& $py -m alembic -c radar_db\alembic.ini upgrade head        # 到 0003
& $py -m alembic -c radar_db\alembic.ini current
```

验收：导入日志最后一行「扫 X 帖，留 Y 帖；落库 Y 行」三数一致；`current` 显示 `0003 (head)`。

## 2. 探测网关（几次请求，几乎不花钱）

```powershell
& $py -m scripts.probe_gateway
& $py -m scripts.probe_gateway --burst 20      # 可选：探限流，花 20 次
```

结论抄进 `docs/ai-data-integration-runbook.md` §6.4。`flex` 显示「透传」就在 `.env` 加 `AI_SERVICE_TIER=flex`（Batch 价，同步接口，429 会自动退避重试）。

## 3. 抽取候选并 exact 预过滤（不花钱）

```powershell
# 先只看数字
& $py -m jobs.extract --own --from 2026-05-28 --to 2026-08-25 --task both --dry-run
# 真排队：自家 61 只、90 天、评论＋帖子（KOL 与官号作者）＋ KOL 评论
& $py -m jobs.extract --own --from 2026-05-28 --to 2026-08-25 --task both
# 记下输出里的 scope_id（scope-2026…），报告在 .scratch\llm-90d\<scope_id>.md
```

看报告里「规则剔除」各条的数量与「用量估算」。同业 59 只想一起跑就 `--all`；只想先亮 d7 就 `--range d7 --with-baseline`。

父帖资格统一由 `radar_db/comment_filters.json` 决定：默认 `exact`，要求父帖 `title + content` 有自身来源 ticker 的严格 cashtag；3037 使用 `exclude ["800000.HK"]`，自身提及优先，否则命中排除 ticker 才剔除。父帖顺带提到其他 ETF 不得跨讨论区复制回复，底层指数或资产词（如「恒指」「HSI」「BTC」「ETH」）不能单独证明产品相关。回复正文不能救回不合格父帖，也不能改路由；父帖合格后，全部回复只继承原讨论区产品。规则剔除必须能追溯到父帖 mention 快照、配置版本与摘要，不能只看排队数推断。

数据源侧同时核对平台报告量、已入库正文量与截断状态。feed 标为 `partial` 时，先由第三方服务／Airflow 补齐 MarketInsight，再按 `feed_id` 从数据库定点重读；Radar 不直连社区接口，也不要把源库缺页当成模型漏判。

## 4. 放量前的 b=1／b=5 实验与金标材料（400 条样本，约 480 次请求）

```powershell
& $py -m scripts.calibrate --scope <scope_id> --n 400 --batch 5 --skip-v1 --max-http-requests 500
```

- 只比较 `batch=1` 与 `batch=5`；`batch=30` 已禁用，不得重新启用。
- `.scratch\llm-90d\calibration-*.json`：只有 `batchGatePassed=true` 才能把该 provider 的生产批大小设为 5。门槛为完整对照至少 300 条、两边零失败、相关性与态度一致率都 ≥90%，且实测吞吐至少为 b=1 的 3 倍。
- `%LOCALAPPDATA%\futu-radar\calibration-<时间>-gold\`：同一次受预算校准调用导出的 `gold-llm-400.xlsx` 与模型标签表。它不写线上 annotations，专门解决“质量门禁前先取得 v3 预测”的启动问题；固定的 3037 投诉案例已占用样本槽位，人工只打开前一份。
- TypeSafe System One 声明的 `max_batch_size=1`，即使请求 `--batch 5`，报告里的 `effective_batch_size` 也会是 1，门禁不会通过。它可做单条判断，但**不用于批量回填**。
- `%LOCALAPPDATA%\futu-radar\calibration-*-offpool-sample.csv`：100 条被「仅个股」规则剔掉的评论，人工在最后一列填 Y/N；它是规则抽检，不替代第 6 步的 400 条金标质量门槛。

## 5. 分阶段回填（主要花费）

```powershell
& $py -m jobs.analyze plan --ranges d1 --ownership all
```

这里先只做只读计划。实际回填命令放在第 6 步质量门禁之后；在金标报告通过前不得启动生产回填。

不要一次把多个区间全压入生产。当前固定优先级为：

1. 全产品池昨日（`d1`）；
2. 全产品池近两天（`d2`）；
3. 全产品池近七天（`d7`）；
4. 前三档完成后，才按需要继续 `d14`、`d30`。

每一阶段都先运行 `jobs.analyze plan`；通过两份门禁报告后，使用 `jobs.refresh analyze --max-http-attempts <当日额度>` 分日推进。该入口在数据库中持久化香港自然日预算，重跑不会重置额度。阶段内仍按评论（own 优先、当前期优先）→ KOL 评论 → 帖子 → Layer B 生成物 → 报表。任一步 401/400 会中止并把任务放回队列，修好 `.env` 后重跑即可。

每阶段验收都看产品页漏斗：`筛选前平台量 → 筛后平台量 → 已抓正文 → 规则入围 → AI 完成 → 相关评论`，并核对合格父帖数与父帖筛选剔除量。`sourceCoverage` 或 `analysisCoverage` 未满、仍有 pending／needs-context 时，相关评论只能读作「≥ 已确认数」，情绪与主题是「部分数据」；不得把阶段性结果写成全量结论。readiness、来源 ticker 或配置摘要不一致时筛选指标应为「暂不可用」。

## 6. 看质量与成本（不花钱）

```powershell
& $py -m jobs.audit --report --scope <scope_id>
& $py -m jobs.audit --lexicon-recall                 # 合规词表命中但模型未标的样本
& $py -m jobs.audit --sample 100 --xlsx              # 随机 100 条给自己翻（只看不批）
& $py -m jobs.review --id <annotation_id> --reject --reviewer <名字> --reason <理由>   # 要下线某条才用
```

没有金标时，审计报表仍然只有完成率、dead-letter、`needs_review_rate`、`evidence_located_rate`、各产品态度分布、规则剔除分布和 token 累计，不能把这些当准确率。

放量前必须完成至少 400 条人工金标，并运行：

```powershell
& $py -m scripts.evaluate_gold --file "$env:LOCALAPPDATA\futu-radar\calibration-<时间>-gold\gold-llm-400.xlsx" --no-write
```

报告 `qualityGate.passed` 只有在 400 条均为同一请求模型与供应商返回模型、`comment-product-v3`、schema、taxonomy 的直接 LLM 结论，相关类 precision ≥95%、recall ≥90%，仓库内固定 3037 投诉 case ID 全部通过，且两篇官号反馈用当前归属代码复跑通过时才会为 true。自由备注不参与门禁。门槛未过时（包括 `--no-write`）命令返回非零且不写 `meta_kv.ai_validation`；`--apply` 同样会从混淆矩阵重算指标并核对当前策略及固定 manifest，不能用手改 `passed=true`、漏掉案例或使用旧 v2 报告绕过。具体填表规则见 [gold-labeling-guide.md](gold-labeling-guide.md)。

通过后把生成的 `gold-eval-*.json` 作为 `--quality-report`（自动任务可放入 `AI_QUALITY_REPORT` Secret），并与第 4 步的 `--calibration-report` 一起交给生产 `jobs.refresh analyze`。缺任一份，或任一份策略不匹配，都会在第一次模型请求前停止。

```powershell
& $py -m jobs.refresh analyze --mode daily --ranges d1 `
  --batch-size 5 --concurrency 2 --max-http-attempts 300 `
  --calibration-report .scratch\llm-90d\calibration-<时间>.json `
  --quality-report .scratch\llm-90d\gold-eval-<时间>.json
```

随后按第 5 步顺序切换 `--codes`／`--ranges`。中断或日预算用尽后重复运行同一条 `jobs.refresh analyze` 命令；任务幂等续做，数据库中的当日预算不会被重置。

低层入口也不能绕过门禁：直接运行 `jobs.pipeline`、`jobs.full_own` 或 `jobs.annotate --run --task comment_product` 时，同样必须传 `--calibration-report` 与 `--quality-report`。`plan`、`--dry-run` 以及非评论任务不调用模型相关性判定，因此不要求这两份报告。

## 7. 看页面

```powershell
cd backend; .\.venv\Scripts\python app.py        # DATA_PROVIDER 默认 sql，8008
cd frontend; npm run dev                          # 5173
cd frontend; npm run real-data-check              # 五页零 pageerror、正文无 null/NaN
```

S6／P7 面板应有「AI 结论由模型自动生成，未经人工验证；每条可回到原文」；未通过金标门禁前 `/api/v1/meta` 的 `aiValidation` 为 `none`。
填满 400 条并通过 `scripts.evaluate_gold` 的质量门槛后才变为 `spot_check`。产品页同时检查「筛前平台量」「筛后评论量」与「相关评论数」没有混用；覆盖不完整时相关评论带 `≥`，情绪与主题明确标记「部分数据」。

## 8. 从只有自家到全池（2026-09-16）

`jobs\full_own.py --all --watch` 把 59 只同业按同一套 scope 排进去（自家先），产品监控页选到同业产品时 AI 块才会有数；
`jobs\sync_prices.py --all` 同步 120 只行情。完整顺序（含抽检、学生模型、网关探测）见 runbook §25。

## 常见问题

- **`extract` 说 meta_kv 里没有 anchor**：步骤 1 没跑完。
- **`pipeline` 立刻中止、日志 401**：Key 或 base_url 错；任务已放回队列，改完重跑。
- **`database is locked`**：有另一个进程（后端）也在写？后端只读不该锁。确认只有一个 worker 进程在跑；并发只支持单进程多线程。
- **System One 校准时 `effective_batch_size=1`**：是 provider 硬限制，不是配置未生效；不要用于批量回填，也不要改成 5 或 30 绕过限制。
- **相关评论数明显少于筛后评论量**：先分别看 `sourceCoverage` 与 `analysisCoverage`。前者不足时先让第三方服务／Airflow 补齐 MarketInsight，再从数据库同步；后者不足才继续预算内回填。若筛前与筛后差异异常，先检查父帖回填审计、readiness、配置摘要及 source ticker；不要用回复文本补数。
- **想重新生成 Layer B**：`python -m jobs.synthesize --scope <id> --force`（指纹相同也重生成，会花钱）。
- **想只跑某几只**：`python -m jobs.synthesize --codes 3033,7226 --ranges d7,d30`。
