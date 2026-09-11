"""原文证据校验 —— 把模型自报的 evidence 在原文里**定位**，定位不到就判未通过。

## 这一层不是可选的

runbook §11.1 要求「evidence 必须是输入原文连续片段」，§9 把这条任务写成
「GPT 返回 span＋**程序验证**」。Gate 0 第一次真实调用就复现了为什么：

    输入：这只ETF点差太大
    模型返回的 evidence：评论指出该ETF点差太大，属于对产品交易成本和交易体验的负面评价。

这是一句**转述**，不是原文。它读起来比原文更像证据 —— 它带了结论、带了归类、语气笃定。
如果直接落库，证据侧栏会显示一段原文里根本不存在的话，而界面上它和真引文长得一模一样。
「每个 AI 结论可回到原文」（runbook §19）会在肉眼检查下全部通过，因为没有人会去原文里
搜这段话。**所以必须由程序搜。**

## 判定顺序

1. 精确子串 —— 绝大多数合规返回走这条。
2. 归一化后子串（空白、全半角标点）—— 模型偶尔会把「，」写成「,」，这不算编造。
3. 都不中 ⇒ `found=False`。调用方据此把整条标注打上 `needs_review`，**不是丢弃**：
   态度判断本身可能是对的，只是证据不合格，交人工复核比直接扔掉更省标注预算。

刻意**不做**模糊匹配（编辑距离／最长公共子串）。相似度 0.85 的「证据」就是转述，
放过它等于放弃这一层的全部意义 —— 而它的失败是静默的，正是铁律 2 要防的形态。
"""

import hashlib
import re
import unicodedata

# 全角→半角标点。模型在中英混排时经常换掉这些，语义没变，不该判成编造。
_PUNCT_MAP = str.maketrans("，。！？；：（）［］【】“”‘’、", ",.!?;:()[][]\"\"'',")
_WS = re.compile(r"\s+")


def normalize(text):
    """定位用的归一化形态。NFKC 统一全半角字母数字，再压平空白与标点。

    归一化**只用于找位置**，落库的 `quote_text` 永远是原文里的那一段（见 `locate`），
    不是归一化后的串 —— 否则证据侧栏显示的就不是用户真正写的字。
    """
    if text is None:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = t.translate(_PUNCT_MAP)
    return _WS.sub("", t).lower()


def quote_hash(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class Located:
    """一次定位结果。`found=False` 时 start/end 为 None —— 不要用 -1 或 0 占位，
    0 是一个**合法的起始偏移**，用它表示「没找到」会让证据指向正文开头。"""

    __slots__ = ("found", "start", "end", "quote", "method")

    def __init__(self, found, start=None, end=None, quote=None, method=None):
        self.found, self.start, self.end, self.quote, self.method = (
            found, start, end, quote, method,
        )

    def __repr__(self):
        return (
            f"Located(found={self.found}, start={self.start}, end={self.end}, "
            f"quote={self.quote!r}, method={self.method!r})"
        )


def locate(evidence, source):
    """在 `source` 里找 `evidence`。返回的 `quote` 是**原文切片**，不是模型给的串。"""
    if not evidence or not source:
        return Located(False)

    # ① 精确子串。
    i = source.find(evidence)
    if i >= 0:
        return Located(True, i, i + len(evidence), source[i : i + len(evidence)], "exact")

    # ② 归一化后子串。命中后要把归一化坐标映射回原文坐标 —— 归一化会删字符，
    #    直接拿归一化下标去切原文会切错位置。逐字符建一张映射表最笨但不会错。
    norm_src, back = _normalize_with_map(source)
    norm_ev = normalize(evidence)
    if not norm_ev:
        return Located(False)
    j = norm_src.find(norm_ev)
    if j >= 0:
        start = back[j]
        # 结束位置取归一化串最后一个字符在原文里的下标 +1，而不是 back[j+len]：
        # 后者在证据结尾紧跟着被删空白时会把那段空白也圈进引文。
        end = back[j + len(norm_ev) - 1] + 1
        return Located(True, start, end, source[start:end], "normalized")

    return Located(False)


def _normalize_with_map(text):
    """归一化，同时记录归一化串每个字符来自原文的哪个下标。

    逐字符 NFKC 而不是整串 NFKC：整串归一化后长度会变（一个全角字符可能展开成两个），
    下标就对不回去了。逐字符做的话，展开出的每一个字符都记同一个源下标，映射仍然成立。
    """
    out, back = [], []
    for i, ch in enumerate(text):
        c = unicodedata.normalize("NFKC", ch).translate(_PUNCT_MAP)
        if _WS.fullmatch(c) or not c:
            continue
        c = c.lower()
        out.append(c)
        back.extend([i] * len(c))
    return "".join(out), back


def verify(evidence, source):
    """给标注作业用的薄封装：返回 `(ok, Located)`。

    `evidence` 为空也算未通过 —— 一条没有证据的结论在证据侧栏里是空白，
    而空白会被读成「这条没问题」。
    """
    loc = locate(evidence, source)
    return loc.found, loc
