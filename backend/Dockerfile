# 构建上下文是**仓库根**，不是 backend/ —— 见 docker-compose.yml 顶部注释。
# 所以下面的 COPY 路径都带 backend/ 前缀。
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    APP_HOST=0.0.0.0 \
    APP_PORT=8008

RUN groupadd -g 1000 appuser && useradd -r -u 1000 -g appuser appuser

# curl 供 HEALTHCHECK；gcc/g++ 是 clickhouse-driver 落到源码分发时的编译兜底，
# 依赖集定稿后若确认全程走 wheel 可以去掉，能省掉约 200MB。
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc g++ \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY backend/ /app
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8008

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8008/health || exit 1

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8008", "app:create_app()"]
