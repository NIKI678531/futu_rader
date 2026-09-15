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
def test_an_unknown_component_without_disclosure_makes_the_whole_thing_unknown(comments, likes, shares):
    """`0.3 × None` 不是 0。

    转发数没采到时把它当 0，得到的热度看起来完全正常 —— 只是偏低，而且没人知道偏了多少。
    没有 `unknown_posts` 这个披露（默认 0），任何一项未知仍然让整体未知：调用方没有说出
    「差了几帖」，就没有资格给一个数。
    """
    assert heat_of(comments, likes, shares) is None


@pytest.mark.parametrize("comments,likes", [(None, 50), (100, None), (None, None)])
def test_comments_or_likes_unknown_is_still_unknown_even_with_disclosure(comments, likes):
    """下限口径只对转发数成立（ADR-0022）。

    评论量与点赞是 dump 的列，没有「已知部分」可言；它们为 None 意味着这一行本身没取到，
    不是「少了几帖」，`unknown_posts` 说不出它们差在哪里。
    """
    assert heat_of(comments, likes, 10, unknown_posts=3) is None


def test_with_unknown_posts_disclosed_the_heat_is_the_sum_of_the_known_parts():
    """ADR-0022：窗口内有转发未知的帖子时，热度按已知项计算并披露未知帖数（下限）。

    结果**等于**只用已知项算出来的数 —— 不补平均值、不做估算。估算出来的数字和真的
    长得一样，而「下限 ＋ 差了 3 帖」是一句能被核对的话。
    """
    assert heat_of(100, 50, 10, unknown_posts=3) == heat_of(100, 50, 10) == 125
    # 已知和为 0（窗口内唯一那一帖就是坏的）：下限是 评论 ＋ 0.3 × 点赞。
    assert heat_of(100, 50, 0, unknown_posts=1) == 115


def test_shares_none_with_zero_unknown_posts_stays_none():
    """`shares=None` 且 `unknown_posts=0` 是「转发数整体未知、没有任何披露」—— 仍是 None。

    这是 demo provider 与任何没数过未知帖数的调用方会落到的分支；只有说出了未知帖数，
    已知和才能顶上去。
    """
    assert heat_of(100, 50, None) is None
    assert heat_of(100, 50, None, unknown_posts=0) is None


def test_shares_none_with_unknown_posts_means_the_known_sum_is_zero():
    """已知和为 None 而未知帖数大于零：调用方一帖的转发数都没有，已知和就是 0。

    披露了未知帖数，这个 0 是「已知部分为零」，不是「当作零」。
    """
    assert heat_of(100, 50, None, unknown_posts=2) == 115


def test_zero_is_a_real_value_not_a_missing_one():
    """真的没人互动 → 热度 0，是个可信数字，不能和上面的 `None` 混。"""
    assert heat_of(0, 0, 0) == 0
