"""产品别名词表 —— 当前生产产品池在富途评论区里的各种写法。

## 为什么模型需要它

`annotate._build_payload` 原来只把 `{"code": "3033"}` 发给模型。模型要判「这条评论
是不是在评价这只 ETF」，却不知道 3033 叫恒生科技指數ETF、社区叫它「恒科」「南方恒科」。
它只能猜，或者把每条提到「恒科」的评论都判成 `needs_context`（Gate 2 首轮 40% 就是这样来的
一部分）。名称和别名是 runbook §11.4 明确允许外发的三样产品信息之一。

## 为什么预过滤需要它

`ai/prefilter.py` 的第 5 条规则「只讨论个股、没有提到这只 ETF」要判「有没有提到这只 ETF」，
判据就是这份词表：代码写法、简繁全名、俗称、以及该产品所属**族**的通用叫法（「恒科」既指
3033 也指 3032/3067/2837/3088，它们都是恒生科技 ETF）。

## 两层别名

- `aliases`：**唯一**指向这只产品的写法（代码形式、全名、带发行商的俗称）。
  `test_lexicon.py` 断言它们跨产品互不冲突 —— 「南方恒科」不能同时指向两只产品。
- `family`：同族产品共享的叫法（「恒科」「恒指」「納指」「A50」）。评论里出现它们，说明作者
  在说这一族里的**某一只**，很可能就是当前讨论区这只；这足以让预过滤放行、交给模型判，
  但不足以证明作者说的一定是这只而不是隔壁那只 —— 所以它们不进 `aliases`。

## 简繁

不引 OpenCC 之类的依赖：产品名字里出现的繁体字是有限集合，一张字表就够，并且这张表
**只在这里用**（评论正文的简繁由模型处理，不在程序里转）。
"""

import json
import re
import unicodedata

from radar_db.product_catalog import load_products as load_catalog_products

VERSION = "aliases-v3"

# 名称里出现过的繁体字 → 简体，成对列出；两张转换表都从这一份生成，不会长短不齐。
_PAIRS = (
    "數数 產产 槓杠 桿杆 備备 兌兑 認认 購购 權权 動动 紅红 時时 東东 選选 韓韩 現现 國国 業业 "
    "華华 滬沪 證证 創创 標标 經经 銀银 題题 陽阳 納纳 達达 頭头 偉伟 電电 亞亚 貨货 幣币 場场 "
    "債债 黃黄 億亿 與与 兩两 幾几 隻只 個个 對对 於于 機机 為为 變变 體体 邊边 積积 極极 "
    "價价 週周 藥药 織织 續续 買买 賣卖 錢钱 開开 關关 長长 節节 號号 點点 齊齐"
).split()
_T2S = str.maketrans("".join(p[0] for p in _PAIRS), "".join(p[1] for p in _PAIRS))
_S2T = str.maketrans("".join(p[1] for p in _PAIRS), "".join(p[0] for p in _PAIRS))


def to_simplified(s):
    return s.translate(_T2S)


def to_traditional(s):
    return s.translate(_S2T)


# ── 人工维护的社区俗称 ────────────────────────────────────────────────
#
# 来源：富途评论区实际写法（Gate 2 影子样本）＋ 产品名的口语缩写。**唯一**指向一只产品。
# 同族共享的叫法放 _FAMILY，不放这里。改这里要改 VERSION。

