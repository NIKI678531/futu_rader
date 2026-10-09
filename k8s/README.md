# PRD 部署手册

本目录按公司 EKS 部署规范落地，只包含 PRD。生产入口为内网 ALB，
一个独占根域名转发到 nginx，再由 nginx 保留 `/api` 前缀转发 Flask API。
配置不进入镜像，数据库使用外部 RDS，不在集群内部署 MySQL。

## 1. 对象与命名

| 项目 | 本项目配置 |
| --- | --- |
| AWS 账号 / 区域 | `449732370091` / `ap-east-1` |
| 应用短名 / namespace | `futuradar` / `futu-radar` |
| ECR | `449732370091.dkr.ecr.ap-east-1.amazonaws.com/prd-futuradar-{srvapp,webapp,worker}` |
| API | Deployment / Service `futuradar-srvapp`，Service `8008` → 容器 `8008` |
| 前端 | Deployment / Service `futuradar-webapp`，Service `80` → 容器 `8080` |
| worker | Deployment `futuradar-worker`，单副本 `Recreate`，不提供 HTTP，不需要 Service |
| Ingress | `hkg-csop-prd-futuradar`，`internal` ALB，HTTPS 443，HTTP 重定向 |

这些名称是本次清单的统一约定，不表示对应 ECR 仓库或 namespace 已存在。
如平台已分配其他名称，应整体替换 Deployment、Service、ConfigMap、Secret、
构建清单和脚本中的名称，不能只改 Ingress。

没有加入 UAT、CI、Entra 配置、PVC、集群 MySQL、Airflow、HPA 或 PDB。
当前三个服务均为一个副本；前端扩为两个副本时再配反亲和与 PDB。
API 无进程内调度器，使用 RollingUpdate；worker 必须保持一个实例，
不要同时启用原有 Airflow DAG 或在工作站启动另一份生产 scheduler。
旧 `deploy/kubernetes/` 中的 Secret 示例不属于本套清单，不要对仓库递归 apply。

## 2. 配置来源

- `prd/configmap.yaml`：非机密运行参数，按当前 `worker/.env`、后端配置模板及代码默认值填写。
- 根目录 `.env.prd.example`：同一套非机密值加五个空白机密字段。实际 `.env.prd` 已被 gitignore 排除。
- `create-secret.sh prd`：从实际环境文件读取固定 key，校验全部成功后更新两个 Secret。
- `prd/secret.example.yaml.txt`：只展示 key 清单，不会被 `kubectl apply -f k8s/prd/` 扫到。

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

1. 平台确认 PRD 集群、namespace 名称和三个 ECR 仓库。清单与依赖锁面向 Linux amd64。
2. 准备 Docker Compose 2.30+、kubectl、AWS CLI、POSIX shell；生产构建在 `AI-Workstation-01`。
3. 从现有安全配置填写本机 `.env.prd` 的五个机密字段，并确认对应生产权限。不要把文件提交。
4. 从集群侧验证两台 RDS 的网络、MySQL 8、`utf8mb4`、时区和账号权限。
   应用账号需要运行及 Alembic 迁移的 DDL 权限，源账号只允许 SELECT。
5. 确认应用库已有业务数据或完成首次同步。迁移只创建表结构，不会把本地 SQLite 数据搬到 RDS。
   已有库必须核对 `alembic_version`，不允许未经核验直接 stamp；空库可直接 upgrade head。
   参考 [数据库迁移说明](../radar_db/migrations/README.md)。
6. 确认出网可以访问配置的两个 AI 端点、FMP、Yahoo Finance 行情端点及系统 DNS。
7. 在 `prd/ingress.yaml` 填写真实 host 和同账号 `ap-east-1` ACM 证书 ARN。
   当前 `REPLACE-ME` 不可部署；证书必须覆盖该 host。子网优先使用控制器标签发现，不猜 subnet ID。

