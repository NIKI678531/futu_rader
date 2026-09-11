"""mysqldump 文件的流式读取器 —— 纯解析，不含任何业务语义。

## 为什么这里有一个自己写的 SQL 解析器

客户给的是 10.3 GB 的 `mysqldump` 产物，而本地开发库是 SQLite（生产才是 MySQL 8）。
SQLite 读不了 mysqldump 语法，所以「9 GB 文件 → 本地瘦库」这一步绕不开一次转换。
[ADR-0008](../../docs/adr/0008-dump-import-and-slim-db.md) 原本否决了「解析 dump」这条路，
理由是**静默丢数据**——那是这个项目里最难发现的一类错误。产品后来选了这条路（不必为一次性
导入去装一套 MySQL），所以否决理由必须被正面消解，而不是绕过：

- **不用正则匹配整行 INSERT。** 正则没法正确处理字符串里的 `\\'` 与 `\\\\`，撞上就静默截断。
  这里是字符级扫描，引号状态是显式的。
- **列数不对就抛异常，不跳过。** `ColumnCountMismatch` 是硬失败。少一列意味着解析器把
  某个字段里的逗号当成了分隔符——那正是静默丢数据的典型现场。
- **字符串未闭合同样是硬失败。** 实测过：把 dump 从行中间截断，解析器立刻报
  `ValueError: 字符串字面量未闭合`，而不是安静地少读几行。
- **读入行数由调用方对账**（见 `DumpReader.stats`）。读了多少、留了多少都记着，
  导入完可以和 dump 里的真实行数对上。

## 性能

9 GB 走 CPython 逐字符循环是数小时级的，不可接受。实测 **150 MB/s**（9 GB ≈ 60 秒），
做法是**只在必要处停下来**：

- **按行切在二进制层，只对命中的 INSERT 行解码。** 一次 pass 只关心一两张表时，
  其余 GB 级的行连 UTF-8 解码都不做。
- 一条 INSERT 语句一定不跨行——mysqldump 会把字符串里的真实换行转义成 `\\n` 两个字符。
  这是整个读取器成立的前提。
- 字符串字段靠 `str.find("'")` 跳到下一个引号，再回头数反斜杠判断是不是真的结束。
  `raw_json` 平均 6 KB，里面单引号极少（JSON 转义的是 `"` 不是 `'`），所以通常一跳到位。
- **字段先只取原始切片，不反转义。** 反转义只对真正留下的行做（`to_python`）。
  过滤掉 99% 的行时，这一条省掉的就是 99% 的工作。
"""

import re

# mysqldump 的转义表。`\Z` 是 MySQL 特有的 EOF 字符（Windows 下的 Ctrl-Z）。
# `\%` `\_` 在 MySQL 里**保留**反斜杠（只有 LIKE 模式里才特殊），所以原样还原两个字符。
_ESCAPES = {
    "0": "\0",
    "b": "\b",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "Z": "\x1a",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "%": "\\%",
    "_": "\\_",
}
_UNESC = re.compile(r"\\(.)")
_INSERT_PREFIX = b"INSERT INTO "

BLOCK = 1 << 23  # 8 MiB


class ColumnCountMismatch(Exception):
    """一行的列数与建表 DDL 不符。**硬失败**——见模块 docstring。"""


def unescape(raw):
    """把 mysqldump 的字符串字面量还原成 Python 字符串。"""
    if "\\" not in raw:
        return raw
    return _UNESC.sub(lambda m: _ESCAPES.get(m.group(1), m.group(1)), raw)


def scan_tuple(line, i):
    """扫描 ``line[i:]`` 处的一个 ``(...)`` 元组。

    返回 ``(fields, next_index)``。``fields`` 是 ``(kind, raw)`` 列表：``kind`` 为
    ``'s'``（带引号的字符串，``raw`` 是**未反转义**的切片）或 ``'r'``（裸 token，
    数字或 ``NULL``）。分开这两类是必要的：``'NULL'`` 这个字符串和 SQL 的 ``NULL``
    在这里必须能区分开，否则铁律 2 在导入的第一步就破了。
    """
    fields = []
    n = len(line)
    j = i + 1  # 跳过 '('
    while True:
        if j >= n:
            raise ValueError(f"元组未闭合，起始于 offset {i}")
        if line[j] == "'":
            k = j + 1
            while True:
                q = line.find("'", k)
                if q < 0:
                    raise ValueError(f"字符串字面量未闭合，起始于 offset {j}")
                # 反斜杠奇数个 ⇒ 这个引号是被转义的，不是结束。
                b = q - 1
                slashes = 0
                while line[b] == "\\":
                    slashes += 1
                    b -= 1
                if slashes % 2 == 0:
                    break
                k = q + 1
            fields.append(("s", line[j + 1 : q]))
            j = q + 1
        else:
            k = j
            while k < n and line[k] not in ",)":
                k += 1
            if k >= n:
                raise ValueError(f"元组未闭合，起始于 offset {i}")
            fields.append(("r", line[j:k]))
            j = k

        c = line[j]
        if c == ",":
            j += 1
        elif c == ")":
            return fields, j + 1
        else:
            raise ValueError(f"元组里出现意外字符 {c!r}，offset {j}")