_NICKNAMES = {
    # 港股
    "3033": ["南方恒科", "南方恒生科技", "南方恒科ETF", "恒生科技ETF南方", "3033恒科"],
    "3037": ["南方恒指", "南方恒指ETF", "南方恒生指数"],
    "2802": ["國指備兌", "国指备兑", "南方國指備兌", "南方国指备兑", "國企備兌", "国企备兑"],
    "3469": ["港股通紅利", "港股通红利", "南方紅利", "南方红利", "恒生港股通高股息"],
    "3174": ["恒生生科", "南方生科", "生物科技ETF", "恒生生物科技"],
    "3442": ["港美科技", "恒生港美科技"],
    "3441": ["東西股票", "东西股票", "富時東西", "富时东西"],
    "3443": ["富時香港股票", "富时香港股票"],
    "3431": ["香港韓國科技", "香港韩国科技", "韓國科技ETF", "韩国科技ETF"],
    "3432": ["MSCI港股通", "港股通精選", "港股通精选"],
    "3535": ["香港日本現金流", "香港日本现金流", "野村富時"],
    "7226": ["南方兩倍恒科", "南方两倍恒科", "恒科兩倍", "恒科两倍", "恒科2x", "恒科2X",
             "恒科二倍", "兩倍恒科", "两倍恒科", "恒科槓桿", "恒科杠杆", "恒科牛"],
    "7552": ["南方恒科反向", "恒科反向", "恒科-2x", "恒科-2X", "恒科兩倍反向", "恒科两倍反向",
             "恒科熊", "恒科淡倉", "恒科淡仓", "反向恒科"],
    "7200": ["南方兩倍恒指", "南方两倍恒指", "恒指兩倍", "恒指两倍", "恒指2x", "恒指2X",
             "兩倍恒指", "两倍恒指", "恒指槓桿", "恒指杠杆", "恒指牛"],
    "7300": ["恒指反向", "恒指-1x", "恒指-1X", "恒指一倍反向", "反向恒指"],
    "7500": ["恒指兩倍反向", "恒指两倍反向", "恒指-2x", "恒指-2X", "恒指熊"],
    "7288": ["國企兩倍", "国企两倍", "國指兩倍", "国指两倍", "國指2x", "国指2x", "H股兩倍", "H股两倍"],
    "7588": ["國企反向", "国企反向", "國指反向", "国指反向", "國指-2x", "国指-2x"],
    # A 股
    "2822": ["南方A50", "富時A50", "富时A50", "南方富時中國A50", "南方富时中国A50", "A50 ETF南方"],
    "3003": ["MSCI A50", "MSCI中國A50", "MSCI中国A50", "A50互聯互通", "A50互联互通"],
    "3133": ["南方滬深300", "南方沪深300", "華泰柏瑞滬深300", "华泰柏瑞沪深300"],
    "3101": ["中證A500", "中证A500", "A500 ETF", "華泰柏瑞A500", "华泰柏瑞A500"],
    "3109": ["科創50", "科创50", "科創板50", "科创板50", "南方科創", "南方科创"],
    "3147": ["創業板ETF", "创业板ETF", "南方創業板", "南方创业板", "創業板指數ETF", "创业板指数ETF"],
    "3005": ["中證500", "中证500", "南方中證500", "南方中证500"],
    "3167": ["標普新經濟", "标普新经济", "中國新經濟行業", "中国新经济行业"],
    "3193": ["5G通信", "5G ETF", "銀華5G", "银华5G"],
    "3134": ["太陽能ETF", "太阳能ETF", "光伏ETF", "太陽能產業", "太阳能产业"],
    "7233": ["滬深300兩倍", "沪深300两倍", "滬深兩倍", "沪深两倍", "300兩倍", "300两倍"],
    # 美股
    "3034": ["南方納指", "南方纳指", "南方納斯達克", "南方纳斯达克", "納指100 ETF南方", "納指ETF南方"],
    "3454": ["七巨頭", "七巨头", "美股七巨頭", "美股七巨头", "美股七雄", "Mag7", "MAG7", "七姐妹"],
    "7266": ["南方兩倍納指", "南方两倍纳指", "納指兩倍", "纳指两倍", "納指2x", "纳指2x", "納指2X",
             "兩倍納指", "两倍纳指", "納指槓桿", "纳指杠杆", "納指牛", "纳指牛"],
    "7568": ["納指反向", "纳指反向", "納指-2x", "纳指-2x", "納指兩倍反向", "纳指两倍反向",
             "納指熊", "纳指熊", "反向納指", "反向纳指"],
    # 单一股票杠反
    "7709": ["海力士兩倍", "海力士两倍", "海力士2x", "SK海力士兩倍", "Hynix 2x"],
    "7788": ["英偉達兩倍", "英伟达两倍", "英偉達2x", "英伟达2x", "NVDA 2x", "NVDA兩倍", "NVDA两倍",
             "輝達兩倍", "辉达两倍", "兩倍英偉達", "两倍英伟达"],
    "7388": ["英偉達反向", "英伟达反向", "英偉達-2x", "英伟达-2x", "NVDA -2x", "NVDA反向",
             "反向英偉達", "反向英伟达", "輝達反向", "辉达反向"],
    "7766": ["特斯拉兩倍", "特斯拉两倍", "特斯拉2x", "TSLA 2x", "TSLA兩倍", "TSLA两倍", "兩倍特斯拉", "两倍特斯拉"],
    "7366": ["特斯拉反向", "特斯拉-2x", "TSLA -2x", "TSLA反向", "反向特斯拉"],
    "7747": ["三星兩倍", "三星两倍", "三星2x", "三星電子兩倍", "三星电子两倍"],
    "7347": ["三星反向", "三星-2x", "三星電子反向", "三星电子反向"],
    "7711": ["Coinbase兩倍", "Coinbase两倍", "Coinbase 2x", "COIN 2x", "COIN兩倍", "COIN两倍"],
    "7311": ["Coinbase反向", "Coinbase -2x", "COIN -2x", "COIN反向"],
    "7799": ["MicroStrategy兩倍", "MicroStrategy两倍", "MSTR 2x", "MSTR兩倍", "MSTR两倍",
             "微策略兩倍", "微策略两倍", "Strategy兩倍", "Strategy两倍"],
    "7399": ["MicroStrategy反向", "MSTR -2x", "MSTR反向", "微策略反向", "Strategy反向"],
    "7777": ["Berkshire兩倍", "Berkshire两倍", "巴郡兩倍", "巴郡两倍", "伯克希爾兩倍", "伯克希尔两倍",
             "巴菲特兩倍", "巴菲特两倍", "BRK 2x"],
    # 亞太
    "3153": ["南方日經", "南方日经", "日經ETF", "日经ETF", "日經225 ETF", "日经225 ETF"],
    "3004": ["越南30", "富時越南", "富时越南", "南方越南", "越南ETF南方"],
    "2830": ["沙特ETF", "沙特阿拉伯ETF", "南方沙特", "沙特ETF南方", "沙地ETF"],
    "3473": ["亞洲科技ETF", "亚洲科技ETF", "富時亞洲科技", "富时亚洲科技"],
    "7262": ["日經兩倍", "日经两倍", "日經2x", "日经2x", "兩倍日經", "两倍日经"],
    "7515": ["日經反向", "日经反向", "日經-2x", "日经-2x", "反向日經", "反向日经"],
    # 固收
    "3053": ["南方港元貨幣", "南方港元货币", "港元貨基", "港元货基", "南方港幣貨幣", "南方港币货币"],
    "3096": ["南方美元貨幣", "南方美元货币", "美元貨基南方", "美元货基南方", "南方美元貨基", "南方美元货基"],
    "3122": ["南方人民幣貨幣", "南方人民币货币", "人民幣貨基", "人民币货基", "南方人幣貨基"],
    "3199": ["中國國債ETF", "中国国债ETF", "政策性銀行債券ETF", "政策性银行债券ETF", "南方國債", "南方国债"],
    "3433": ["美債20年", "美债20年", "美國國債20年", "美国国债20年", "南方美債", "南方美债", "20年美債", "20年美债", "長債ETF", "长债ETF"],
    # 商品
    "3030": ["南方黃金", "南方黄金", "黃金ETF南方", "黄金ETF南方"],
    "7299": ["黃金兩倍", "黄金两倍", "黃金期貨兩倍", "黄金期货两倍", "黃金2x", "黄金2x", "兩倍黃金", "两倍黄金"],
    # 虚拟资产
    "3066": ["南方比特幣", "南方比特币", "比特幣期貨ETF", "比特币期货ETF", "南方比特幣期貨", "南方比特币期货"],
    "3068": ["南方以太幣", "南方以太币", "以太幣期貨ETF", "以太币期货ETF", "南方以太幣期貨", "南方以太币期货"],
    "7376": ["比特幣反向", "比特币反向", "比特幣-1x", "比特币-1x", "反向比特幣", "反向比特币"],
    # 同业
    "3032": ["恒生恒科", "恒生恒生科技ETF", "恒生科技指數ETF恒生", "恒生投資恒科", "恒生投资恒科"],
    "3589": ["恒科備兌", "恒科备兑", "恒生科技備兌", "恒生科技备兑"],
    "3067": ["安碩恒科", "安硕恒科", "iShares恒科", "安碩恒生科技", "安硕恒生科技"],
    "2837": ["GlobalX恒科", "Global X恒科", "Global X 恒生科技"],
    "3088": ["華夏恒科", "华夏恒科", "華夏恒生科技", "华夏恒生科技"],
    "3406": ["平安科技精選", "平安科技精选"],
    "2800": ["盈富", "盈富基金", "Tracker Fund", "TraHK"],
    "3115": ["安碩恒指", "安硕恒指", "安碩核心恒指", "安硕核心恒指", "iShares恒指"],
    "2828": ["恒生國企", "恒生国企", "國企指數上市基金", "国企指数上市基金", "H股ETF", "恒生H股", "國企ETF", "国企ETF"],
    "3519": ["恒生國指備兌", "恒生国指备兑"],
    "3416": ["GlobalX國指備兌", "GlobalX国指备兑", "Global X 國指備兌", "Global X 国指备兑"],
    "3070": ["平安高息股", "平安香港高息", "CSI香港高息股"],
    "3488": ["惠理紅利低波", "惠理红利低波", "港美紅利低波", "港美红利低波"],
    "3069": ["華夏生科", "华夏生科", "華夏恒生生物科技", "华夏恒生生物科技"],
    "3477": ["平安東西方", "平安东西方"],
    "3119": ["亞洲半導體", "亚洲半导体", "半導體ETF", "半导体ETF"],
    "2848": ["Xtrackers韓國", "Xtrackers韩国", "MSCI韓國ETF", "MSCI韩国ETF", "韓國ETF", "韩国ETF"],
    "3104": ["新興市場亞洲", "新兴市场亚洲", "GlobalX新興亞洲", "GlobalX新兴亚洲"],
    "2838": ["恒生富時中國50", "恒生富时中国50", "恒生A50", "恒生中國50", "恒生中国50"],
    "2823": ["安碩A50", "安硕A50", "iShares A50", "安碩富時中國A50", "安硕富时中国A50"],
    "2801": ["安碩MSCI中國", "安硕MSCI中国", "安碩核心MSCI中國", "安硕核心MSCI中国"],
    "3007": ["Xtrackers中國A", "Xtrackers中国A", "MSCI中國A UCITS", "MSCI中国A UCITS"],
    "3040": ["GlobalX MSCI中國", "GlobalX MSCI中国", "Global X MSCI 中国"],
    "2839": ["華夏A50", "华夏A50", "華夏MSCI中國A50", "华夏MSCI中国A50"],
    "2846": ["安碩滬深300", "安硕沪深300", "iShares滬深300", "iShares沪深300"],
    "3188": ["華夏滬深300", "华夏沪深300", "華夏300", "华夏300"],
    "2827": ["標智滬深300", "标智沪深300", "W.I.S.E.滬深300"],
    "3151": ["Premia科創50", "Premia科创50", "睦億科創", "睦亿科创"],
    "3173": ["Premia新經濟", "Premia新经济", "中國新經濟ETF", "中国新经济ETF"],
    "3086": ["華夏納指", "华夏纳指", "華夏納斯達克", "华夏纳斯达克"],
    "3451": ["納指備兌", "纳指备兑", "GlobalX納指備兌", "GlobalX纳指备兑"],
    "7261": ["華夏納指兩倍", "华夏纳指两倍", "華夏兩倍納指", "华夏两倍纳指"],
    "7522": ["華夏納指反向", "华夏纳指反向", "華夏反向納指", "华夏反向纳指"],
    "3087": ["越南掉期", "Xtrackers越南"],
    "2804": ["Premia越南", "越南市場ETF", "越南市场ETF"],
    "3410": ["恒生日本", "日本東證一百", "日本东证一百", "東證100", "东证100"],
    "3152": ["博時港元貨幣", "博时港元货币", "博時港元", "博时港元"],
    "3471": ["華夏港元數字貨幣", "华夏港元数字货币", "華夏港元", "华夏港元"],
    "3071": ["中金港元貨幣", "中金港元货币", "中金港元"],
    "3421": ["惠理港元貨幣", "惠理港元货币", "惠理港元"],
    "3137": ["GlobalX美元貨幣", "GlobalX美元货币", "Global X 美元货币"],
    "3011": ["工銀中金美元", "工银中金美元", "中金美元貨幣", "中金美元货币"],
    "3472": ["華夏美元數字貨幣", "华夏美元数字货币", "華夏美元", "华夏美元"],
    "3480": ["惠理美元貨幣", "惠理美元货币", "惠理美元"],
    "3196": ["博時美元貨幣", "博时美元货币", "博時美元", "博时美元"],
    "3461": ["華夏人民幣數字貨幣", "华夏人民币数字货币", "華夏人民幣數字", "华夏人民币数字"],
    "3161": ["華夏人民幣貨幣", "华夏人民币货币", "華夏人幣貨幣", "华夏人币货币"],
    "3420": ["惠理人民幣貨幣", "惠理人民币货币", "惠理人民幣", "惠理人民币"],
    "2829": ["安碩中國政府債券", "安硕中国政府债券", "iShares中國國債", "iShares中国国债"],
    "3041": ["GlobalX政策性銀行債", "GlobalX政策性银行债", "Global X 富时中国政策性银行"],
    "2817": ["Premia中國長久期", "Premia中国长久期", "中國長久期國債", "中国长久期国债"],
    "3146": ["華夏美債", "华夏美债", "華夏20年美債", "华夏20年美债", "華夏美國國債", "华夏美国国债"],
    "3077": ["Premia浮息", "美國國庫浮息", "美国国库浮息", "浮息票據ETF", "浮息票据ETF"],
    "3170": ["恒生黃金", "恒生黄金", "恒生黃金ETF", "恒生黄金ETF"],
    "2840": ["SPDR金", "SPDR黃金", "SPDR黄金", "道富黃金", "道富黄金", "SPDR Gold"],
    "3533": ["黃金備兌", "黄金备兑", "GlobalX黃金備兌", "GlobalX黄金备兑"],
    "3081": ["價值黃金", "价值黄金", "Sensible黃金", "Sensible黄金"],
    "3042": ["華夏比特幣", "华夏比特币", "華夏BTC", "华夏BTC"],
    "3046": ["華夏以太幣", "华夏以太币", "華夏ETH", "华夏ETH"],
}