当前业务页没有 Entra 登录。代码未消费 `ADMIN_API_TOKEN`，所以没有把这个无效变量
放进 Secret，也没有伪造鉴权配置。生产 nginx 对 `/admin` 与 `/api/v1/admin*` 返回 404。
管理 API 仅供有 Kubernetes 权限的运维经受控端口转发访问；后端应用层仍无鉴权，
同集群网络访问也需由平台权限/网络边界控制。正式开放管理页面之前必须先实现鉴权。
不要将此 Ingress 改成公网。曾经出现在日志或聊天中的凭据应轮换，不应复用。

## 4. 构建与本地镜像验收

在工作站从仓库根目录执行，不需要把真实配置放入构建上下文：

```bash
export AWS_PROFILE=ih_ecr_prd
REGISTRY=449732370091.dkr.ecr.ap-east-1.amazonaws.com
aws sts get-caller-identity
aws ecr get-login-password --region ap-east-1 | docker login --username AWS --password-stdin "$REGISTRY"
docker compose -f docker-compose.prd.yml build
docker compose -f docker-compose.prd.yml push
SHA=$(git rev-parse HEAD)
for component in srvapp webapp worker; do
  IMAGE="$REGISTRY/prd-futuradar-$component"
  docker tag "$IMAGE:latest" "$IMAGE:$SHA"
  docker push "$IMAGE:$SHA"
done
```

只从已提交、可追溯的版本构建，记录三张镜像 digest。平台清单沿用 `latest + Always`，
同时保留 SHA 标签供回滚。基础运行时、直接及传递 Python 依赖、npm lock 均已固定；
Dockerfile 校验 lock 与 requirements 一致。Python 保留项目的 3.11，不擅自升级到模板的 3.12。
前端强制构建参数 `VITE_API_BASE=/api/v1`；不需要 Entra 参数，也不支持子路径挂载。

`.dockerignore` 在仓库根，因为三个镜像都使用根构建上下文；前端仍需读取只读设计资源。
仅部署脚本采用 LF，保留 `design/` 的逐字节约束。

在有 RDS 网络通路、已准备应用库的隔离验收环境，可以运行以下命令验证镜像。
此命令会启动真实采集/AI 调度，不要在生产 scheduler 运行期间另起一份：

```bash
docker compose -f docker-compose.prd.yml run --rm worker python -m alembic -c /app/radar_db/alembic.ini upgrade head
docker compose -f docker-compose.prd.yml up -d
curl -f http://127.0.0.1:18008/health
curl -f http://127.0.0.1:18008/health/deep
curl -f http://127.0.0.1:18080/healthz
curl -f http://127.0.0.1:18080/sector
curl -f http://127.0.0.1:18080/api/v1/meta
docker compose -f docker-compose.prd.yml down
```

Compose 的 `format: raw` 与 Secret 脚本使用同一份字面值。构建不要求 `.env.prd` 存在；
启动时缺少配置会由守卫拒绝。普通本地开发仍使用现有命令与端口，不运行这套 PRD 实例。

## 5. 首次发布顺序

以下命令不包含 Ingress。不要在占位符未填好时直接 apply 整个目录。

```bash
kubectl config current-context
kubectl apply -f k8s/prd/namespace.yaml -f k8s/prd/configmap.yaml
sh k8s/create-secret.sh prd
kubectl apply -f k8s/prd/worker-deployment.yaml
kubectl -n futu-radar rollout status deploy/futuradar-worker --timeout=20m
kubectl apply -f k8s/prd/srvapp-deployment.yaml -f k8s/prd/srvapp-service.yaml
kubectl -n futu-radar rollout status deploy/futuradar-srvapp --timeout=5m
kubectl apply -f k8s/prd/webapp-deployment.yaml -f k8s/prd/webapp-service.yaml
kubectl -n futu-radar rollout status deploy/futuradar-webapp --timeout=3m
```

worker 的 initContainer 先运行 `alembic upgrade head`；失败时不启动调度器。
不要在 API 或多个 worker 中并行迁移。首次发布前应备份应用库；已有数据库不匹配时
查看迁移日志并按实际版本处理，不能删除数据库绕过失败。

