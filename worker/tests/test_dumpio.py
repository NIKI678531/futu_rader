"""`jobs/dumpio.py` —— mysqldump 流式读取器。

这个解析器是 ADR-0008 里被**否决过**的方案（理由：解析 dump 会静默丢数据），后来因为
产品选了「不为一次性导入装一套 MySQL」而复活。复活的条件是原否决理由被正面消解，
所以这份测试的大半都在证明**它宁可炸也不会安静地少读**：

- 引号／反斜杠的每一种组合都不会把一行截断；
- 列数不对是异常，不是跳过；
- 字符串未闭合是异常，不是读到哪算哪；
- 块边界、文件末尾这些「少一行不会有人发现」的地方，一行都不能少。

编造的 dump 片段够了 —— 语法就那么点，而真 dump 是 10.3 GB、含 PII、不进 git。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs import dumpio  # noqa: E402
from jobs.dumpio import (  # noqa: E402
    ColumnCountMismatch,
    DumpReader,
    iter_tuples,
    scan_tuple,
    to_python,
    unescape,
)


def _write(tmp_path, text, newline_at_end=True):
    p = tmp_path / "dump.sql"
    p.write_text(text + ("\n" if newline_at_end else ""), encoding="utf-8")
    return str(p)


def _insert(table, *tuples):
    return f"INSERT INTO `{table}` VALUES " + ",".join(tuples) + ";"


# ── 反转义 ─────────────────────────────────────────────────────────────


class TestUnescape:
    def test_the_usual_suspects(self):
        assert unescape(r"a\nb") == "a\nb"
        assert unescape(r"a\tb") == "a\tb"
        assert unescape(r"a\rb") == "a\rb"
        assert unescape(r"a\'b") == "a'b"
        assert unescape(r"a\\b") == "a\\b"
        assert unescape(r'a\"b') == 'a"b'

    def test_mysql_only_escapes(self):
        assert unescape(r"a\0b") == "a\0b"
        assert unescape(r"a\Zb") == "a\x1ab"  # \Z 是 MySQL 的 EOF 字符

    def test_percent_and_underscore_keep_their_backslash(self):
        r"""`\%` `\_` 只在 LIKE 模式里特殊，普通字符串里是**两个字符**。

        按常规转义处理会把 `100\%` 变成 `100%` —— 一个看不出来的内容改写。
        """
        assert unescape(r"100\%") == r"100\%"
        assert unescape(r"a\_b") == r"a\_b"

    def test_no_backslash_is_returned_untouched(self):
        s = "普通中文正文，没有任何转义"
        assert unescape(s) is s


# ── 元组扫描 ───────────────────────────────────────────────────────────


class TestScanTuple:
    def test_plain_row(self):
        fields, nxt = scan_tuple("(1,'abc',NULL)", 0)
        assert fields == [("r", "1"), ("s", "abc"), ("r", "NULL")]
        assert nxt == len("(1,'abc',NULL)")

    def test_quoted_null_is_not_sql_null(self):
        """`'NULL'`（字符串）和 `NULL`（真的没有）必须分得开。

        混成一个，铁律 2 在导入的第一步就破了：一个昵称叫 NULL 的用户会变成缺失值，
        或者反过来，缺失值变成字符串 'NULL' 被当成有效数据算进指标。
        """
        fields, _ = scan_tuple("('NULL',NULL)", 0)
        assert fields == [("s", "NULL"), ("r", "NULL")]
        assert to_python(fields) == ["NULL", None]

    def test_separators_inside_strings_are_not_separators(self):
        """逗号和右括号出现在字符串里 —— 正则方案就是死在这里。"""
        fields, _ = scan_tuple("('a,b)c',7)", 0)
        assert to_python(fields) == ["a,b)c", "7"]

    def test_escaped_quote(self):
        fields, _ = scan_tuple(r"('a\'b',1)", 0)
        assert to_python(fields) == ["a'b", "1"]

    def test_string_ending_in_an_escaped_backslash_closes_properly(self):
        r"""`'a\\'` 的末尾引号**是**结束符：前面两个反斜杠是一个转义过的反斜杠。

        数错反斜杠奇偶就会把这一行和下一行粘起来，然后列数对不上 —— 幸好那是硬失败。
        """
        fields, nxt = scan_tuple(r"('a\\',1)", 0)
        assert to_python(fields) == ["a\\", "1"]
        assert nxt == len(r"('a\\',1)")

    def test_three_backslashes_then_a_quote_is_still_escaped(self):
        fields, _ = scan_tuple(r"('a\\\'b',1)", 0)
        assert to_python(fields) == ["a\\'b", "1"]

    def test_json_payload_survives(self):
        """`raw_json` 是最长也最脏的一列。JSON 转义的是 `"` 不是 `'`。"""
        raw = r"""('{\"k\": \"v, x)\", \"n\": null}',1)"""
        fields, _ = scan_tuple(raw, 0)
        assert to_python(fields)[0] == '{"k": "v, x)", "n": null}'

    def test_unterminated_string_is_a_hard_failure(self):
        """截断的行必须炸，不能读到哪算哪 —— 这是 ADR-0008 原否决理由的正面消解。"""
        with pytest.raises(ValueError, match="字符串字面量未闭合"):
            scan_tuple("('abc", 0)

    def test_unterminated_tuple_is_a_hard_failure(self):
        with pytest.raises(ValueError, match="元组未闭合"):
            scan_tuple("(1,2", 0)


class TestToPython:
    def test_numbers_stay_strings(self):
        """解析器不做类型转换 —— 那是调用方按 DDL 决定的事。"""
        assert to_python([("r", "42"), ("r", "-1.5")]) == ["42", "-1.5"]

    def test_null_becomes_none_not_zero(self):
        """dump 里的 NULL 是「源系统就没有这个值」。落成 0 就是撒谎（铁律 2）。"""
        assert to_python([("r", "NULL")]) == [None]


def test_iter_tuples_walks_a_multi_row_insert():
    """mysqldump 一条 INSERT 里塞几千行，这是常态不是例外。"""
    body = "(1,'a'),(2,'b'),(3,'c');"
    assert [to_python(f) for f in iter_tuples(body, 0)] == [
        ["1", "a"], ["2", "b"], ["3", "c"],
    ]


# ── 读取器 ─────────────────────────────────────────────────────────────


class TestDumpReader:
    def test_reads_only_the_requested_tables(self, tmp_path):
        path = _write(
            tmp_path,
            "\n".join(
                [
                    "-- 注释行",
                    "CREATE TABLE `futu_feeds` (...);",
                    _insert("futu_feeds", "(1,'a')", "(2,'b')"),
                    _insert("futu_users", "(9,'x','y','z')"),
                ]
            ),
        )
        r = DumpReader(path, {"futu_feeds": 2})
        rows = list(r.rows())
        assert [to_python(f) for _, f in rows] == [["1", "a"], ["2", "b"]]
        assert r.stats == {"futu_feeds": {"read": 2, "kept": 2}}

    def test_keep_filters_before_unescaping(self, tmp_path):
        """`keep` 拿到的是**未反转义**的原始切片 —— 过滤趁早是 9 GB 能分钟级跑完的原因。"""
        path = _write(tmp_path, _insert("t", "(1,'keep')", "(2,'drop')", "(3,'keep')"))
        seen = []

        def keep(table, fields):
            seen.append(fields[1])
            return fields[1] == ("s", "keep")

        r = DumpReader(path, {"t": 2})
        rows = list(r.rows(keep))
        assert [to_python(f)[0] for _, f in rows] == ["1", "3"]
        # 读了 3 行、留了 2 行，两个数都记着 —— 导入完要和 dump 的真实行数对账
        assert r.stats["t"] == {"read": 3, "kept": 2}
        assert len(seen) == 3

    def test_column_count_mismatch_raises_instead_of_skipping(self, tmp_path):
        """少一列意味着解析器把某个字段里的逗号当成了分隔符 —— 静默丢数据的典型现场。"""
        path = _write(tmp_path, _insert("t", "(1,'a')", "(2)"))
        r = DumpReader(path, {"t": 2})
        with pytest.raises(ColumnCountMismatch) as e:
            list(r.rows())
        assert "期望 2 列" in str(e.value)
        assert "第 2 行" in str(e.value)  # 定位到具体哪一行，不是「文件坏了」

    def test_stop_when_past_gives_up_after_the_target_tables(self, tmp_path):
        """mysqldump 按表顺序写，目标表读完就没必要再扫后面几 GB。"""
        path = _write(
            tmp_path,
            "\n".join(
                [
                    _insert("t", "(1,'a')"),
                    _insert("other", "(0)"),
                    _insert("t", "(2,'b')"),  # 真 dump 里不会出现，这里用来证明确实停了
                ]
            ),
        )
        assert len(list(DumpReader(path, {"t": 2}, stop_when_past=True).rows())) == 1
        assert len(list(DumpReader(path, {"t": 2}, stop_when_past=False).rows())) == 2

    def test_a_row_straddling_a_read_block_is_not_lost(self, tmp_path, monkeypatch):
        """行被 8 MiB 的读块从中间切开时靠 tail 拼回来。

        9 GB / 8 MiB ≈ 1100 次块边界，每次都可能落在一行中间。这里把块缩到 7 字节，
        等于把那 1100 次边界全都撞一遍。
        """
        monkeypatch.setattr(dumpio, "BLOCK", 7)
        path = _write(
            tmp_path,
            "\n".join(_insert("t", f"({i},'内容{i}')") for i in range(20)),
        )
        rows = list(DumpReader(path, {"t": 2}).rows())
        assert [to_python(f)[0] for _, f in rows] == [str(i) for i in range(20)]

    def test_the_last_line_survives_a_missing_trailing_newline(self, tmp_path):
        """dump 不以换行结尾时，最后一行不能被丢掉。

        mysqldump 通常会补上换行，但被截断过、或被别的工具重新拼过的文件不保证。
        丢掉它是完全无声的：`stats` 的对账数会跟着一起少，两边永远对得上。
        """
        path = _write(
            tmp_path,
            _insert("t", "(1,'a')") + "\n" + _insert("t", "(2,'b')"),
            newline_at_end=False,
        )
        r = DumpReader(path, {"t": 2})
        assert [to_python(f)[0] for _, f in r.rows()] == ["1", "2"]
        assert r.stats["t"]["read"] == 2

    def test_an_empty_file_is_empty_not_an_error(self, tmp_path):
        path = _write(tmp_path, "", newline_at_end=False)
        r = DumpReader(path, {"t": 2})
        assert list(r.rows()) == []
        assert r.stats["t"] == {"read": 0, "kept": 0}