# 同族共享叫法：出现即说明在说这一族的某只 ETF／指数，但不能定位到具体一只。
# 键是族名，值是成员产品。预过滤用它放行，Prompt 用它告诉模型「这些叫法可能指本产品」。
_FAMILY = {
    "恒科": {"terms": ["恒科", "恒生科技", "恆科", "恒生科指", "HSTECH", "科指"],
             "codes": ["3033", "3032", "3589", "3067", "2837", "3088", "3423", "7226", "7552"]},
    "恒指": {"terms": ["恒指", "恒生指數", "恒生指数", "HSI", "大市"],
             "codes": ["3037", "2800", "3115", "7200", "7300", "7500"]},
    "國指": {"terms": ["國指", "国指", "國企指數", "国企指数", "H股", "HSCEI", "國企", "国企"],
             "codes": ["2802", "2828", "3519", "3416", "7288", "7588"]},
    "納指": {"terms": ["納指", "纳指", "納斯達克", "纳斯达克", "NDX", "Nasdaq", "QQQ"],
             "codes": ["3034", "3086", "3451", "2834", "3455", "7266", "7568", "7261", "7522"]},
    "A50": {"terms": ["A50", "A股50", "中國A50", "中国A50"],
            "codes": ["2822", "3003", "2838", "2823", "2839", "3111", "2843"]},
    "滬深300": {"terms": ["滬深300", "沪深300", "300ETF", "CSI300"],
               "codes": ["3133", "2846", "3188", "2827", "7233"]},
    "黃金": {"terms": ["黃金", "黄金", "金ETF", "Gold", "GLD", "金價", "金价"],
             "codes": ["3030", "7299", "3170", "2840", "3533", "3081", "3418"]},
    "比特幣": {"terms": ["比特幣", "比特币", "BTC", "Bitcoin"],
               "codes": ["3066", "7376", "3042"]},
    "以太幣": {"terms": ["以太幣", "以太币", "ETH", "Ethereum", "以太坊"],
               "codes": ["3068", "3046"]},
    "日經": {"terms": ["日經", "日经", "日經225", "日经225", "Nikkei", "日股"],
             "codes": ["3153", "7262", "7515", "3410"]},
    "越南": {"terms": ["越南", "Vietnam", "越股"],
             "codes": ["3004", "3087", "2804"]},
    "港元貨幣": {"terms": ["港元貨幣", "港元货币", "港幣貨幣", "港币货币", "貨基", "货基", "貨幣基金", "货币基金", "貨幣市場", "货币市场", "現金管理", "现金管理"],
                "codes": ["3053", "3152", "3471", "3071", "3421"]},
    "美元貨幣": {"terms": ["美元貨幣", "美元货币", "美元貨基", "美元货基", "美元現金", "美元现金"],
                "codes": ["3096", "3137", "3011", "3472", "3480", "3196"]},
    "人民幣貨幣": {"terms": ["人民幣貨幣", "人民币货币", "人幣貨幣", "人币货币", "人民幣貨基", "人民币货基"],
                  "codes": ["3122", "3461", "3161", "3420"]},
    "美債": {"terms": ["美債", "美债", "美國國債", "美国国债", "TLT", "長債", "长债", "20年債", "20年债"],
             "codes": ["3433", "3146", "3077"]},
    "中國國債": {"terms": ["中國國債", "中国国债", "政策性銀行", "政策性银行", "中債", "中债", "國債ETF", "国债ETF"],
                "codes": ["3199", "2829", "3041", "2817"]},
    "生科": {"terms": ["生科", "生物科技", "醫藥ETF", "医药ETF", "生物醫藥", "生物医药"],
             "codes": ["3174", "3069"]},
    "備兌": {"terms": ["備兌", "备兑", "Covered Call", "covered call", "期權ETF", "期权ETF", "月月派", "高息ETF"],
             "codes": ["2802", "3469", "3589", "3519", "3416", "3070", "3488", "3451", "3533", "3537", "3031"]},
    "科創": {"terms": ["科創", "科创", "科創板", "科创板"],
             "codes": ["3109", "3151", "2832"]},
    "新經濟": {"terms": ["新經濟", "新经济"], "codes": ["3167", "3173"]},
    "韓國": {"terms": ["韓國", "韩国", "韓股", "韩股", "KOSPI", "Kospi"],
             "codes": ["3431", "2848", "3121", "3537", "3408"]},
    "港股紅利": {"terms": ["港股紅利", "港股红利", "港股通高股息", "港股高息", "紅利低波", "红利低波"],
                 "codes": ["3469", "3070", "3488", "3031"]},
    "香港股票": {"terms": ["香港股票", "港股大盤", "港股大盘", "MPF香港", "富時香港", "富时香港"],
                 "codes": ["3443", "3444", "3579"]},
    "亞太房托": {"terms": ["亞太房托", "亚太房托", "亞太REIT", "亚太REIT", "亞太房地產信託", "亚太房地产信托"],
                 "codes": ["3447", "3187"]},
    "英偉達": {"terms": ["英偉達", "英伟达", "輝達", "辉达", "NVDA", "Nvidia", "NVIDIA", "老黃", "老黄"],
               "codes": ["7788", "7388"]},
    "特斯拉": {"terms": ["特斯拉", "TSLA", "Tesla", "馬斯克", "马斯克"], "codes": ["7766", "7366"]},
    "三星": {"terms": ["三星", "Samsung", "三星電子", "三星电子"], "codes": ["7747", "7347"]},
    "海力士": {"terms": ["海力士", "Hynix", "SK海力士", "SK Hynix"], "codes": ["7709"]},
    "Coinbase": {"terms": ["Coinbase", "COIN", "coinbase"], "codes": ["7711", "7311"]},
    "MicroStrategy": {"terms": ["MicroStrategy", "MSTR", "微策略", "Strategy"], "codes": ["7799", "7399"]},
    "Berkshire": {"terms": ["Berkshire", "巴郡", "伯克希爾", "伯克希尔", "巴菲特", "BRK"], "codes": ["7777"]},
    "七巨頭": {"terms": ["七巨頭", "七巨头", "Mag7", "MAG7", "Magnificent 7", "七雄", "七姐妹"], "codes": ["3454"]},
    "太陽能": {"terms": ["太陽能", "太阳能", "光伏"], "codes": ["3134"]},
    "5G": {"terms": ["5G"], "codes": ["3193"]},
    "半導體": {"terms": ["半導體", "半导体", "晶片", "芯片"], "codes": ["3119"]},
    "沙特": {"terms": ["沙特", "沙地", "Saudi"], "codes": ["2830"]},
    "創業板": {"terms": ["創業板", "创业板", "創指", "创指"], "codes": ["3147"]},
    "中證500": {"terms": ["中證500", "中证500", "CSI500"], "codes": ["3005"]},
    "A500": {"terms": ["A500"], "codes": ["3101"]},
}

