"""`core/delta.py` —— 环比契约（PRD §3.1、§5）。

这个模块的三种返回是**三种语义**，测试按语义分组，不按输入分组：未知、无基数、正常。
把「不知道」和「没变化」混成同一个 0 是铁律 2 点名的错误，所以 `None` 分支单独一组，
且断言的是逐字文案而不是「有没有字」。
"""

import pytest

from core.delta import UNAVAILABLE_SHORT, UNAVAILABLE_TEXT, delta


class TestUnknown:
    """任一侧未知 → 「数据暂不可用」，绝不落成 0%。"""

    @pytest.mark.parametrize("cur,base", [(None, 100), (100, None), (None, None), (None, 0)])
    def test_propagates_instead_of_zero(self, cur, base):
        d = delta(cur, base)
        assert d == {
            "text": UNAVAILABLE_TEXT,
            "short": UNAVAILABLE_SHORT,
            "abs": None,
            "pct": None,
            "dir": 0,
        }

    def test_wording_is_verbatim_and_the_two_registers_differ(self):
        """数值位用长文案，短徽章用短文案。互换就违反铁律 2 第二段。"""
        assert UNAVAILABLE_TEXT == "数据暂不可用"
        assert UNAVAILABLE_SHORT == "暂不可用"


class TestZeroBase:
    """基数为 0 → 「新增 N」，`pct` 为 `None`。除以 0 得不出百分比。"""

    def test_growth_from_nothing_has_no_percentage(self):
        d = delta(37, 0)
        assert d["text"] == "新增 37"
        assert d["short"] == "新增"
        assert d["abs"] == 37
        assert d["pct"] is None
        assert d["dir"] == 1

    def test_still_nothing(self):
        assert delta(0, 0) == {"text": "—", "short": "—", "abs": 0, "pct": None, "dir": 0}


class TestNormal:
    def test_increase_carries_an_explicit_plus_sign(self):
        d = delta(120, 100)
        assert d["text"] == "+20（+20.0%）"
        assert d["short"] == "+20%"
        assert (d["abs"], d["pct"], d["dir"]) == (20, 20.0, 1)

    def test_decrease_uses_the_minus_from_the_number_itself(self):
        d = delta(80, 100)
        assert d["text"] == "-20（-20.0%）"
        assert d["short"] == "-20%"
        assert (d["abs"], d["pct"], d["dir"]) == (-20, -20.0, -1)

    def test_flat_is_signed_plus_zero_not_a_dash(self):
        """持平是「已知且没变」，和基数为 0 的「—」不是一回事。"""
        d = delta(100, 100)
        assert d["text"] == "+0（+0.0%）"
        assert d["dir"] == 0

    def test_rounds_away_from_zero_like_js_tofixed(self):
        """Python 的 `round`／`format` 是银行家舍入，会把 `.05` 抹平成偶数。

        `generate.mjs` 顶部特意记过这个坑：JS `(0.05).toFixed(1) === '0.1'`，而
        `format(0.05, '.1f') == '0.1'` 但 `format(0.25, '.1f') == '0.2'`。差一位小数
        本身不致命，致命的是演示 provider（JS 算的）与真实 provider（Python 算的）
        在同一个数上给出不同文案。
        """
        # 2100/10000 = 0.21 →（对不上的写法会给 0.2）
        assert delta(10021, 10000)["text"] == "+21（+0.2%）"
        # 25/10000*100 = 0.25 → JS toFixed(1) = '0.3'，banker's 会给 '0.2'
        assert delta(10025, 10000)["text"] == "+25（+0.3%）"
        # short 位是 toFixed(0)：2.5% → '3%'，banker's 会给 '2%'
        assert delta(1025, 1000)["short"] == "+3%"

    def test_pct_stays_unrounded_for_downstream_use(self):
        """文案是舍入过的，`pct` 不是 —— 前端排序／色阶用原值。"""
        assert delta(10025, 10000)["pct"] == pytest.approx(0.25)
