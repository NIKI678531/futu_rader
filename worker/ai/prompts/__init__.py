"""Prompt 注册表 —— 任务名到 Prompt 模块的唯一映射。

业务代码按任务名取 Prompt，不 import 具体模块：换版本时只改这张表，调用方一行不动。
版本号跟着 Prompt 走（每个模块的 `VERSION`），不从环境变量另配一份 —— 配串和实际用的
Prompt 对不上时，`annotation_runs.prompt_version` 记的就是一个假版本。
"""

from . import comment_product_v1, post_annotation_v1

_REGISTRY = {
    "comment_product": comment_product_v1,
    "post_annotation": post_annotation_v1,
}


class UnknownTask(KeyError):
    pass


def get(task):
    """取该任务当前的 Prompt 模块（有 `VERSION` / `SYSTEM` / `user_message`）。"""
    try:
        return _REGISTRY[task]
    except KeyError:
        raise UnknownTask(
            f"未知任务 {task!r}，可选：{', '.join(sorted(_REGISTRY))}"
        ) from None


def tasks():
    return tuple(sorted(_REGISTRY))
