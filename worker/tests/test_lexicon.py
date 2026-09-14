"""词表测试 —— 别名不冲突、识别不误杀、个股词表不把 ETF 当个股。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

import pytest  # noqa: E402

from ai.lexicon import offpool_stocks, product_aliases  # noqa: E402


@pytest.fixture(scope="module")
def plex():
    return product_aliases.ProductLexicon()


@pytest.fixture(scope="module")
def slex():
    return offpool_stocks.StockLexicon()


def test_every_product_has_aliases_and_name_forms(plex):
    assert len(plex.by_code) == 120
    for code, p in plex.by_code.items():
        assert len(p["aliases"]) >= 3, code
        assert p["name_forms"], code


def test_unique_aliases_do_not_collide_across_products(plex):
    owner = {}
    for code, p in plex.by_code.items():
        for a in p["aliases"]:
            key = a.lower()
            assert key not in owner or owner[key] == code, f"{a!r} 同时指向 {owner.get(key)} 与 {code}"
            owner[key] = code


def test_nicknames_only_reference_pool_codes(plex):
    for code in product_aliases.nicknames():
        assert code in plex.by_code, code
    for fam, spec in product_aliases.family_specs().items():
        for code in spec["codes"]:
            assert code in plex.by_code, (fam, code)


def test_simplified_traditional_roundtrip_on_names(plex):
    assert product_aliases.to_simplified("恒生科技指數ETF") == "恒生科技指数ETF"
    assert product_aliases.to_traditional("恒生科技指数ETF") == "恒生科技指數ETF"
    # 同业 3032 是简体名，繁体化后与 3033 全名相同 —— 允许（这是产品命名的事实）。
    assert "恒生科技指數ETF" in plex.by_code["3032"]["name_forms"]


@pytest.mark.parametrize(
    "text,code,expected",
    [
        ("$03033.HK$ 今日跌得好慘", "3033", True),
        ("3033 呢隻仲可以入", "3033", True),
        ("03033 加倉", "3033", True),
        ("恒科呢隻要唔要走", "3033", True),           # 族叫法
        ("南方恒科費率貴", "3033", True),               # 俗称
        ("恒生科技指数ETF 好定", "3033", True),         # 简体全名
        ("騰訊今日爆升", "3033", False),
        ("27200 股成交", "7200", False),                # 裸代码词边界
        ("7200.HK 兩倍", "7200", True),
        ("英偉達業績好", "7788", True),                 # 标的股是族叫法
        ("盈富基金", "2800", True),
        ("", "3033", False),
        (None, "3033", False),
    ],
)
def test_references(plex, text, code, expected):
    assert plex.references(text, code) is expected


def test_deictic_and_product_talk(plex):
    assert plex.mentions_deictic("呢隻可以入")
    assert plex.mentions_deictic("點差太大")
    assert plex.mentions_deictic("佢升得好勁")
    assert not plex.mentions_deictic("騰訊業績好")


def test_underlying_terms_only_for_single_stock_products(plex):
    assert "英偉達" in plex.underlying_terms("7788")
    assert "Tesla" in plex.underlying_terms("7366")
    assert plex.underlying_terms("3033") == set()


def test_product_block_is_whitelist_shaped(plex):
    b = plex.product_block("3033")
    assert set(b) == {"code", "name", "aliases"}
    assert b["code"] == "3033"
    assert "南方恒科" in b["aliases"]
    assert len(b["aliases"]) <= 12


# ── 个股词表 ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("$00700.HK$ 今日爆升", {"騰訊"}),
        ("腾讯拖累指数", {"騰訊"}),
        ("Tencent 業績", {"騰訊"}),
        ("metaverse 概念", set()),                # 单词边界
        ("Meta 業績好", {"Meta"}),
        ("2020年買入", set()),                    # 年份不是安踏
        ("安踏 2020 好強", {"安踏"}),
        ("0.0001 的差價", set()),
        ("00005 大笨象", {"匯豐"}),
        ("恒科呢隻要唔要走", set()),              # 池内 ETF 不在个股词表
        ("", set()),
    ],
)
def test_stocks_in(slex, text, expected):
    assert slex.stocks_in(text) == expected


def test_ignore_terms_drop_underlying(slex, plex):
    # 在 7788 讨论区里，「英偉達」是标的股，不算跑题 —— 词表本来就不含它；
    # 若将来把 NVDA 加进静态表，这条断言会提醒要走 underlying_terms。
    assert slex.stocks_in("英偉達兩倍今日勁", ignore_terms=plex.underlying_terms("7788")) == set()


def test_load_from_db_without_db_returns_empty():
    class Boom:
        def connect(self):
            raise RuntimeError("no db")

    assert offpool_stocks.load_from_db(Boom(), {"3033"}) == {}
