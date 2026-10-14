"""舆情雷达后端 API 入口。

只读舆情工作台的查询侧。当前唯一有实现的端点是 GET /api/v1/meta；
PRD 第 5 章其余 22 组端点的映射表见 plan.md §2.1，前端尚未接线。

响应信封（plan.md Q7 定案）：
- 200 一律 {"status": ..., "data": ...}，status 取 PRD §3.6 五态
  ok / empty / unavailable / low_sample / na —— **数据缺失也返回 200，不返回 5xx**。
- 传输层错误（404/405/500）走 {"error": {...}}，不带 status 字段：五态描述的是
  数据可得性，不是请求成败，两者混用会让前端分不清「没这个产品」和「没这条路由」。
"""

import logging
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
from flask import Flask, jsonify
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

from api.v1 import v1_bp

_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_ENV):
    load_dotenv(_ENV, override=False)

logging.basicConfig(
    level=os.getenv("RADAR_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)


def create_app():
    app = Flask(__name__)
    CORS(app)

    # 默认会把中文转义成 \uXXXX。本项目的响应通篇是逐字中文文案（六态、热度公式、
    # 页面文案），转义后 curl 不可读、体积涨数倍，逐字比对也没法直接 grep。
    app.json.ensure_ascii = False

    # 反代子路径（plan.md Q6：预留不启用）。留空时行为与无前缀完全一致。
    root = os.getenv("ROOT_PATH", "").rstrip("/")

    app.register_blueprint(v1_bp, url_prefix=root + "/api/v1")

    @app.get(root + "/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get(root + "/health/deep")
    def deep_health():
        from sqlalchemy import text
        from radar_db import make_engine

        engine = None
        try:
            engine = make_engine()
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:
            return jsonify({"status": "degraded", "dependencies": {"database": "unavailable"}}), 503
        finally:
            if engine is not None:
                engine.dispose()
        return jsonify({"status": "ok", "dependencies": {"database": "ok"}})

    @app.get(root + "/health/ready")
    def ready_health():
        """Require a source-backed calendar anchor before accepting traffic."""
        from sqlalchemy import select
        from radar_db import make_engine
        from radar_db.schema import meta_kv

        engine = None
        try:
            engine = make_engine()
            with engine.connect() as connection:
                value = connection.execute(
                    select(meta_kv.c.v).where(meta_kv.c.k == "anchor")
                ).scalar_one_or_none()
        except Exception:
            return jsonify({
                "status": "not_ready",
                "dependencies": {"database": "unavailable", "anchor": "unknown"},
            }), 503
        finally:
            if engine is not None:
                engine.dispose()

        if value is None or not str(value).strip():
            return jsonify({
                "status": "not_ready",
                "dependencies": {"database": "ok", "anchor": "missing"},
            }), 503
        try:
            date.fromisoformat(str(value).strip()[:10])
        except ValueError:
            return jsonify({
                "status": "not_ready",
                "dependencies": {"database": "ok", "anchor": "invalid"},
            }), 503
        return jsonify({
            "status": "ok",
            "dependencies": {"database": "ok", "anchor": "ok"},
        })

    @app.errorhandler(HTTPException)
    def json_errors(e):
        return jsonify({"error": {"code": e.code, "message": e.description}}), e.code

    return app


if __name__ == "__main__":
    create_app().run(
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "8008")),
        debug=os.getenv("APP_ENV", "development") != "production",
    )
