import os
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from sqlalchemy.engine import make_url


def required(name):
    value = os.getenv(name, "").strip()
    if not value or "REPLACE-ME" in value.upper():
        raise ValueError(f"Missing production configuration: {name}")
    return value


def database_url(name):
    try:
        url = make_url(required(name))
        valid = (
            url.drivername in {"mysql", "mysql+mysqldb", "mysql+pymysql"}
            and url.host and url.database and url.username and url.password
            and url.query.get("charset", "utf8mb4") == "utf8mb4"
        )
    except Exception:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a complete MySQL URL compatible with utf8mb4")
    url = url.set(drivername="mysql+pymysql", query={**url.query, "charset": "utf8mb4"})
    os.environ[name] = url.render_as_string(hide_password=False)
    return url


def validate(service):
    if os.getenv("APP_ENV") == "development":
        return
    if required("APP_ENV") != "production":
        raise ValueError("APP_ENV must be production")
    if os.getenv("RADAR_FACT_SOURCE", "radar") != "radar":
        raise ValueError("PRD requires RADAR_FACT_SOURCE=radar")
    target = database_url("RADAR_DB_URL")
    if service == "srvapp":
        if required("DATA_PROVIDER") != "sql" or os.getenv("ROOT_PATH", ""):
            raise ValueError("PRD requires DATA_PROVIDER=sql and an empty ROOT_PATH")
        return
    if service != "worker":
        raise ValueError("Unknown service")
    source = database_url("MARKET_INSIGHT_DATABASE_URL")
    from radar_db.source_db import require_separate_databases

    require_separate_databases(target, source)
    required("FMP_API_KEY")
    if os.getenv("AI_DATA_GOVERNANCE_APPROVED", "true").lower() == "false":
        return
    from ai.config import load, synthesis

    config = load()
    summary = synthesis(config)
    if summary.provider == "typesafe_system_one":
        raise ValueError("TypeSafe requires a separate AI_SYNTH generation model")
    for url in (config.base_url, summary.base_url):
        if not url.startswith("https://"):
            raise ValueError("Production AI endpoints must use HTTPS")


def healthcheck(service):
    if service == "worker":
        path = Path(os.getenv("WORKER_HEALTH_FILE", "/tmp/worker-heartbeat"))
        age = time.time() - path.stat().st_mtime
        if not -5 <= age < 180:
            raise ValueError("Worker heartbeat expired")
    elif service == "srvapp":
        with urlopen("http://127.0.0.1:8008/health", timeout=3) as response:
            if response.status != 200:
                raise ValueError("API health check failed")
    else:
        raise ValueError("Unknown service")


if __name__ == "__main__":
    try:
        if sys.argv[1] == "--healthcheck":
            healthcheck(sys.argv[2])
        else:
            validate(sys.argv[1])
            os.execvp(sys.argv[2], sys.argv[2:])
    except Exception:
        print("Container startup/health check failed; verify runtime configuration.", file=sys.stderr)
        sys.exit(1)