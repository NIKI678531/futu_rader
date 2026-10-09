import importlib.util
import logging
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app import create_app

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("container_runtime", ROOT / "deploy" / "runtime.py")
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


@pytest.fixture
def production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATA_PROVIDER", "sql")
    monkeypatch.setenv("RADAR_FACT_SOURCE", "radar")
    monkeypatch.setenv("ROOT_PATH", "")
    monkeypatch.setenv("RADAR_DB_URL", "mysql+pymysql://app:password@db/app?charset=utf8mb4")


def test_production_accepts_explicit_mysql(production):
    runtime.validate("srvapp")


@pytest.mark.parametrize("value", ["", "sqlite:///local.db", "mysql+pymysql://app@db/app", "REPLACE-ME", "mysql+pymysql://app:password@db/app?charset=latin1"])
def test_production_rejects_missing_or_incomplete_database(production, monkeypatch, value):
    monkeypatch.setenv("RADAR_DB_URL", value)
    with pytest.raises(ValueError, match="RADAR_DB_URL"):
        runtime.validate("srvapp")


@pytest.mark.parametrize("driver", ["mysql", "mysql+mysqldb", "mysql+pymysql"])
def test_database_normalization_preserves_existing_environment_credentials(production, monkeypatch, driver):
    from sqlalchemy.engine import make_url

    configured = f"{driver}://reader:p%24ss%40word@source:3306/market_insight?connect_timeout=10"
    monkeypatch.setenv("MARKET_INSIGHT_DATABASE_URL", configured)
    normalized = runtime.database_url("MARKET_INSIGHT_DATABASE_URL")
    assert normalized.drivername == "mysql+pymysql"
    assert normalized.query == {"connect_timeout": "10", "charset": "utf8mb4"}
    original = make_url(configured)
    for field in ("host", "port", "database", "username", "password"):
        assert getattr(normalized, field) == getattr(original, field)
    assert make_url(os.environ["MARKET_INSIGHT_DATABASE_URL"]) == normalized


def test_production_rejects_demo_and_subpath(production, monkeypatch):
    monkeypatch.setenv("DATA_PROVIDER", "demo")
    with pytest.raises(ValueError, match="DATA_PROVIDER"):
        runtime.validate("srvapp")
    monkeypatch.setenv("DATA_PROVIDER", "sql")
    monkeypatch.setenv("ROOT_PATH", "/remote")
    with pytest.raises(ValueError, match="ROOT_PATH"):
        runtime.validate("srvapp")


def test_worker_rejects_shared_source_database(production, monkeypatch):
    monkeypatch.setenv("MARKET_INSIGHT_DATABASE_URL", os.environ["RADAR_DB_URL"])
    with pytest.raises(ValueError, match="separate"):
        runtime.validate("worker")


def test_worker_local_probe_expires_without_database_access(tmp_path, monkeypatch):
    health_file = tmp_path / "heartbeat"
    monkeypatch.setenv("WORKER_HEALTH_FILE", str(health_file))
    with pytest.raises(FileNotFoundError):
        runtime.healthcheck("worker")
    health_file.touch()
    runtime.healthcheck("worker")
    os.utime(health_file, (0, 0))
    with pytest.raises(ValueError, match="expired"):
        runtime.healthcheck("worker")


def test_deep_health_checks_database(monkeypatch):
    monkeypatch.setattr("radar_db.make_engine", lambda: create_engine("sqlite://"))
    response = create_app().test_client().get("/health/deep")
    assert response.status_code == 200
    assert response.json == {"status": "ok", "dependencies": {"database": "ok"}}


def test_shallow_health_survives_dependency_failure_without_leaking_secrets(monkeypatch):
    def unavailable():
        raise RuntimeError("mysql://private-user:private-password@private-host/app")

    monkeypatch.setattr("radar_db.make_engine", unavailable)
    client = create_app().test_client()
    assert client.get("/health").status_code == 200
    response = client.get("/health/deep")
    assert response.status_code == 503
    assert response.json == {"status": "degraded", "dependencies": {"database": "unavailable"}}


def test_gunicorn_filters_only_successful_shallow_probes():
    spec = importlib.util.spec_from_file_location("gunicorn_config", ROOT / "backend" / "gunicorn.conf.py")
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)
    probe_filter = config.HealthcheckFilter()
    record = logging.makeLogRecord({"args": {"U": "/health", "s": "200"}})
    assert not probe_filter.filter(record)
    record.args["s"] = "503"
    assert probe_filter.filter(record)
    record.args = {"U": "/api/v1/meta", "s": "200"}
    assert probe_filter.filter(record)


