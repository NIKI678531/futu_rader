"""`ai/evidence.py` —— 证据必须能在原文里找到。

这份测试的核心不是「找得到的能找到」，而是**转述必须判不通过**。Gate 0 的首次真实调用
返回的就是一句转述（见 `ai/evidence.py` 模块头），它通顺、笃定、像证据，只是原文里没有。
放过它，界面上就会出现一段用户从没说过的话，且没有任何断言会变红。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.evidence import locate, normalize, quote_hash, verify  # noqa: E402


class TestExactMatch:
    def test_finds_contiguous_span(self):
        src = "跌下来正好继续加这只"
        loc = locate("继续加这只", src)
        assert loc.found and loc.method == "exact"
        assert src[loc.start : loc.end] == "继续加这只"

    def test_whole_text_is_a_valid_span(self):
        src = "这只ETF点差太大"
        loc = locate(src, src)
        assert loc.found and (loc.start, loc.end) == (0, len(src))

    def test_span_at_offset_zero_is_found_not_confused_with_missing(self):
        # start=0 是合法偏移。用 0 或 -1 表示「没找到」会让证据指向正文开头。
        loc = locate("跌下来", "跌下来正好继续加这只")
        assert loc.found and loc.start == 0
        assert locate("原文里没有这句", "跌下来正好继续加这只").start is None


class TestParaphraseIsRejected:
    def test_the_actual_gate0_response_is_rejected(self):
        # 逐字来自 2026-09-11 对 gpt-5.6-luna 的首次真实调用。
        src = "这只ETF点差太大"
        para = "评论指出该ETF点差太大，属于对产品交易成本和交易体验的负面评价。"
        assert not locate(para, src).found

    def test_superset_of_the_original_is_rejected(self):
        # 转述常见形态：原文整句被包进一个更长的句子里。子串方向不能反。
        assert not locate("用户说这只ETF点差太大所以不买", "这只ETF点差太大").found

    def test_reordered_words_are_rejected(self):
        assert not locate("点差太大这只ETF", "这只ETF点差太大").found

    def test_near_miss_single_char_is_rejected(self):
        # 不做模糊匹配：相似度再高也是编造。
        assert not locate("这只ETF点差太小", "这只ETF点差太大").found

    def test_empty_evidence_is_not_a_pass(self):
        # 空证据在证据侧栏里是一片空白，会被读成「这条没问题」。
        assert not locate("", "这只ETF点差太大").found
        assert not locate(None, "这只ETF点差太大").found


class TestNormalizedMatch:
    def test_fullwidth_comma_swap_is_tolerated(self):
        # 模型把「，」写成「,」不算编造，语义一字未改。
        src = "费率低，流动性也不错"
        loc = locate("费率低,流动性也不错", src)
        assert loc.found and loc.method == "normalized"

    def test_quote_is_sliced_from_source_not_from_model_output(self):
        # 落库的引文必须是**用户真正写的字**，不是模型给的那个变体。
        src = "费率低，流动性也不错"
        loc = locate("费率低,流动性也不错", src)
        assert loc.quote == "费率低，流动性也不错"
        assert "，" in loc.quote

    def test_whitespace_differences_are_tolerated(self):
        src = "点差 太大 了"
        loc = locate("点差太大了", src)
        assert loc.found and src[loc.start : loc.end] == "点差 太大 了"

    def test_offsets_map_back_through_removed_whitespace(self):
        # 归一化会删字符；拿归一化下标直接切原文会整体错位。
        src = "开头无关内容。  点差 太大"
        loc = locate("点差太大", src)
        assert loc.found and src[loc.start : loc.end] == "点差 太大"

    def test_trailing_whitespace_not_swallowed_into_quote(self):
        src = "点差太大    后面还有"
        loc = locate("点差太大", src)
        assert loc.quote == "点差太大"

    def test_fullwidth_digits_match_halfwidth(self):
        src = "费率０．９９％"
        assert locate("费率0.99%", src).found


class TestNormalizeAndHash:
    def test_normalize_strips_whitespace_and_case(self):
        assert normalize("ETF  点差\n太大") == "etf点差太大"

    def test_normalize_handles_none(self):
        assert normalize(None) == ""

    def test_hash_is_stable_and_distinguishes(self):
        assert quote_hash("继续加这只") == quote_hash("继续加这只")
        assert quote_hash("继续加这只") != quote_hash("继续加那只")


class TestVerify:
    def test_returns_tuple_with_location(self):
        ok, loc = verify("继续加这只", "跌下来正好继续加这只")
        assert ok and loc.start == 5

    def test_failure_carries_no_bogus_offsets(self):
        ok, loc = verify("编造的证据", "跌下来正好继续加这只")
        assert not ok and loc.start is None and loc.quote is None