Pod 验证成功、域名和证书确认后，最后执行：

```bash
kubectl apply -f k8s/prd/ingress.yaml
kubectl -n futu-radar get ingress hkg-csop-prd-futuradar
```

DNS CNAME 指向该内网 ALB 地址。只配置一条 `/` → `futuradar-webapp:80` 规则，
不另配 `/api` Ingress 规则，不开第二个域名，也不去掉 `/api` 前缀。

## 6. 验证、更新与回滚

```bash
kubectl -n futu-radar get pods,deploy,svc,ingress
kubectl -n futu-radar logs deploy/futuradar-worker -c migrate --tail=40
kubectl -n futu-radar logs deploy/futuradar-worker -c worker --tail=40
kubectl -n futu-radar logs deploy/futuradar-srvapp -c srvapp --tail=40
kubectl -n futu-radar exec deploy/futuradar-srvapp -- python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8008/health/deep').read().decode())"
kubectl -n futu-radar exec deploy/futuradar-webapp -- wget -qO- http://127.0.0.1:8080/api/v1/meta
kubectl -n futu-radar exec deploy/futuradar-worker -- python /app/deploy/runtime.py worker python -m jobs.refresh check-source --codes 3033,2802 --sample-size 25
```

通过 `kubectl exec` 执行 worker 运维命令时，保留上述 `runtime.py worker` 前缀。
exec 使用容器原始环境，不继承主进程已规范化的数据库驱动与字符集。

确认三个服务 `Running 1/1`，前端刷新深链可用、HTTP 跳转 HTTPS、五个业务页面可读，
管理路径为 404。真实模型冒烟按 [AI 操作文档](../docs/llm-90d-operations.md) 使用
明确的模型与最小请求预算；不会由本部署脚本自动调用。AI 结论仍未经人工验证。

API 探针仅使用浅 `/health`；深 `/health/deep` 只检查 API 自身依赖的应用库，不返回
连接信息。worker 用独立于数据库结果的本地心跳检查，AI 就绪情况继续由应用管理接口解释。
根文件系统只读，只有 `/tmp`、`/home/app` 与 worker `/app/.scratch` 是限额临时目录；
任务报告和行情客户端缓存随 Pod 删除，不承担持久化职责，结果持久化在 RDS。
资源数值是首次部署起点，尚未压测，需根据内存、CPU、磁盘用量调整。

改 ConfigMap 或 Secret 后，需要重启两个 Python 服务；改镜像时重启对应服务。
先确认没有长任务在跑，再更新 worker，其退出宽限时间为 900 秒：

```bash
kubectl apply -f k8s/prd/configmap.yaml
sh k8s/create-secret.sh prd
kubectl -n futu-radar rollout restart deploy/futuradar-worker
kubectl -n futu-radar rollout status deploy/futuradar-worker --timeout=20m
kubectl -n futu-radar rollout restart deploy/futuradar-srvapp
kubectl -n futu-radar rollout status deploy/futuradar-srvapp --timeout=5m
kubectl -n futu-radar rollout restart deploy/futuradar-webapp
kubectl -n futu-radar rollout status deploy/futuradar-webapp --timeout=3m
```

回滚：将之前保存的三个 SHA 镜像重新 tag 为 `latest` 并 push，再按以上顺序 restart。
ConfigMap 和 Secret 必须恢复到匹配版本；`rollout undo` 不会恢复它们，也不能可靠回退
可变的 `latest`。数据库先确认旧代码兼容，不能把镜像回滚等同于数据库回滚。

受控运维入口：`kubectl -n futu-radar port-forward svc/futuradar-srvapp 18008:8008`，
仅绑定本机回环地址。不得用 `--address 0.0.0.0` 暴露无鉴权的管理 API。

本次只生成并离线验证文件，没有推镜像、操作集群、迁移真实数据库或发起付费模型请求。
Docker 引擎未启动，镜像构建、只读容器运行、RDS 连通性与真实 ALB/TLS 仍须上线前实测。