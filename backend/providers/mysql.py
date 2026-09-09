"""MySQL provider —— 第一期空壳（ADR-0001、ADR-0002、ADR-0014）。

真实供数不在本期范围内：10GB dump 尚未导入（ADR-0008），AI 标注管线尚未建立
（ADR-0010），所以绝大多数派生字段此刻确实取不到。

关键在于**它怎么"取不到"**：每个方法返回 None，由 core/envelope.py 判成
status=unavailable + HTTP 200，前端渲染「暂不可用」。**绝不能返回 0 或 []** ——
那是在说「查过了，真的是零 / 真的没有」，而事实是「还没接上」。这正是铁律 2。

口径实现（手写 SQL，SQLAlchemy Core 执行）落在 backend/core/，不在这里：
provider 只负责取数，不负责算口径。
"""


class MysqlProvider:
    name = "mysql"

    def __init__(self):
        # 连接池在真正实现取数时才建。现在建了也没人用，反而让没有库的环境起不来。
        pass

    # ── PRD §5 契约函数 ────────────────────────────────────────────────
    # 一律 None＝暂不可用。不是 0，不是 []。

    def build_range(self, key):
        # 区间与时间桶是纯日历计算，不依赖库，本可以现在就算。但那会让 mysql provider
        # 的口径实现散在两处（一部分在这里、一部分在将来的 SQL 里），先统一挂起。
        return None

    def official_posts(self, range_key):
        return None

    def etf_mentions_for(self, account, range_key):
        return None
