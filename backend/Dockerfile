FROM python:3.11.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONPATH=/app \
    APP_ENV=production \
    TZ=Asia/Hong_Kong \
    HOME=/home/app \
    XDG_CACHE_HOME=/home/app/.cache

RUN apt-get update && apt-get install -y --no-install-recommends tini tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 app && useradd -r -u 10001 -g app -d /home/app app \
    && mkdir -p /home/app && chmod 1777 /home/app /tmp

WORKDIR /app/backend
COPY backend/requirements.txt backend/requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && pip install --no-cache-dir --no-index --no-deps -r requirements.txt \
    && pip check

COPY backend/ /app/backend/
COPY radar_db/ /app/radar_db/
COPY deploy/runtime.py /app/deploy/runtime.py

USER 10001:10001
EXPOSE 8008
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "/app/deploy/runtime.py", "--healthcheck", "srvapp"]
ENTRYPOINT ["/usr/bin/tini", "--", "python", "/app/deploy/runtime.py", "srvapp"]
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:create_app()"]
