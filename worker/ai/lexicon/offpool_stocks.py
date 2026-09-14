"""个股词表 —— 产品池之外的标的在评论区里的叫法。

## 只用于识别「评论说的是哪只个股」，不用于判态度

`ai/prefilter.py` 第 5 条规则要判「这条评论只在聊个股、没有提到这只 ETF」。前半句靠这份
词表：`$00700.HK$` 标签、`騰訊`／`腾讯`／`Tencent` 这类俗称。后半句靠 `product_aliases`。
两半都成立才剔除；任何一半不成立都放行交给模型。

## 两个来源

1. **静态俗称**（本文件）：港股／美股社区里常见的个股叫法。这是人工维护的，改了要改 VERSION。
2. **瘦库 `src_stocks`**（`load_from_db`）：dump 里 323 只标的中不在产品池的 203 只，
   取 `ticker` / `name_zh` / `name_en`。运行时合并进来 —— 这一份不需要人维护，随库变。

## 单一股票杠反产品的例外

7788（英偉達兩倍）的讨论区里出现「英偉達」不是跑题，那是标的股。`ProductLexicon.underlying_terms(code)`
给出这些叫法，预过滤在检查该产品时把它们从个股集合里去掉。
"""

import re

from sqlalchemy import select

VERSION = "offpool-v1"

# 键＝规范名（用于报告里分组），值＝所有写法。**不含**产品池里任何 ETF 的叫法。
_STATIC = {
    "騰訊": ["騰訊", "腾讯", "Tencent", "tencent", "企鵝", "企鹅", "00700", "0700.HK", "700.HK"],
    "阿里巴巴": ["阿里巴巴", "阿里", "Alibaba", "BABA", "9988", "09988"],
    "美團": ["美團", "美团", "Meituan", "3690", "03690"],
    "小米": ["小米", "Xiaomi", "1810", "01810", "雷軍", "雷军"],
    "匯豐": ["匯豐", "汇丰", "HSBC", "大笨象", "00005", "0005.HK"],
    "比亞迪": ["比亞迪", "比亚迪", "BYD", "1211", "01211"],
    "京東": ["京東", "京东", "JD", "9618", "09618"],
    "網易": ["網易", "网易", "NetEase", "9999", "09999"],
    "快手": ["快手", "Kuaishou", "1024", "01024"],
    "百度": ["百度", "Baidu", "9888", "09888"],
    "中芯": ["中芯", "中芯國際", "中芯国际", "SMIC", "0981", "00981"],
    "建行": ["建行", "建設銀行", "建设银行", "0939", "00939"],
    "工行": ["工行", "工商銀行", "工商银行", "1398", "01398"],
    "中移動": ["中移動", "中移动", "中國移動", "中国移动", "0941", "00941"],
    "友邦": ["友邦", "AIA", "1299", "01299"],
    "港交所": ["港交所", "HKEX", "0388", "00388"],
    "藥明生物": ["藥明生物", "药明生物", "2269", "02269"],
    "藥明康德": ["藥明康德", "药明康德", "2359", "02359"],
    "聯想": ["聯想", "联想", "Lenovo", "0992", "00992"],
    "吉利": ["吉利", "Geely", "0175", "00175"],
    "蔚來": ["蔚來", "蔚来", "NIO", "9866", "09866"],
    "小鵬": ["小鵬", "小鹏", "XPeng", "9868", "09868"],
    "理想": ["理想汽車", "理想汽车", "Li Auto", "2015", "02015"],
    "商湯": ["商湯", "商汤", "SenseTime", "0020", "00020"],
    "泡泡瑪特": ["泡泡瑪特", "泡泡玛特", "Pop Mart", "9992", "09992"],
    "舜宇": ["舜宇", "2382", "02382"],
    "中海油": ["中海油", "0883", "00883"],
    "中石油": ["中石油", "0857", "00857"],
    "中石化": ["中石化", "0386", "00386"],
    "長和": ["長和", "长和", "0001", "00001"],
    "新鴻基": ["新鴻基", "新鸿基", "0016", "00016"],
    "領展": ["領展", "领展", "0823", "00823"],
    "港鐵": ["港鐵", "港铁", "0066", "00066"],
    "中電": ["中電", "中电", "0002", "00002"],
    "煤氣": ["煤氣", "煤气", "0003", "00003"],
    "蒙牛": ["蒙牛", "2319", "02319"],
    "安踏": ["安踏", "2020", "02020"],
    "李寧": ["李寧", "李宁", "2331", "02331"],
    "周大福": ["周大福", "1929", "01929"],
    "招行": ["招行", "招商銀行", "招商银行", "3968", "03968"],
    "平安": ["中國平安", "中国平安", "平保", "2318", "02318"],
    "國壽": ["國壽", "国寿", "中國人壽", "中国人寿", "2628", "02628"],
    "海底撈": ["海底撈", "海底捞", "6862", "06862"],
    "農夫山泉": ["農夫山泉", "农夫山泉", "9633", "09633"],
    "攜程": ["攜程", "携程", "Trip.com", "9961", "09961"],
    "嗶哩嗶哩": ["嗶哩嗶哩", "哔哩哔哩", "B站", "Bilibili", "9626", "09626"],
    "老鋪黃金": ["老鋪黃金", "老铺黄金", "6181", "06181"],
    "蜜雪": ["蜜雪", "蜜雪冰城", "2097", "02097"],
    "地平線": ["地平線", "地平线", "9660", "09660"],
    "寒武紀": ["寒武紀", "寒武纪"],
    "紫金": ["紫金", "紫金礦業", "紫金矿业", "2899", "02899"],
    "中興": ["中興", "中兴", "ZTE", "0763", "00763"],
    "華虹": ["華虹", "华虹", "1347", "01347"],
    "金蝶": ["金蝶", "0268", "00268"],
    "同程": ["同程", "0780", "00780"],
    "新東方": ["新東方", "新东方", "9901", "09901"],
    "萬科": ["萬科", "万科", "2202", "02202"],
    "恒大": ["恒大", "3333", "03333"],
    "碧桂園": ["碧桂園", "碧桂园", "2007", "02007"],
    # 美股（社区里常以美股讨论带动 ETF 讨论）
    "蘋果": ["蘋果", "苹果", "Apple", "AAPL"],
    "微軟": ["微軟", "微软", "Microsoft", "MSFT"],
    "亞馬遜": ["亞馬遜", "亚马逊", "Amazon", "AMZN"],
    "Meta": ["Meta", "META", "Facebook", "臉書", "脸书"],
    "谷歌": ["谷歌", "Google", "GOOGL", "GOOG", "Alphabet"],
    "台積電": ["台積電", "台积电", "TSMC", "TSM", "2330"],
    "AMD": ["AMD", "超微半導體", "超微半导体"],
    "英特爾": ["英特爾", "英特尔", "Intel", "INTC"],
    "博通": ["博通", "Broadcom", "AVGO"],
    "Palantir": ["Palantir", "PLTR"],
    "Netflix": ["Netflix", "NFLX", "奈飛", "奈飞"],
    "超微電腦": ["超微電腦", "超微电脑", "SMCI", "Super Micro"],
    "甲骨文": ["甲骨文", "Oracle", "ORCL"],
    "AppLovin": ["AppLovin"],
    "Robinhood": ["Robinhood", "HOOD"],
    "Circle": ["Circle", "CRCL"],
    "拼多多": ["拼多多", "PDD", "Temu"],
}