# 这些族名描述的是单一股票杠反产品所跟踪的正股，不是 ETF 自身。它们与指数／资产族
# 分开外发，避免下游把「NVDA」这类标的词误当成「7788」的产品别名。
_UNDERLYING_FAMILIES = frozenset(
    {"英偉達", "特斯拉", "三星", "海力士", "Coinbase", "MicroStrategy", "Berkshire"}
)

# 泛指当前讨论对象的指代词。评论区里「呢隻」「這隻」「佢」几乎总是指本讨论区的 ETF。
DEICTIC = (
    "呢隻", "呢只", "依隻", "依只", "這隻", "这只", "這只", "呢個", "呢个", "依個", "依个",
    "這個", "这个", "隻ETF", "只ETF", "ETF", "etf", "基金", "隻基金", "只基金",
    "呢隻貨", "呢只货", "呢隻產品", "呢只产品", "這個產品", "这个产品", "佢", "它",
)

# 产品属性与交易动作词汇。出现它们说明作者在谈「一只可以买卖的产品」，而不是在聊某只个股
# 的新闻 —— 这足以让预过滤放行、交给模型判。
PRODUCT_TALK = (
    "槓桿", "杠杆", "反向", "牛證", "牛证", "熊證", "熊证", "貼水", "贴水", "溢價", "溢价",
    "跟蹤", "跟踪", "點差", "点差", "費率", "费率", "管理費", "管理费", "分紅", "分红", "派息", "收息",
    "成交量", "流動性", "流动性", "淨值", "净值", "損耗", "损耗", "定投", "加倉", "加仓", "減倉", "减仓",
    "建倉", "建仓", "清倉", "清仓", "持有", "持倉", "持仓", "入貨", "入货", "出貨", "出货", "接貨", "接货",
    "掃貨", "扫货", "止蝕", "止蚀", "止損", "止损", "溝貨", "沟货", "佈局", "布局", "上車", "上车",
    "落車", "落车", "追入", "沽", "揸", "揸住", "坐貨", "坐货",
)

