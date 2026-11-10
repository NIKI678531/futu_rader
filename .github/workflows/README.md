# Futu Radar delivery workflows

This directory contains three workflows adapted for Futu Radar:

- `ci-security-build.yml` tests Python and frontend code, builds both production images, and runs Gitleaks, Semgrep, and Trivy.
- `cd-security-scan-release.yml` builds and deploys `srvapp`/`webapp` to UAT, runs ZAP, and optionally continues through a protected PRD approval.
- `daily-uat-security-scan.yml` runs at 00:00 Hong Kong time after a successful PRD release in the previous 24 hours.

## GitHub environments

Create these environments:

- `uat` for UAT deployment and DAST jobs.
- `prd` for the production approval job. Configure required reviewers here so a PRD dispatch cannot deploy without approval.

All jobs use GitHub-hosted runners. The UAT DAST job creates a temporary ZAP Job
inside the UAT cluster, so the runner does not need direct network access to an
internal UAT Service.

## Required secrets

Configure these as repository or organization secrets:

| Secret | Purpose |
| --- | --- |
| `AWS_CREDENTIALS` | AWS shared credentials file containing `ecr_uat`, `eks_uat`, `ecr_prd`, and `eks_prd` |
| `AWS_CONFIG` | AWS shared config file for those four profiles |

Do not place application credentials, database URLs, or API keys in workflow files or tracked Kubernetes Secret manifests.

## Required variables

| Variable | Required | Default / purpose |
| --- | --- | --- |
| `UAT_EKS_CLUSTER_NAME` | yes | `hkg-csop-aws-uat-vpc02-eks004-ahmiao` |
| `PRD_EKS_CLUSTER_NAME` | yes for PRD | EKS cluster updated after PRD approval |
| `AWS_REGION` | no | `ap-east-1` |
| `UAT_AWS_ACCOUNT_ID` | no | `978533598453` |
| `PRD_AWS_ACCOUNT_ID` | no | `449732370091` |
| `UAT_DAST_TARGET` | no | Defaults to the UAT webapp ClusterIP Service URL |
| `ENABLE_GHAS_CODE_SCANNING` | no | Set to `true` only when GitHub Advanced Security code scanning is enabled |

The following variables override project defaults when platform naming differs:

| Variable | Default |
| --- | --- |
| `UAT_K8S_NAMESPACE` | `ih` |
| `UAT_ECR_SRVAPP_REPOSITORY` | `ih-uat-remote-opinion-radar-srvapp` |
| `UAT_ECR_WEBAPP_REPOSITORY` | `ih-uat-remote-opinion-radar-webapp` |
| `UAT_SRVAPP_DEPLOYMENT` | `ih-uat-remote-opinion-radar-srvapp` |
| `UAT_WORKER_DEPLOYMENT` | `ih-uat-remote-opinion-radar-worker` |
| `UAT_WEBAPP_DEPLOYMENT` | `ih-uat-remote-opinion-radar-webapp` |
| `UAT_WEBAPP_SERVICE` | `ih-uat-remote-opinion-radar-webapp` |
| `UAT_WEBAPP_SERVICE_PORT` | `3026` |
| `PRD_K8S_NAMESPACE` | `ih` |
| `PRD_ECR_SRVAPP_REPOSITORY` | `ih-prd-remote-opinion-radar-srvapp` |
| `PRD_ECR_WEBAPP_REPOSITORY` | `ih-prd-remote-opinion-radar-webapp` |
| `PRD_SRVAPP_DEPLOYMENT` | `ih-prd-remote-opinion-radar-srvapp` |
| `PRD_WORKER_DEPLOYMENT` | `ih-prd-remote-opinion-radar-worker` |
| `PRD_WEBAPP_DEPLOYMENT` | `ih-prd-remote-opinion-radar-webapp` |
| `PRD_SRVAPP_SERVICE` | `ih-prd-remote-opinion-radar-srvapp` |
| `PRD_WEBAPP_SERVICE` | `ih-prd-remote-opinion-radar-webapp` |
| `PRD_SRVAPP_CONFIGMAP` | `ih-prd-remote-opinion-radar-srvapp-config` |
| `PRD_WORKER_CONFIGMAP` | `ih-prd-remote-opinion-radar-worker-config` |
| `PRD_SRVAPP_SECRET` | `ih-prd-remote-opinion-radar-srvapp-secret` |
| `PRD_WORKER_SECRET` | `ih-prd-remote-opinion-radar-worker-secret` |
| `PRD_INGRESS_NAME` | `hkg-csop-prd-lb16-ih` |
| `PRD_INGRESS_API_PATH` | `/remote/opinion-radar/api` |
| `PRD_INGRESS_WEB_PATH` | `/remote/opinion-radar` |

All ECR repositories and Kubernetes runtime resources must exist before the first
release. The CD workflow updates existing Deployments; it does not create
clusters, repositories, namespaces, ConfigMaps, Secrets, Services, or Ingresses.
The intended UAT namespace is `ih` (the cluster name ends in `ahmiao`; that is not
the application namespace). The prior Argo UAT definition used
`CreateNamespace=true`, so platform automation must recreate `ih` when absent.

The PRD job validates the two ConfigMaps, two Kubernetes Secrets, two Services,
and both routes in the existing shared Ingress before changing an image. It never
applies `k8s/prd/ingress.yaml`, because that manifest owns routes shared by other
applications. The application keys currently expected in Kubernetes are
`RADAR_DB_URL`, `MARKET_INSIGHT_DATABASE_URL`, `AI_PRIMARY_API_KEY`,
`AI_SYNTH_API_KEY`, and `FMP_API_KEY`; their values do not belong in GitHub
workflow files.

## First activation

1. Remove plaintext secrets from Git tracking and rotate any credential that has ever been committed.
2. Configure the secrets, variables, environments, Kubernetes resources, and
   environment-specific application credentials above.
3. Run `CI Security Build Pipeline` manually and resolve all static-security findings.
4. Run `CD Security Scan Release Pipeline` with target `UAT` and verify the two images, three Deployments, and ZAP report.
5. Protect the `prd` environment with required reviewers, then test a `PRD` dispatch.
