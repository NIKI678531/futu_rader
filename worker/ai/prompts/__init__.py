"""Prompt 注册表 —— 任务名（＋版本）到 Prompt 模块的唯一映射。

业务代码按任务名取 Prompt，不 import 具体模块：换版本时只改这张表，调用方一行不动。
版本号跟着 Prompt 走（每个模块的 `VERSION`），不从环境变量另配一份 —— 配串和实际用的
Prompt 对不上时，`annotation_runs.prompt_version` 记的就是一个假版本。

## 为什么要留旧版本

`v1` 是 Gate 0–2 影子运行用过的形状。留着它有两个用处：回放旧 run 的 `input_hash`
时要用同一份正文；步骤 11 的「v1 vs v2 一致率」实验要两版同时可取。**默认取最新**。
"""

from . import (
    comment_product_v1,
    comment_product_v2,
    post_annotation_v1,
    post_annotation_v2,
)

# 任务 → {VERSION: 模块}。每个任务的第一个键是默认（最新）版本。
_REGISTRY = {
    "comment_product": {
        comment_product_v2.VERSION: comment_product_v2,
        comment_product_v1.VERSION: comment_product_v1,
    },
    "post_annotation": {
        post_annotation_v2.VERSION: post_annotation_v2,
        post_annotation_v1.VERSION: post_annotation_v1,
    },
}

# Prompt 版本 → 它输出的 schema 版本。Prompt 与 schema 是一对：v2 Prompt 要求七个字段，
# 拿 v1 schema 校验会整批失败。
SCHEMA_OF = {
    comment_product_v1.VERSION: "v1",
    comment_product_v2.VERSION: "v2",
    post_annotation_v1.VERSION: "v1",
    post_annotation_v2.VERSION: "v2",
}


class UnknownTask(KeyError):
    pass


def get(task, version=None):
    """取该任务的 Prompt 模块（有 `VERSION` / `SYSTEM` / `user_message`）。

    `version` 给了且属于这个任务 ⇒ 用它；给了但属于**别的**任务（`AI_PROMPT_VERSION` 一个
    变量盖两个任务时常见）或没给 ⇒ 用该任务的默认版本。给了一个谁都不认识的版本 ⇒ 报错，
    而不是静默退回默认 —— 那多半是 .env 打错了字。
    """
    try:
        versions = _REGISTRY[task]
    except KeyError:
        raise UnknownTask(f"未知任务 {task!r}，可选：{', '.join(sorted(_REGISTRY))}") from None
    if version is None:
        return next(iter(versions.values()))
    if version in versions:
        return versions[version]
    if any(version in v for v in _REGISTRY.values()):
        return next(iter(versions.values()))
    raise UnknownTask(
        f"任务 {task!r} 没有 Prompt 版本 {version!r}，可选：{', '.join(versions)}"
    )


def schema_version_for(prompt_module):
    return SCHEMA_OF[prompt_module.VERSION]


def tasks():
    return tuple(sorted(_REGISTRY))


def versions(task):
    return tuple(_REGISTRY[task])