# 标的标签：ETL 把富文本里的标的还原成 `$03033.HK$`；美股是 `$NVDA.US$`。
TAG_RE = re.compile(r"\$([A-Za-z0-9.]{1,12})\.(HK|US|SH|SZ|hk|us|sh|sz)\$")
# 裸代码：4–5 位港股代码，前后不是数字。评论里「3033」「03033」「3033.HK」都常见。
# 用正则而不是子串：`"7200" in "27200股"` 会误命中。
BARE_CODE_RE = re.compile(r"(?<![0-9])0?(\d{4})(?:\.HK|\.hk|HK)?(?![0-9])")


def load_products(path=None):
    if path is None:
        return load_catalog_products()
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["products"]


def _code_forms(code):
    """子串匹配安全的代码写法。裸代码（`3033`）不在这里 —— 它由 `BARE_CODE_RE` 按词边界匹配。"""
    padded = code.zfill(5)
    return [f"{code}.HK", f"{padded}.HK", f"${padded}.HK$", f"{code}.hk", f"{padded}.hk"]


def _name_forms(name, *, strip_product_suffix=True):
    forms = {name, to_simplified(name), to_traditional(name)}
    # 宽松语境识别历史上接受去掉「ETF／產品」的写法；exact 产品归属不能这样做，
    # 否则 3037 的「恒生指數ETF」会退化成共享底层词「恒生指數」。
    for n in list(forms):
        stripped = re.sub(r"\s+", "", n)
        forms.add(stripped)
        if strip_product_suffix:
            forms.add(re.sub(r"(ETF|產品|产品)$", "", stripped))
    return [f for f in forms if f]


