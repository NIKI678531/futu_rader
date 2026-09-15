"""`core/calendar.py` —— 区间与时间桶的 Python 实现（PRD §3.1、§5）。

守的是同一件事：**同一套日历只有一份定义**（铁律 1）。现在它有两个运行时——浏览器里的
`design/radar-data.js` `buildRange()`，和后端的 `core/calendar.build()`。两份代码不可避免，
但两套**行为**不可接受，所以这里拿演示 fixture（由设计源逐字导出）当基准逐字节比对。

这一条一旦红，说明真实 provider 下发的横轴与设计源不是同一段时间——热力图、趋势图、
K 线三者的对齐就全错了，而错法是安静的。
"""

import json
from datetime import date
from pathlib import Path

import pytest

from core.calendar import PRESETS, build, parse_anchor

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "demo" / "ranges.json").read_text(
        encoding="utf-8"
    )
)
# 设计源的锚点，ADR-0012 冻结在这一天。
DEMO_ANCHOR = date(2026, 9, 1)


@pytest.mark.parametrize("key", list(FIXTURE))
def test_matches_the_design_source_byte_for_byte(key):
    assert build(key, DEMO_ANCHOR) == FIXTURE[key]


@pytest.mark.parametrize("key", list(PRESETS))
def test_anchor_moves_the_whole_window(key):
    """锚点不是常量。真实数据的锚点是 2026-08-25，区间必须跟着走。"""
    r = build(key, date(2026, 8, 25))
    assert r["to"] == "2026-08-25"
    assert r["dates"][-1] == "2026-08-25"
    assert len(r["dates"]) == r["days"]


@pytest.mark.parametrize("key", list(PRESETS))
def test_benchmark_window_is_adjacent_and_equal_length(key):
    r = build(key, date(2026, 8, 25))
    assert r["benchTo"] < r["from"]
    assert (date.fromisoformat(r["benchTo"]) - date.fromisoformat(r["benchFrom"])).days == (
        r["days"] - 1
    )
    assert (date.fromisoformat(r["from"]) - date.fromisoformat(r["benchTo"])).days == 1


def test_no_fallback_to_today_when_the_anchor_is_missing():
    """拿不到锚点就返回 None，**不用系统时间兜底**。

    今天是 2026-09-10，数据止于 2026-08-26。按系统时间算「近 7 天」会得到一个一条数据
    都没有的窗口——在界面上和「这周确实没人发帖」长得一模一样（铁律 2 的失败模式）。
    """
    assert parse_anchor(None) is None
    assert parse_anchor("") is None
    assert parse_anchor("not-a-date") is None
    assert parse_anchor("2026-08-25 23:59:59") == date(2026, 8, 25)


@pytest.mark.parametrize("anchor", [date(2026, 8, 25), date(2026, 8, 31), date(2024, 2, 29), date(2026, 9, 1)])
def test_month_to_date_stops_at_actual_data_anchor(anchor):
    result = build("mtd", anchor)
    assert result["from"] == anchor.replace(day=1).isoformat()
    assert result["to"] == anchor.isoformat()
    assert result["days"] == anchor.day
    assert len(result["dates"]) == anchor.day
    assert result["buckets"][0]["day"] == result["from"]
    assert sum(bucket["span"] for bucket in result["buckets"]) == pytest.approx(anchor.day)
