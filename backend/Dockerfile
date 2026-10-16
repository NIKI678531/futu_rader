FROM python:3.11.14-slim-bookworm AS dependencies
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/srvapp /opt/worker
WORKDIR /build/srvapp
COPY backend/requirements.txt backend/requirements.lock ./
RUN /opt/srvapp/bin/pip install --no-cache-dir --require-hashes -r requirements.lock \
    && /opt/srvapp/bin/pip install --no-cache-dir --no-index --no-deps -r requirements.txt \
    && /opt/srvapp/bin/pip check
WORKDIR /build/worker
COPY worker/requirements.txt worker/requirements.lock ./
RUN /opt/worker/bin/pip install --no-cache-dir --require-hashes -r requirements.lock \
    && /opt/worker/bin/pip install --no-cache-dir --no-index --no-deps -r requirements.txt \
    && /opt/worker/bin/pip check

FROM python:3.11.14-slim-bookworm
ENV PATH=/opt/srvapp/bin:$PATH \
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

WORKDIR /app/backend
COPY --from=dependencies /opt/srvapp /opt/srvapp
COPY --from=dependencies /opt/worker /opt/worker
COPY backend/ /app/backend/
COPY worker/ /app/worker/
COPY radar_db/ /app/radar_db/
COPY deploy/runtime.py /app/deploy/runtime.py
RUN chmod -R a+rX /app/backend /app/worker /app/radar_db /app/deploy

USER 10001:10001
EXPOSE 8008
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "/app/deploy/runtime.py", "--healthcheck", "srvapp"]
ENTRYPOINT ["/usr/bin/tini", "--", "python", "/app/deploy/runtime.py", "srvapp"]
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:create_app()"]
