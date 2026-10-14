# PRD 部署手册

本目录按公司 EKS 部署规范落地，只包含 PRD。复用 `ih` 中已有的共享 ALB，
远程资源入口为现有域名下的 `/remote/opinion-radar`，host 页面路由为 `/opinion-radar`。
页面资源和 API 都先经过 nginx，
API 转发时去掉 `/remote/opinion-radar`，保留 Flask 使用的 `/api` 前缀。
配置不进入镜像，数据库使用外部 RDS，不在集群内部署 MySQL。

## 1. 对象与命名

| 项目 | 本项目配置 |
| --- | --- |
| AWS 账号 / 区域 | `449732370091` / `ap-east-1` |
| 资源前缀 / namespace | `ih-prd-remote-opinion-radar-` / 已存在的 `ih` |
| ECR | `449732370091.dkr.ecr.ap-east-1.amazonaws.com/ih-prd-remote-opinion-radar-{srvapp,webapp}:latest` |
| API | Deployment / Service `ih-prd-remote-opinion-radar-srvapp`，Service `8026` → 容器 `8008` |
| 前端 | Deployment / Service `ih-prd-remote-opinion-radar-webapp`，Service `3026` → 容器 `8080` |
| worker | Deployment `ih-prd-remote-opinion-radar-worker`，复用 srvapp 镜像，单副本 `Recreate`，无 Service |
| Ingress | 保留共享资源 `hkg-csop-prd-lb16-ih`，`internet-facing` ALB，HTTPS 443，HTTP 重定向 |

不创建 namespace，不再提供 Namespace 清单。除已有共享 Ingress 外，本项目资源都带
`ih-prd-remote-` 前缀。Ingress 保留原名、证书、子网和其他应用的路由；不得改名
另建一个共享 ALB，也不得用仅含本应用路由的清单覆盖它。

srvapp 镜像包含 API 与 worker 代码，但依赖分别锁定在 `/opt/srvapp`、`/opt/worker`。
API 默认启动 gunicorn；worker 和迁移容器显式指定 worker 解释器与运行守卫。
仍是三个 Deployment、两个镜像，API 不注入 worker 的模型和源库密钥。

没有加入 UAT、CI、Entra 配置、PVC、集群 MySQL、Airflow、HPA 或 PDB。
当前三个服务均为一个副本；前端扩为两个副本时再配反亲和与 PDB。
API 无进程内调度器，使用 RollingUpdate；worker 必须保持一个实例，
不要同时启用原有 Airflow DAG 或在工作站启动另一份生产 scheduler。
旧 `deploy/kubernetes/` 中的 Secret 示例不属于本套清单，不要对仓库递归 apply。

## 2. 配置来源

- `prd/configmap.yaml`：非机密运行参数，按当前 `worker/.env`、后端配置模板及代码默认值填写。
- 根目录 `.env.prd.example`：同一套非机密值加五个空白机密字段。实际 `.env.prd` 已被 gitignore 排除。
- `create-secret.sh prd`：从实际环境文件读取固定 key，校验全部成功后更新两个 Secret。
- 本机 `prd/secret.yaml` 含真实凭据，已被 gitignore 排除；不得提交或写进镜像。
   推荐用脚本生成 Secret，不要直接对整个 `k8s/prd/` 目录执行 apply。

| 机密 key | 来源与用途 | 注入 |
| --- | --- | --- |
| `RADAR_DB_URL` | 当前 worker 环境中的应用 RDS，库名 `opinion_radars` | API + worker，同一份值 |
| `MARKET_INSIGHT_DATABASE_URL` | 当前 worker 环境中的独立源库 `market_insight`，SELECT-only 账号 | 仅 worker |
| `AI_PRIMARY_API_KEY` | TypeSafe，逐条分类 | 仅 worker |
| `AI_SYNTH_API_KEY` | 独立生成模型，页面总结 | 仅 worker |
| `FMP_API_KEY` | 行情同步 | 仅 worker |

连接串整体作为机密处理，不拆主机名、用户名或密码进 ConfigMap。可直接沿用当前
MySQL 连接串；当前源库使用 `mysql+mysqldb`，而项目只安装 PyMySQL。容器启动守卫
通过 SQLAlchemy 将驱动规范化为 `mysql+pymysql`，未指定字符集时补 `utf8mb4`，
不改主机、端口、库名或凭据。显式配置其他字符集会被拒绝。密码中的保留字符必须 URL 编码。
不要复制 backend 未设置连接串时的 SQLite 默认值，也不要把源库当作应用库。