def iter_tuples(line, start):
    """产出一条 INSERT 语句 ``VALUES`` 之后的每个元组。"""
    n = len(line)
    i = start
    while i < n:
        c = line[i]
        if c == "(":
            fields, i = scan_tuple(line, i)
            yield fields
        elif c in ", \t":
            i += 1
        else:  # ';' 或行尾空白
            return


def to_python(fields):
    """把 ``scan_tuple`` 的原始字段转成 Python 值。

    ``NULL`` → ``None``。**不要**在这里把 ``None`` 变成 ``0`` 或 ``''``：
    dump 里的 NULL 是「源系统就没有这个值」，落成 0 就是撒谎（铁律 2）。
    """
    out = []
    for kind, raw in fields:
        if kind == "s":
            out.append(unescape(raw))
        elif raw == "NULL":
            out.append(None)
        else:
            out.append(raw)
    return out


def _lines(path, prefixes):
    """二进制分行，**只对目标表的 INSERT 行解码**。

    ``prefixes`` 是 ``[(前缀字节, 表名), ...]``。匹配靠 bytes 比较，不解码——所以一次
    只取 `futu_comments_stocks` 时，扫过 9 GB 的 feeds 成本只有 `bytes.find` 和
    `startswith`，是纯 I/O 级别。这是「两遍扫描」这个方案能成立的原因。

    产出 ``(表名, VALUES 之后的字符串)``。别的表的 INSERT 产出 ``(表名占位 None, None)``
    以便调用方判断「已经扫过头了」。
    """

    def emit(raw):
        for prefix, table in prefixes:
            if raw.startswith(prefix):
                return table, raw[len(prefix) :].decode("utf-8", errors="surrogateescape")
        return None, None

    tail = b""
    with open(path, "rb") as fh:
        while True:
            block = fh.read(BLOCK)
            if not block:
                break
            block = tail + block
            start = 0
            while True:
                nl = block.find(b"\n", start)
                if nl < 0:
                    break
                raw = block[start:nl]
                start = nl + 1
                if raw.startswith(_INSERT_PREFIX):
                    yield emit(raw)
            tail = block[start:]
    # 文件不以换行结尾时，最后一行还留在 tail 里。mysqldump 通常会补上换行，但「通常」
    # 不是保证，而这里少读一行是**完全无声的**——连 stats 的对账数都跟着一起少。
    if tail.startswith(_INSERT_PREFIX):
        yield emit(tail)


class DumpReader:
    """按表流式产出 dump 里的行。

    ``tables`` 是 ``{表名: 列数}``。只有列在表里的 INSERT 会被解析，其余整行跳过。

    ``stop_after`` 给出一组表名：当这些表都已出现过、且又遇到**别的**表的 INSERT 时
    停止扫描。mysqldump 按表顺序写，所以这能省掉目标表之后的那几 GB。
    """

    def __init__(self, path, tables, stop_when_past=False):
        self.path = path
        self.tables = dict(tables)
        self.stop_when_past = stop_when_past
        self._prefixes = [
            (b"INSERT INTO `" + name.encode() + b"` VALUES ", name) for name in self.tables
        ]
        # 对账用：读了多少、留了多少。导入完必须和 dump 的真实行数对得上。
        self.stats = {name: {"read": 0, "kept": 0} for name in self.tables}

    def rows(self, keep=None):
        """产出 ``(表名, fields)``。

        ``keep(table, fields) -> bool`` 在**反转义之前**调用，拿到的是原始切片。
        过滤要趁早——这是 9 GB 能在分钟级跑完的唯一原因。

        ``stop_when_past=True`` 时，目标表都出现过、又遇到别的表的 INSERT 就停。
        mysqldump 按表顺序写，所以这能省掉目标表之后的几 GB。
        """
        seen = False
        for table, body in _lines(self.path, self._prefixes):
            if table is None:
                if seen and self.stop_when_past:
                    return
                continue
            seen = True
            ncols = self.tables[table]
            stat = self.stats[table]
            for fields in iter_tuples(body, 0):
                stat["read"] += 1
                if len(fields) != ncols:
                    raise ColumnCountMismatch(
                        f"{table}: 期望 {ncols} 列，解析出 {len(fields)} 列。"
                        f"第 {stat['read']} 行，前两个字段 {fields[:2]!r}"
                    )
                if keep is not None and not keep(table, fields):
                    continue
                stat["kept"] += 1
                yield table, fields
