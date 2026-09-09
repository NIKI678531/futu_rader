"""响应信封与状态判定 —— 六态（PRD §3.6）在接口层的**唯一**落点。

## 为什么状态判定在这里，不在各端点里

六态是字段级语义，但接口层还要给整个响应一个 status 枚举。若每个端点自己判，
很快就会出现「这个端点把空数组判成 unavailable、那个判成 empty」。判定规则集中在
本模块一处，端点只负责取数。

## 判定规则（PRD §5 结尾逐字：「字段级 null＝暂不可用；空数组＝暂无内容」）

| provider 返回 | status        | HTTP | 前端渲染 |
|---------------|---------------|------|----------|
| None          | `unavailable` | 200  | 「数据暂不可用」／短徽章「暂不可用」 |
| `[]` / `{}`   | `empty`       | 200  | 「暂无内容」／空态「暂无相关内容」 |
| 其他          | `ok`          | 200  | 正常渲染 |

另两态由端点显式声明，判不出来：
- `low_sample` —— 有效样本 < LOW_SAMPLE(10)，不输出倾向结论。
- `na`         —— 结构性不适用（前端渲染 `—`）。

## 两条硬约束

1. **数据缺失一律 HTTP 200。** 404/5xx 描述的是「请求成不成立」，不是「数据有没有」。
   混用会让前端分不清「服务挂了」和「这个字段没有」——而这两件事在 UI 上必须长得不一样
   （屏级错误条 vs 字段级「暂不可用」）。
2. **绝不把 None 替换成 0 或 []。** `0` 是「已取得数据且确实为零」的专用值；
   拿它冒充未知就是撒谎（CLAUDE.md 铁律 2）。本模块不含任何默认值兜底。
"""

from flask import jsonify

# PRD §3.6 接口层 status 枚举。前端按这五个值分支，不接受别的取值。
OK = "ok"
EMPTY = "empty"
UNAVAILABLE = "unavailable"
LOW_SAMPLE = "low_sample"
NA = "na"

STATUSES = (OK, EMPTY, UNAVAILABLE, LOW_SAMPLE, NA)


def status_of(data):
    """按 PRD §5 结尾的状态语义判定 status。不改 data 本身。"""
    if data is None:
        return UNAVAILABLE
    # 只有容器类型才谈得上「空」。0 和 "" 不是空态：0 是真实的零，"" 由字段自己表达。
    if isinstance(data, (list, tuple, dict)) and len(data) == 0:
        return EMPTY
    return OK


def envelope(data, status=None):
    """构造 200 响应体。status 省略时按 data 自动判定；显式传入用于 low_sample / na。"""
    st = status if status is not None else status_of(data)
    if st not in STATUSES:
        raise ValueError(f"未知 status={st!r}，可选：{', '.join(STATUSES)}")
    return jsonify({"status": st, "data": data})


def respond(data, status=None):
    """端点的标准出口。数据缺失也是 200 —— 见模块头「两条硬约束」第 1 条。"""
    return envelope(data, status), 200