class StockLexicon:
    """个股词表。`stocks_in(text)` 返回文中出现的个股规范名集合。"""

    def __init__(self, extra=None):
        # {写法(lower): 规范名}
        self._terms = {}
        for canon, forms in _STATIC.items():
            for f in forms:
                self._terms[f.lower()] = canon
        for canon, forms in (extra or {}).items():
            for f in forms:
                self._terms.setdefault(f.lower(), canon)
        # 三类写法三种匹配：纯数字代码按词边界；拉丁字母词按单词边界（`Meta` 不能命中
        # `metaverse`）；中文词按子串。
        self._codes = {t: c for t, c in self._terms.items() if re.fullmatch(r"\d{4,5}", t)}
        self._tickers = {t: c for t, c in self._terms.items() if re.fullmatch(r"\d{3,5}\.hk", t)}
        latin = [(t, c) for t, c in self._terms.items()
                 if t not in self._codes and t not in self._tickers and re.fullmatch(r"[a-z0-9 .\-]+", t)]
        self._latin = [(re.compile(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])"), c) for t, c in latin]
        self._cjk = [(t, c) for t, c in self._terms.items()
                     if t not in self._codes and t not in self._tickers and not re.fullmatch(r"[a-z0-9 .\-]+", t)]
        self._cjk.sort(key=lambda tc: -len(tc[0]))
        self._code_re = re.compile(r"(?<![0-9.])(0?\d{4})(\.hk)?(?![0-9])")

    def stocks_in(self, text, ignore_terms=()):
        """文中出现的个股规范名。`ignore_terms` 是要当作「本产品标的」跳过的写法。"""
        if not text:
            return set()
        low = text.lower()
        ignore = {t.lower() for t in ignore_terms}
        found = set()
        for term, canon in self._cjk:
            if term not in ignore and term in low:
                found.add(canon)
        for rx, canon in self._latin:
            if rx.pattern not in ignore and rx.search(low):
                found.add(canon)
        for num, suffix in self._code_re.findall(low):
            if suffix:
                canon = self._tickers.get(f"{num}{suffix}") or self._tickers.get(f"{num.zfill(5)}{suffix}")
                if canon:
                    found.add(canon)
                    continue
            # 裸 4 位数只在带前导 0（`00700`）或不像年份（`2020`）时才当代码。
            bare = num.lstrip("0") or "0"
            if len(num) == 5 or not (1990 <= int(bare) <= 2040):
                canon = self._codes.get(num) or self._codes.get(num.zfill(5)) or self._codes.get(bare)
                if canon and bare not in ignore:
                    found.add(canon)
        return found

    def size(self):
        return len(self._terms)


def load_from_db(engine, pool_codes):
    """从 `src_stocks` 取不在产品池的标的 → `{规范名: [写法...]}`。

    dump 有 323 只标的，其中 203 只不在池里；它们的 `ticker`（`00700.HK`）、`name_zh`、
    `name_en` 都是评论里可能出现的写法。库不在时返回空 dict，静态表照常工作。
    """
    from radar_db.schema import src_stocks  # 延迟导入：本模块在没有库的环境里也要能 import

    out = {}
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                select(src_stocks.c.ticker, src_stocks.c.name_zh, src_stocks.c.name_en,
                       src_stocks.c.instrument_type)
            ).all()
    except Exception:  # noqa: BLE001  表不存在／库不在：静态表兜底
        return out
    for ticker, zh, en, _itype in rows:
        head = ticker.split(".", 1)[0]
        code = head.lstrip("0") or "0"
        if code in pool_codes:
            continue
        canon = zh or en or ticker
        forms = [ticker, head]
        if zh:
            forms.append(zh)
        if en and len(en) >= 3:
            forms.append(en)
        out[canon] = forms
    return out