实际环境文件使用逐行的原始 `KEY=value`：不加 shell 外层引号，不用 `export`、
变量插值或行尾注释。脚本不 `source`；`$`、空格、`=`、`#` 均作为值保留。
空值、重复 key、占位值会导致失败；错误日志不打印值。
脚本使用权限受限的临时文件和 server-side apply，不把机密放在命令行参数或
`last-applied-configuration` 注解里。更新后必须手动重启 Pod。

当前 AI 配置为 TypeSafe `jev-latest` + 独立生成模型 `gpt-5.6-luna`，并发 `8`、
micro batch `1`。沿用代码默认：香港时间 08:00 全量刷新，13:00、15:00 日内刷新，
`AI_OWN_TOP_LIMIT=0` 表示全部活跃产品，每日 HTTP 上限 `100000`。
`AI_DATA_GOVERNANCE_APPROVED=false` 才关闭 AI。上述值不是建议预算，请上线前确认。
未加入一次性迁移、dump、已移除本地模型或前端机密变量。

## 3. 首次上线前

1. 平台确认 PRD 集群、已有 `ih` namespace 和两个指定 ECR 仓库。清单与依赖锁面向 Linux amd64。
2. 准备 Docker Compose 2.30+、kubectl、AWS CLI、POSIX shell；生产构建在 `AI-Workstation-01`。
3. 从现有安全配置填写本机 `.env.prd` 的五个机密字段，并确认对应生产权限。不要把文件提交。
4. 从集群侧验证两台 RDS 的网络、MySQL 8、`utf8mb4`、时区和账号权限。
   应用账号需要运行及 Alembic 迁移的 DDL 权限，源账号只允许 SELECT。
5. 确认应用库已有业务数据，或源库已有最近一次成功且完整的 `comments_all` 回执。
   迁移只创建表结构，不会把本地 SQLite 数据搬到 RDS；空库发布时，worker 的
   `bootstrap-data` initContainer 会通过该回执执行首次同步并生成真实 `meta_kv.anchor`。
   回执缺失、源库不可读或同步失败时 rollout 会保持未完成，不再把空库服务标为 Ready。
   已有库必须核对 `alembic_version`，不允许未经核验直接 stamp；需要保留本地历史时仍须先按
   [数据库迁移说明](../radar_db/migrations/README.md)迁移，bootstrap 不替代历史搬迁。
6. 确认出网可以访问配置的两个 AI 端点、FMP、Yahoo Finance 行情端点及系统 DNS。
7. 比对集群中共享 Ingress 的最新版本，保留其他应用的新增规则后再 apply。
   当前文件沿用用户提供的证书 ARN、子网与无 host 限制规则；确认现有域名、证书和入口访问控制
   适用于 `/remote/opinion-radar`，不要另建 DNS 或修改共享 ALB 属性。

当前业务页没有 Entra 登录。代码未消费 `ADMIN_API_TOKEN`，所以没有把这个无效变量
放进 Secret，也没有伪造鉴权配置。生产 nginx 对子路径下的 `/admin` 与 `/api/v1/admin*` 返回 404。
管理 API 仅供有 Kubernetes 权限的运维经受控端口转发访问；后端应用层仍无鉴权，
同集群网络访问也需由平台权限/网络边界控制。正式开放管理页面之前必须先实现鉴权。
共享 ALB 已是公网入口；nginx 的管理接口拦截不等于业务页面鉴权。上线前必须确认现有入口的
鉴权或访问限制覆盖本子路径。曾经出现在日志或聊天中的凭据应轮换，不应复用。

## 4. 构建与本地镜像验收

在工作站从仓库根目录执行，不需要把真实配置放入构建上下文：

```bash
export AWS_PROFILE=ih_ecr_prd
REGISTRY=449732370091.dkr.ecr.ap-east-1.amazonaws.com
aws sts get-caller-identity
aws ecr get-login-password --region ap-east-1 | docker login --username AWS --password-stdin "$REGISTRY"
docker compose -f docker-compose.prd.yml build srvapp webapp
docker compose -f docker-compose.prd.yml push srvapp webapp
```