@pytest.mark.parametrize("invalid", [None, "missing", "duplicate", "placeholder"])
def test_secret_script_preserves_literals_and_validates_before_apply(tmp_path, invalid):
    git_bash = Path(os.getenv("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
    bash = str(git_bash) if git_bash.exists() else shutil.which("bash")
    if not bash:
        pytest.skip("Bash is required to test the deployment script")
    secret_file = tmp_path / ".env.prd"
    values = {
        "RADAR_DB_URL": "mysql+pymysql://user:encoded%24password@db/app?charset=utf8mb4",
        "MARKET_INSIGHT_DATABASE_URL": "mysql+pymysql://reader:password@source/source?charset=utf8mb4",
        "AI_PRIMARY_API_KEY": "literal$cash with spaces==",
        "AI_SYNTH_API_KEY": "literal#hash=and$cash",
        "FMP_API_KEY": "price-key",
    }
    if invalid == "missing":
        values.pop("FMP_API_KEY")
    if invalid == "placeholder":
        values["FMP_API_KEY"] = "REPLACE-ME"
    content = "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"
    if invalid == "duplicate":
        content += "FMP_API_KEY=duplicate\n"
    secret_file.write_bytes(content.replace("\n", "\r\n").encode())
    harness = r'''
kubectl() {
    echo "KUBECTL_CALLED" >&2
    case "$1" in
        -n)
            [ "$2" = ih ] || return 98
            case "$6" in
                ih-prd-remote-opinion-radar-srvapp-secret|ih-prd-remote-opinion-radar-worker-secret) ;;
                *) return 97 ;;
            esac
            for arg in "$@"; do
                case "$arg" in --from-env-file=*) cat "${arg#*=}" ;; esac
            done
            ;;
        label) shift; while [ "$#" -gt 0 ]; do
            if [ "$1" = -f ]; then shift; cat "$1"; fi
            shift
        done ;;
        apply) shift; while [ "$#" -gt 0 ]; do
            if [ "$1" = -f ]; then shift; cat "$1"; fi
            shift
        done ;;
        *) return 99 ;;
    esac
}
export -f kubectl
exec bash "$@"
'''
    result = subprocess.run(
        [bash, "-c", harness, "secret-test", (ROOT / "k8s/create-secret.sh").as_posix(), "prd", secret_file.as_posix()],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if invalid:
        assert result.returncode != 0
        assert "KUBECTL_CALLED" not in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout.count(f"RADAR_DB_URL={values['RADAR_DB_URL']}") == 2
        for key, value in values.items():
            assert f"{key}={value}" in result.stdout


@pytest.mark.parametrize("rendered_state", ["valid", "missing", "unrendered", "old-root"])
def test_nginx_startup_requires_rendered_subpath_proxy(tmp_path, rendered_state):
    git_bash = Path(os.getenv("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
    bash = str(git_bash) if git_bash.exists() else shutil.which("bash")
    if not bash:
        pytest.skip("Bash is required to test the nginx startup script")
    upstream = "ih-prd-remote-opinion-radar-srvapp:8026"
    template = (ROOT / "frontend/nginx/default.conf.template").read_text(encoding="utf-8")
    rendered = template.replace("${API_UPSTREAM}", upstream)
    if rendered_state == "unrendered":
        rendered = template
    if rendered_state == "old-root":
        rendered = rendered.replace(f"http://{upstream}/api/;", f"http://{upstream};")
    if rendered_state != "missing":
        (tmp_path / "default.conf").write_text(rendered, encoding="utf-8")
    harness = r'''
nginx() { [ "$1" = -t ] || return 99; echo NGINX_CONFIG_TEST; }
export -f nginx
export API_UPSTREAM="$1"
export NGINX_ENVSUBST_OUTPUT_DIR="$2"
exec bash "$3"
'''
    result = subprocess.run(
        [bash, "-c", harness, "nginx-test", upstream, tmp_path.as_posix(),
         (ROOT / "frontend/docker-entrypoint.d/25-require-rendered-config.sh").as_posix()],
        capture_output=True, text=True, timeout=30,
    )
    assert (result.returncode == 0) == (rendered_state == "valid"), result.stderr
    assert ("NGINX_CONFIG_TEST" in result.stdout) == (rendered_state == "valid")


@pytest.fixture
def prd_resources():
    yaml = pytest.importorskip("yaml", reason="Install PyYAML to validate Kubernetes manifests")
    return {
        (document["kind"], document["metadata"]["name"]): document
        for path in (ROOT / "k8s/prd").glob("*.yaml")
        if path.name != "secret.yaml"
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8"))
    }


def test_prd_uses_existing_namespace_and_prefixed_resources(prd_resources):
    assert not (ROOT / "k8s/prd/namespace.yaml").exists()
    for (kind, name), resource in prd_resources.items():
        assert kind != "Namespace"
        assert resource["metadata"]["namespace"] == "ih"
        if kind == "Ingress":
            assert name == "hkg-csop-prd-lb16-ih"
        else:
            assert name.startswith("ih-prd-remote-opinion-radar-")


def test_prd_selectors_and_environment_references_resolve(prd_resources):
    prefix = "ih-prd-remote-opinion-radar-"
    for component in ("srvapp", "worker", "webapp"):
        deployment = prd_resources["Deployment", prefix + component]
        pod = deployment["spec"]["template"]
        assert deployment["spec"]["selector"]["matchLabels"].items() <= pod["metadata"]["labels"].items()
        if component != "worker":
            service = prd_resources["Service", prefix + component]
            assert service["spec"]["selector"].items() <= pod["metadata"]["labels"].items()
        for container in pod["spec"].get("initContainers", []) + pod["spec"]["containers"]:
            for source in container.get("envFrom", []):
                if "configMapRef" in source:
                    assert ("ConfigMap", source["configMapRef"]["name"]) in prd_resources
                else:
                    assert source["secretRef"]["name"] == prefix + component + "-secret"


def test_prd_shared_backend_image_keeps_explicit_worker_role(prd_resources):
    yaml = pytest.importorskip("yaml")
    services = yaml.safe_load((ROOT / "docker-compose.prd.yml").read_text(encoding="utf-8"))["services"]
    registry = "449732370091.dkr.ecr.ap-east-1.amazonaws.com/ih-prd-remote-opinion-radar-"
    for component in ("srvapp", "worker", "webapp"):
        image = registry + ("webapp" if component == "webapp" else "srvapp") + ":latest"
        assert services[component]["image"] == image
        deployment = prd_resources["Deployment", "ih-prd-remote-opinion-radar-" + component]
        pod = deployment["spec"]["template"]["spec"]
        for container in pod.get("initContainers", []) + pod["containers"]:
            assert container["image"] == image
            assert container["imagePullPolicy"] == "Always"
            if component == "worker":
                assert container["workingDir"] == "/app/worker"
                assert container["command"] == services["worker"]["entrypoint"]
                assert container["command"][-3:] == ["/opt/worker/bin/python", "/app/deploy/runtime.py", "worker"]
                assert container["args"][0] == "/opt/worker/bin/python"
    assert "build" not in services["worker"]
    assert services["worker"]["healthcheck"]["test"][-2:] == ["--healthcheck", "worker"]
    dockerfile = (ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    for component in ("srvapp", "worker"):
        assert f"COPY --from=dependencies /opt/{component} /opt/{component}" in dockerfile
        assert f"/opt/{component}/bin/pip install --no-cache-dir --require-hashes" in dockerfile
    assert "COPY worker/ /app/worker/" in dockerfile


def test_shared_ingress_radar_routes_keep_nginx_access_controls(prd_resources):
    ingress = prd_resources["Ingress", "hkg-csop-prd-lb16-ih"]
    service = prd_resources["Service", "ih-prd-remote-opinion-radar-webapp"]
    assert "status" not in ingress
    assert set(ingress["metadata"]) == {"name", "namespace", "annotations"}
    assert "kubectl.kubernetes.io/last-applied-configuration" not in ingress["metadata"]["annotations"]
    routes = {route["path"]: route for route in ingress["spec"]["rules"][0]["http"]["paths"]}
    for path in ("/remote/opinion-radar", "/remote/opinion-radar/api"):
        assert routes[path]["backend"]["service"] == {
            "name": service["metadata"]["name"], "port": {"number": 3026},
        }
    assert routes["/"]["backend"]["service"]["name"] == "ih-prd-host-webapp"
    assert service["metadata"]["annotations"]["alb.ingress.kubernetes.io/healthcheck-path"] == "/healthz"
    nginx = (ROOT / "frontend/nginx/default.conf.template").read_text(encoding="utf-8")
    assert "location ^~ /remote/opinion-radar/api/v1/admin { return 404; }" in nginx
    assert "location = /remote/opinion-radar/admin { return 404; }" in nginx
    assert "proxy_pass http://${API_UPSTREAM}/api/;" in nginx
    assert "try_files $uri /remote/opinion-radar/index.html;" in nginx
    main = (ROOT / "frontend/src/main.jsx").read_text(encoding="utf-8")
    assert "basename={import.meta.env.BASE_URL}" in main