"""基准区间与时间桶（PRD §3.1、§5 `buildRange(key)`）。

PRD §5 逐字：区间与桶由**后端下发，前端不自行算桶**。这条不是性能考虑——桶的边界
决定了热力图、趋势图、K 线三者的横轴是否对齐，前端各算各的就一定会错开。

同一条口径也定义了「基准区间」：与当前区间等长、紧邻其前的一段（d1/d2 例外，
文案是「较昨日同期」）。环比 delta 的分母就是它，所以它必须和区间一起下发。

演示 provider 的取值逐字来自设计源；真实 provider 的实现是同一套日历规则的 SQL 版本，
**只会有这一份**（铁律 1）。
"""

from providers import get_provider
from providers.demo import MISSING

# PRD §3.1 的五个预设。未知 key 不做兜底回落——静默回落到 d7 会让前端以为自己拿到了
# 请求的区间，图表横轴和标题却是另一段时间。
VALID_KEYS = ("d1", "d2", "d7", "d14", "d30")


def build_range(key):
    """返回该预设区间的完整描述（含 buckets / benchFrom / benchTo / 各处文案）。

    未知 key 返回 MISSING —— 那是「没这个东西」，由端点转成 404；
    与「有这个东西但取不到值」（None → unavailable）是两回事。
    """
    if key not in VALID_KEYS:
        return MISSING
    return get_provider().build_range(key)
