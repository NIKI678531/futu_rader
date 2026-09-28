# 构建上下文是**仓库根**，不是 worker/ —— 见 docker-compose.yml 顶部注释。
FROM python:3.11-slim

WORKDIR /app/worker

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd -g 1000 appuser && useradd -r -u 1000 -g appuser appuser

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ \
    && rm -rf /var/lib/apt/lists/*

COPY worker/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY worker/ /app/worker
# 表结构 backend 与 worker 共用一份，源在仓库根（radar_db/__init__.py 说明了为什么）。
COPY radar_db/ /app/radar_db
# Layer B（jobs/synthesize.py）以只读方式 import backend/core 的叶子模块组事实 JSON ——
# 口径只有那一份实现（铁律 1），worker 不重写公式；产品主数据也在 backend 下。
COPY backend/core/ /app/backend/core
COPY backend/fixtures/demo/master.json /app/backend/fixtures/demo/master.json
RUN chown -R appuser:appuser /app

USER appuser

CMD ["python", "scheduler.py"]
