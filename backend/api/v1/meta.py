"""GET /api/v1/meta —— 全局常量（PRD 第 5 章表末「常量」行）。

演示期原样返回 fixtures/meta.json。正式实现时这些值改由配置与主数据表提供，
**响应形状不变**：前端只承接字段，不在屏内重算任何口径（CLAUDE.md 铁律 1）。

fixture 里的取值逐字来自 design/radar-data.js 的 PRESETS / SECTORS / STATUS_LEGEND /
HEAT_FORMULA / HEAT_NOTE / HEAT_W / LOW_SAMPLE / NEW_DAYS，与 PRD §3.1、§3.3、§3.5、
§3.6、§3.7 三方一致；STATUS_LEGEND 的 bg/fg 是设计系统的 CSS 变量名，属展示层，不下发。
"""

import json
from pathlib import Path

from flask import jsonify

from . import v1_bp

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "meta.json"


@v1_bp.get("/meta")
def meta():
    # 每次请求重读：改 fixture 不用重启，骨架阶段的调试成本比这点开销值钱。
    return jsonify(json.loads(FIXTURE.read_text(encoding="utf-8")))
