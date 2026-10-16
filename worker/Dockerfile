FROM python:3.11.14-slim-bookworm AS dependencies
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /build
COPY worker/requirements.txt worker/requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && pip install --no-cache-dir --no-index --no-deps -r requirements.txt \
    && pip check

FROM python:3.11.14-slim-bookworm
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONPATH=/app:/app/worker \
    APP_ENV=production \
    TZ=Asia/Hong_Kong \
    HOME=/home/app \
    XDG_CACHE_HOME=/home/app/.cache \
    WORKER_HEALTH_FILE=/tmp/worker-heartbeat
RUN apt-get update && apt-get install -y --no-install-recommends tini tzdata ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 app && useradd -r -u 10001 -g app -d /home/app app \
    && mkdir -p /home/app /app/.scratch && chmod 1777 /home/app /app/.scratch /tmp
COPY --from=dependencies /opt/venv /opt/venv
COPY worker/ /app/worker/
COPY radar_db/ /app/radar_db/
COPY backend/core/ /app/backend/core/
COPY backend/fixtures/demo/master.json /app/backend/fixtures/demo/master.json
COPY backend/fixtures/product_catalog_additions.json /app/backend/fixtures/product_catalog_additions.json
COPY deploy/runtime.py /app/deploy/runtime.py
RUN chmod -R a+rX /app/backend /app/worker /app/radar_db /app/deploy

WORKDIR /app/worker
USER 10001:10001
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD ["python", "/app/deploy/runtime.py", "--healthcheck", "worker"]
ENTRYPOINT ["/usr/bin/tini", "--", "python", "/app/deploy/runtime.py", "worker"]
CMD ["python", "scheduler.py"]
