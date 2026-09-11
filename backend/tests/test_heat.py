"""`core/heat.py` —— 讨论热度（PRD §3.3，铁律 1 点名的三条口径之一）。

热度是全站唯一的跨产品可比指标，权重一改所有排序都跟着变，所以权重本身也在断言里。
"""

import json
from pathlib import Path

import pytest

from core.heat import WEIGHTS, heat_of

META = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "meta.json").read_text(encoding="utf-8")
)


def test_weights_match_the_ones_the_backend_hands_to_the_frontend():
    """`/api/v1/meta` 下发的权重必须就是计算用的那组。

    两者一旦分叉，界面上的公式说明会理直气壮地解释一个并没有被执行的算法。
    """
    assert WEIGHTS == {"like": 0.3, "share": 1}
    assert META["heat"]["weights"]["like"] == WEIGHTS["like"]
    assert META["heat"]["weights"]["share"] == WEIGHTS["share"]


def test_formula():
    # 100 + 0.3×50 + 1×10 = 125
    assert heat_of(100, 50, 10) == 125


def test_rounds_half_up_like_js_math_round():
    """Python 的 `round()` 是银行家舍入（`round(2.5) == 2`），JS 的 `Math.round` 不是。

    0.3 的权重让 `.5` 结尾极其常见（点赞数为 5 的奇数倍时），这不是边角情况。
    """
    # 0 + 0.3×5 + 0 = 1.5 → 2（banker's 会给 2，巧合）
    assert heat_of(0, 5, 0) == 2
    # 1 + 0.3×5 + 0 = 2.5 → 3（banker's 会给 2）
    assert heat_of(1, 5, 0) == 3
    # 3 + 0.3×5 + 0 = 4.5 → 5（banker's 会给 4）
    assert heat_of(3, 5, 0) == 5


@pytest.mark.parametrize(
    "comments,likes,shares",
    [(None, 50, 10), (100, None, 10), (100, 50, None), (None, None, None)],
)
def test_any_unknown_component_makes_the_whole_thing_unknown(comments, likes, shares):
    """`0.3 × None` 不是 0。

    转发数没采到时把它当 0，得到的热度看起来完全正常 —— 只是偏低，而且没人知道偏了多少。
    这是真实 provider 上会发生的事：少量 feed 的 `share_count` 因源侧 raw_json 截断为 NULL。
    """
    assert heat_of(comments, likes, shares) is None


def test_zero_is_a_real_value_not_a_missing_one():
    """真的没人互动 → 热度 0，是个可信数字，不能和上面的 `None` 混。"""
    assert heat_of(0, 0, 0) == 0