def _family_of(code):
    return [fam for fam, spec in _FAMILY.items() if code in spec["codes"]]


class ProductLexicon:
    """生产产品池的别名词表，带「这段文字提到了这只产品吗」的判定。"""

    def __init__(self, products=None):
        products = products or load_products()
        self.by_code = {}
        for p in products:
            code = p["code"]
            raw_source_aliases = list(p.get("shortNames", [])) + list(p.get("aliases", []))
            source_aliases = _dedupe(
                variant
                for alias in raw_source_aliases
                for variant in (alias, unicodedata.normalize("NFKC", alias))
            )
            nick = _dedupe(_NICKNAMES.get(code, []) + source_aliases)
            source_names = _dedupe([p["name"], p.get("officialName")] + source_aliases)
            fams = _family_of(code)
            family_terms = _dedupe(
                term
                for family in fams
                if family not in _UNDERLYING_FAMILIES
                for term in _FAMILY[family]["terms"]
            )
            underlying_terms = _dedupe(
                term
                for family in fams
                if family in _UNDERLYING_FAMILIES
                for term in _FAMILY[family]["terms"]
            )
            context_terms = {
                term.casefold() for term in family_terms + underlying_terms
            }
            direct_nick = [
                alias for alias in nick if alias.casefold() not in context_terms
            ]
            exact_names = _dedupe([p["name"], p.get("officialName")] + direct_nick)
            self.by_code[code] = {
                "code": code,
                "name": p["name"],
                "name_simplified": to_simplified(p["name"]),
                "ownership": p["ownership"],
                # 唯一别名：代码写法＋俗称。`test_lexicon.py` 断言跨产品不冲突。
                "aliases": _dedupe(_code_forms(code) + direct_nick),
                "nicknames": direct_nick,
                # 全名变体：**允许**跨产品重名 —— 3033「恒生科技指數ETF」与同业 3032
                # 「恒生科技指数ETF」只差简繁，这是产品命名的事实，不是词表的错。
                "name_forms": _dedupe(
                    form for name in source_names if name for form in _name_forms(name)
                ),
                # Deterministic routing accepts only the full product form (or
                # a unique direct nickname), never suffix-stripped index/asset
                # names such as 「恒生指數」, BTC or ETH.
                "exact_name_forms": _dedupe(
                    form
                    for name in exact_names
                    if name
                    for form in _name_forms(name, strip_product_suffix=False)
                ),
                "family": fams,
                "family_terms": family_terms,
                "underlying_terms": underlying_terms,
            }
        name_owners = {}
        for code, product in self.by_code.items():
            for name in {value.casefold() for value in product["exact_name_forms"]}:
                name_owners.setdefault(name, set()).add(code)
        self._unique_name_forms = {
            code: [
                name for name in product["exact_name_forms"]
                if len(name_owners[name.casefold()]) == 1
            ]
            for code, product in self.by_code.items()
        }

    # ── 给 Prompt 用 ───────────────────────────────────────────────

    def product_block(self, code, max_aliases=12):
        """发给模型的产品块，明确区分唯一标识与仅供消歧的上下文词。

        ``aliases`` 只放能唯一指向该产品的代码／俗称。``family_terms`` 与
        ``underlying_terms`` 可能同时指向多只产品或标的资产，绝不能被调用方当成产品
        相关性的充分证据。
        """
        p = self.by_code[code]
        ordered = _dedupe(
            p["nicknames"]
            + [f"{code}", f"{code.zfill(5)}.HK", f"${code.zfill(5)}.HK$"]
        )
        return {
            "code": code,
            "name": p["name"],
            "aliases": ordered[:max_aliases],
            "family_terms": list(p["family_terms"]),
            "underlying_terms": list(p["underlying_terms"]),
        }

    # ── 给预过滤用 ─────────────────────────────────────────────────

    def references(self, text, code):
        """文字里有没有足以让预过滤放行的候选信号。

        这是召回优先的预过滤接口，不是「已证明产品相关」。共享族名与标的词只会让
        评论继续进入判定，不能直接产出 ``relevance=relevant``。需要严格判断唯一产品
        标识时使用 :meth:`references_product`。
        """
        signals = self.reference_signals(text, code)
        return any(signals.values())

    def references_product(self, text, code):
        """是否命中当前产品的唯一代码、唯一俗称或非歧义名称。"""
        return bool(self.reference_signals(text, code)["product"])

    def reference_signals(self, text, code):
        """返回命中的产品唯一标识、共享族词和标的词，三类永不混装。"""
        empty = {"product": [], "family": [], "underlying": []}
        if not text:
            return empty
        p = self.by_code.get(code)
        if p is None:
            return empty

        product = []
        tag_spans = []
        for match in TAG_RE.finditer(text):
            if _tag_to_code(match.group(1), match.group(2)) == code:
                product.append(match.group(0))
                tag_spans.append(match.span())
        for match in BARE_CODE_RE.finditer(text):
            inside_tag = any(start <= match.start() and match.end() <= end for start, end in tag_spans)
            if match.group(1) == code and not inside_tag:
                product.append(match.group(0))

        low = text.casefold()
        code_forms = {form.casefold() for form in _code_forms(code)}
        for alias in p["aliases"]:
            if alias.casefold() in code_forms:
                continue
            if alias.casefold() in low:
                product.append(alias)

        # 全名只有在没有被别的产品共享时才算唯一标识。简繁转换可能把两个发行商产品
        # 归成同一名称，因此不能假设每个 name_form 都唯一。
        for name in self._unique_name_forms[code]:
            if name.casefold() in low:
                product.append(name)

        family = [term for term in p["family_terms"] if term.casefold() in low]
        underlying = [term for term in p["underlying_terms"] if term.casefold() in low]
        return {
            "product": _dedupe(product),
            "family": _dedupe(family),
            "underlying": _dedupe(underlying),
        }

    def mentions_deictic(self, text):
        """有没有「呢隻／它」这类指代，或费率／点差／加仓这类产品属性与交易动作词。"""
        if not text:
            return False
        low = text.lower()
        return any(d.lower() in low for d in DEICTIC) or any(d.lower() in low for d in PRODUCT_TALK)

    def underlying_terms(self, code):
        """单一股票杠反产品的「标的股」叫法：在这些产品的讨论区里，提到标的股不算「跑题到个股」。"""
        p = self.by_code.get(code)
        if p is None:
            return set()
        return set(p["underlying_terms"])

    def pool_codes(self):
        return set(self.by_code)


def _tag_to_code(symbol, market):
    market = market.upper()
    if market == "HK":
        return symbol.lstrip("0") or "0"
    return f"{market.lower()}:{symbol}"


def _dedupe(items):
    seen, out = set(), []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


def family_specs():
    """只读视图，给测试与审计用。"""
    return {k: dict(v) for k, v in _FAMILY.items()}


def nicknames():
    return {k: list(v) for k, v in _NICKNAMES.items()}