只从已提交、可追溯的版本构建，记录 Git commit 与两张镜像 digest。标签统一为
`latest`，集群拉取策略为 `Always`；回滚用已记录的 digest 找回镜像。
基础运行时、直接及传递 Python 依赖、npm lock 均已固定；
Dockerfile 校验 lock 与 requirements 一致。Python 保留项目的 3.11，不擅自升级到模板的 3.12。
前端强制构建参数 `VITE_BASE_PATH=/remote/opinion-radar/`、
`VITE_API_BASE=/remote/opinion-radar/api/v1`；`remoteEntry.js`、静态资源、独立访问路由和
nginx 使用该前缀。host 内的页面路由为 `/opinion-radar/*`。
本地日常开发未设置这些参数时仍使用根路径和原有 `5173` / `8008`。

`.dockerignore` 在仓库根，因为两个镜像都使用根构建上下文；前端仍需读取只读设计资源。
仅部署脚本采用 LF，保留 `design/` 的逐字节约束。

在有 RDS 网络通路、已准备应用库的隔离验收环境，可以运行以下命令验证镜像。
此命令会启动真实采集/AI 调度，不要在生产 scheduler 运行期间另起一份：

```bash
docker compose -f docker-compose.prd.yml run --rm worker /opt/worker/bin/python -m alembic -c /app/radar_db/alembic.ini upgrade head
docker compose -f docker-compose.prd.yml up -d
curl -f http://127.0.0.1:18008/health
curl -f http://127.0.0.1:18008/health/deep
curl -f http://127.0.0.1:18008/health/ready
curl -f http://127.0.0.1:18080/healthz
curl -f http://127.0.0.1:18080/remote/opinion-radar/remoteEntry.js
curl -f http://127.0.0.1:18080/remote/opinion-radar/sector
curl -f http://127.0.0.1:18080/remote/opinion-radar/api/v1/meta
docker compose -f docker-compose.prd.yml down
```

Compose 的 `format: raw` 与 Secret 脚本使用同一份字面值。构建不要求 `.env.prd` 存在；
启动时缺少配置会由守卫拒绝。普通本地开发仍使用现有命令与端口，不运行这套 PRD 实例。

## 5. 首次发布顺序

以下命令不包含 Ingress，不创建已有 namespace。不要直接 apply 整个目录。

```bash
kubectl config current-context
kubectl get namespace ih
kubectl apply -f k8s/prd/configmap.yaml
sh k8s/create-secret.sh prd
kubectl apply -f k8s/prd/worker-deployment.yaml
kubectl -n ih rollout status deploy/ih-prd-remote-opinion-radar-worker --timeout=20m
kubectl apply -f k8s/prd/srvapp-deployment.yaml -f k8s/prd/srvapp-service.yaml
kubectl -n ih rollout status deploy/ih-prd-remote-opinion-radar-srvapp --timeout=5m
kubectl apply -f k8s/prd/webapp-deployment.yaml -f k8s/prd/webapp-service.yaml
kubectl -n ih rollout status deploy/ih-prd-remote-opinion-radar-webapp --timeout=3m
```

worker 的 initContainer 依次运行 `alembic upgrade head` 与 `jobs.bootstrap`。后者仅在
`meta_kv.anchor` 缺失时调用正常 MarketInsight 同步；只有带完整回执的同步真正发布了 anchor
才成功。任一步失败都不会启动调度器。不要在 API 或多个 worker 中并行迁移或首次同步。
首次发布前应备份应用库；已有数据库不匹配时查看迁移日志并按实际版本处理，不能删除数据库
或手工插入 anchor 绕过失败。

Pod 验证成功、现有域名和证书确认后，先比较共享 Ingress，再由平台执行：

```bash
kubectl diff -f k8s/prd/ingress.yaml
kubectl apply -f k8s/prd/ingress.yaml
kubectl -n ih get ingress hkg-csop-prd-lb16-ih
```

不需要新建 DNS；使用现有域名的 `/remote/opinion-radar/`。
共享 Ingress 的 `/remote/opinion-radar` 和 `/remote/opinion-radar/api` 两条规则都指向
`ih-prd-remote-opinion-radar-webapp:3026`，由 nginx 统一执行路径改写与管理接口拦截。
前端 Service 单独指定 ALB 健康检查 `/healthz`，不改变其他应用的健康检查。

