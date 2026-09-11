"""「没这个资源」的哨兵 —— 与「这个字段暂不可用」严格分开的那一个值。

## 为什么它不能是 None

`None` 是一个**合法的数据取值**：它就是六态里的 `unavailable`，会渲染成「暂不可用」
（`core/envelope.py`、铁律 2）。而「你要的这个产品代码／区间预设／官号根本不存在」
是另一件事 —— 那是请求不成立，该回 404。

两件事都用 `None` 表示的话，拼错一个产品代码会得到一屏「暂不可用」而不是 404：
页面看起来像是采集出了问题，实际上是 URL 打错了。查错方向从一开始就是反的。

所以 provider 层约定：**查不到键返回 `MISSING`，取到了但没有值返回 `None`。**
端点把前者转成 404，把后者交给信封判成 200 + `status: unavailable`。

## 为什么它从 providers/demo.py 搬了出来（2026-09-11）

原来它定义在 `providers/demo.py` 里，因为当时只有 demo provider 会「查不到键」。
接了真库之后 `sql` provider 也要表达同一件事（未知官号、未知产品代码），而
`providers/sql.py` 去 import `providers.demo` 是说不通的 —— 两个 provider 是平行的
实现，谁都不该依赖另一个。搬到这里之后两边都 import 它，谁也不依赖谁。

`providers.demo.MISSING` 仍然可用（那边 re-export），但新代码请从这里取。
"""


class _Missing:
    """「查不到这个参数键」的哨兵。与 None（字段暂不可用）严格区分。"""

    def __repr__(self):
        return "MISSING"

    # falsy：`if not data` 这种写法在两种情况下都成立，读起来不会突然反直觉。
    # 但判定必须用 `is MISSING` —— `== MISSING` 和布尔判断都区分不了它和 None。
    def __bool__(self):
        return False


MISSING = _Missing()
