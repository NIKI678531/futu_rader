"""环比 `delta(cur, base)` 的 Python 实现（PRD §3.1、§5 契约）。

和 `core/calendar.py` 一样是**叶子模块**：不 import provider，供真实 provider 与端点共用。

演示 provider 不走这里 —— fixture 里的 delta 是 `fixtures/generate.mjs` 在 JS 侧算的，
逐字来自设计源。`generate.mjs` 顶部特意写明了为什么不在 Python 侧重算：`toFixed(1)` 的
舍入在 `.05` 边界上 JS 与 Python 不一致。本模块因此不用 `format(x, '.1f')`（Python 是
banker's rounding，`0.05 → 0.0`），而是**离零取半**，与 JS 一致。

三种返回，三种语义，不可互相替代：

- `cur` 或 `base` 为 `None` → 「数据暂不可用」。**不是 0%**（铁律 2）。
- `base == 0` 且有增长 → 「新增 N」，`pct` 为 `None`。除以 0 得不出百分比，
  写 `+∞%` 或 `+100%` 都是编的。
- 其余 → 带符号的绝对值与百分比。
"""

from decimal import ROUND_HALF_UP, Decimal

# 长文案给数值位与环比位（PRD §3.1、§5），短徽章给状态图例（PRD §3.6）。两套并存，
# 不可互换 —— CLAUDE.md 铁律 2 第二段。
UNAVAILABLE_TEXT = "数据暂不可用"
UNAVAILABLE_SHORT = "暂不可用"


def _fixed(x, digits):
    """JS `Number.prototype.toFixed` 的等价物：离零取半，不是 Python 的银行家舍入。"""
    q = Decimal(1).scaleb(-digits)
    return str(Decimal(repr(x)).quantize(q, rounding=ROUND_HALF_UP))


def delta(cur, base):
    if cur is None or base is None:
        return {
            "text": UNAVAILABLE_TEXT,
            "short": UNAVAILABLE_SHORT,
            "abs": None,
            "pct": None,
            "dir": 0,
        }
    d = cur - base
    if base == 0:
        if d > 0:
            return {"text": f"新增 {d}", "short": "新增", "abs": d, "pct": None, "dir": 1}
        return {"text": "—", "short": "—", "abs": 0, "pct": None, "dir": 0}
    p = d / base * 100
    return {
        "text": f"{'+' if d >= 0 else ''}{d}（{'+' if p >= 0 else ''}{_fixed(p, 1)}%）",
        "short": f"{'+' if p >= 0 else ''}{_fixed(p, 0)}%",
        "abs": d,
        "pct": p,
        "dir": 1 if d > 0 else (-1 if d < 0 else 0),
    }
