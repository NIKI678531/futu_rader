"""演示 provider：读 fixtures/demo/ 下由设计源导出的 JSON（生成器见 fixtures/generate.mjs）。

这里**没有任何口径计算**。数值逐字来自 design/radar-data.js，本模块只做「按参数键查表」。
所以它天然满足两条要求：

- 确定性：同参数永远同结果，逐字节相同（Playwright 逐字比对的前提）。
- 演示锚点冻结：ANCHOR=2026-09-01、NOW=2026-09-02 09:00 HKT 烤进 fixture，
  这里不碰系统时间，业务逻辑里没有 datetime.now()（ADR-0012）。

查不到的参数键返回 MISSING（不是 None）—— None 是合法的「字段暂不可用」取值，
用它表示「没这个键」会把两件事混成一件（铁律 2 的同一个坑，换了个位置）。
"""

import json
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "demo"


class _Missing:
    """「fixture 里没有这个参数键」的哨兵。与 None（字段暂不可用）严格区分。"""

    def __repr__(self):
        return "MISSING"

    def __bool__(self):
        return False


MISSING = _Missing()


class DemoProvider:
    name = "demo"

    def __init__(self, fixture_dir=None):
        self._dir = Path(fixture_dir) if fixture_dir else FIXTURE_DIR
        self._cache = {}

    def _table(self, name):
        if name not in self._cache:
            path = self._dir / f"{name}.json"
            if not path.exists():
                raise FileNotFoundError(
                    f"演示数据 {path} 不存在。先跑：node backend/fixtures/generate.mjs"
                )
            self._cache[name] = json.loads(path.read_text(encoding="utf-8"))
        return self._cache[name]

    def _lookup(self, table, *parts):
        key = "|".join(str(p) for p in parts)
        return self._table(table).get(key, MISSING)

    # ── PRD §5 契约函数 ────────────────────────────────────────────────

    def build_range(self, key):
        return self._lookup("ranges", key)

    def official_posts(self, range_key):
        return self._lookup("official_posts", range_key)

    def etf_mentions_for(self, account, range_key):
        return self._lookup("etf_mentions", account, range_key)