## 6. 验证、更新与回滚

```bash
kubectl -n ih get pods,deploy,svc -l app=ih-prd-remote-opinion-radar
kubectl -n ih logs deploy/ih-prd-remote-opinion-radar-worker -c migrate --tail=40
kubectl -n ih logs deploy/ih-prd-remote-opinion-radar-worker -c bootstrap-data --tail=80
kubectl -n ih logs deploy/ih-prd-remote-opinion-radar-worker -c worker --tail=40
kubectl -n ih logs deploy/ih-prd-remote-opinion-radar-srvapp -c srvapp --tail=40
kubectl -n ih exec deploy/ih-prd-remote-opinion-radar-srvapp -- python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8008/health/ready').read().decode())"
kubectl -n ih exec deploy/ih-prd-remote-opinion-radar-webapp -- wget -qO- http://127.0.0.1:8080/remote/opinion-radar/api/v1/meta
kubectl -n ih exec deploy/ih-prd-remote-opinion-radar-worker -- /opt/worker/bin/python /app/deploy/runtime.py worker /opt/worker/bin/python -m jobs.refresh check-source --codes 3033,2802 --sample-size 25
```

通过 `kubectl exec` 执行 worker 运维命令时，使用 `/opt/worker/bin/python` 并保留上述 `runtime.py worker` 前缀。
exec 使用容器原始环境，不继承主进程已规范化的数据库驱动与字符集。

确认三个服务 `Running 1/1`，前端刷新深链可用、HTTP 跳转 HTTPS、五个业务页面可读，
管理路径为 404。真实模型冒烟按 [AI 操作文档](../docs/llm-90d-operations.md) 使用
明确的模型与最小请求预算；不会由本部署脚本自动调用。AI 结论仍未经人工验证。

API 的 startup/liveness 探针使用浅 `/health`，readiness 使用 `/health/ready` 并要求目标库存在
合法 anchor；深 `/health/deep` 仍只检查数据库连通性，不返回连接信息。worker 的
startup/liveness 使用本地心跳，readiness 另外检查同一目标库的 anchor。AI 就绪情况继续由
应用管理接口解释。
根文件系统只读，只有 `/tmp`、`/home/app` 与 worker `/app/.scratch` 是限额临时目录；
任务报告和行情客户端缓存随 Pod 删除，不承担持久化职责，结果持久化在 RDS。
资源数值是首次部署起点，尚未压测，需根据内存、CPU、磁盘用量调整。

改 ConfigMap 或 Secret 后，需要重启两个 Python 服务；改镜像时重启对应服务。
先确认没有长任务在跑，再更新 worker，其退出宽限时间为 900 秒：

```bash
kubectl apply -f k8s/prd/configmap.yaml
sh k8s/create-secret.sh prd
kubectl -n ih rollout restart deploy/ih-prd-remote-opinion-radar-worker
kubectl -n ih rollout status deploy/ih-prd-remote-opinion-radar-worker --timeout=20m
kubectl -n ih rollout restart deploy/ih-prd-remote-opinion-radar-srvapp
kubectl -n ih rollout status deploy/ih-prd-remote-opinion-radar-srvapp --timeout=5m
kubectl -n ih rollout restart deploy/ih-prd-remote-opinion-radar-webapp
kubectl -n ih rollout status deploy/ih-prd-remote-opinion-radar-webapp --timeout=3m
```

回滚：按已保存的两个镜像 digest 拉取旧镜像，重新 tag 为 `latest` 并 push，再按以上顺序 restart。
ConfigMap 和 Secret 必须恢复到匹配版本；`rollout undo` 不会恢复它们，也不能可靠回退
可变的 `latest`。数据库先确认旧代码兼容，不能把镜像回滚等同于数据库回滚。

受控运维入口：`kubectl -n ih port-forward svc/ih-prd-remote-opinion-radar-srvapp 18008:8026`，
仅绑定本机回环地址。不得用 `--address 0.0.0.0` 暴露无鉴权的管理 API。

本次只生成并离线验证文件，没有推镜像、操作集群、迁移真实数据库或发起付费模型请求。
Docker 引擎未启动，镜像构建、只读容器运行、RDS 连通性与真实 ALB/TLS 仍须上线前实测。
