# 90 天 LLM 试点 · 本机操作单（Windows PowerShell）

> 配套 [ADR-0020](adr/0020-llm-only-90d-pilot.md)。本单只列**要在你本机跑**的命令：它们要 dump、瘦库或 API Key，云端环境没有。
> 每一步都是幂等的，跑错了直接重跑。凡是花钱的步骤前面都有一个不花钱的 `--dry-run`。

所有命令在仓库根执行；`$py = "worker\.venv\Scripts\python"`。Windows 控制台先 `$env:PYTHONIOENCODING = "utf-8"`。

## 0. 前置

```powershell
cd worker; .\.venv\Scripts\pip install -r requirements.txt; cd ..
# worker\.env：AI_PRIMARY_BASE_URL / AI_PRIMARY_API_KEY / AI_PRIMARY_MODEL 三项必填；
# AI_PROMPT_VERSION=comment-product-v2、AI_TAXONOMY_VERSION=v2、AI_SCHEMA_VERSION=v2（模板已改）。
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

## 3. 抽取候选并预过滤（不花钱）

```powershell
# 先只看数字
& $py -m jobs.extract --own --from 2026-05-28 --to 2026-08-25 --task both --dry-run
# 真排队：自家 61 只、90 天、评论＋帖子（KOL 与官号作者）＋ KOL 评论
& $py -m jobs.extract --own --from 2026-05-28 --to 2026-08-25 --task both
# 记下输出里的 scope_id（scope-2026…），报告在 .scratch\llm-90d\<scope_id>.md
```

看报告里「规则剔除」各条的数量与「用量估算」。同业 59 只想一起跑就 `--all`；只想先亮 d7 就 `--range d7 --with-baseline`。

## 4. 放量前的一致性实验（约 320 次请求）

```powershell
& $py -m scripts.calibrate --scope <scope_id> --n 300
```

- `.scratch\llm-90d\calibration-*.json`：`b1_vs_b30.attitude_agreement` 与 `v1_vs_v2.attitude_agreement` 任一 < 0.90 ⇒ 先改 `worker/ai/prompts/comment_product_v2.py`（改了要改 `VERSION`）再放量。
- `%LOCALAPPDATA%\futu-radar\calibration-*-offpool-sample.csv`：100 条被「仅个股」规则剔掉的评论，人工在最后一列填 Y/N。误杀 > 5% ⇒ 步骤 3 重跑时加 `--no-prefilter-offpool`。

## 5. 一次过跑完（主要花费）

```powershell
& $py -m jobs.pipeline --scope <scope_id> --dry-run                 # 每步会做什么、估算多少 token
& $py -m jobs.pipeline --scope <scope_id> --budget-requests 300      # 先跑 300 批（约 9,000 条）看看
& $py -m jobs.pipeline --scope <scope_id>                            # 跑完剩下的；中断后直接重跑即续
```

顺序：评论（own 优先、当前期优先）→ KOL 评论 → 帖子 → Layer B 生成物 → 报表。任一步 401/400 会中止并把任务放回队列，修好 `.env` 重跑即可。

## 6. 看质量与成本（不花钱）

```powershell
& $py -m jobs.audit --report --scope <scope_id>
& $py -m jobs.audit --lexicon-recall                 # 合规词表命中但模型未标的样本
& $py -m jobs.audit --sample 100 --xlsx              # 随机 100 条给自己翻（只看不批）
& $py -m jobs.review --id <annotation_id> --reject --reviewer <名字> --reason <理由>   # 要下线某条才用
```

报表里**没有**准确率 —— 没有金标就没有分母（ADR-0019）。看的是：完成率、dead-letter、`needs_review_rate`、`evidence_located_rate`、各产品态度分布、规则剔除分布、token 累计。

## 7. 看页面

```powershell
cd backend; .\.venv\Scripts\python app.py        # DATA_PROVIDER 默认 sql，8008
cd frontend; npm run dev                          # 5173
cd frontend; npm run real-data-check              # 五页零 pageerror、正文无 null/NaN
```

S6／P7 面板应有「AI 结论由模型自动生成，未经人工验证；每条可回到原文」；`/api/v1/meta` 的 `aiValidation` 为 `none`。

## 常见问题

- **`extract` 说 meta_kv 里没有 anchor**：步骤 1 没跑完。
- **`pipeline` 立刻中止、日志 401**：Key 或 base_url 错；任务已放回队列，改完重跑。
- **`database is locked`**：有另一个进程（后端）也在写？后端只读不该锁。确认只有一个 worker 进程在跑；并发只支持单进程多线程。
- **想重新生成 Layer B**：`python -m jobs.synthesize --scope <id> --force`（指纹相同也重生成，会花钱）。
- **想只跑某几只**：`python -m jobs.synthesize --codes 3033,7226 --ranges d7,d30`。
