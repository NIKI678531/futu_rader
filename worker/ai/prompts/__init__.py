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
    comment_product_v3,
    kol_opinion_v1,
    kol_opinion_v2,
    post_annotation_v1,
    post_annotation_v2,
)

# 任务 → {VERSION: 模块}。每个任务的第一个键是默认（最新）版本。
_REGISTRY = {
    "comment_product": {
        comment_product_v3.VERSION: comment_product_v3,
        comment_product_v2.VERSION: comment_product_v2,
        comment_product_v1.VERSION: comment_product_v1,
    },
    "post_annotation": {
        post_annotation_v2.VERSION: post_annotation_v2,
        post_annotation_v1.VERSION: post_annotation_v1,
    },
    "kol_comment_opinion": {
        kol_opinion_v2.VERSION: kol_opinion_v2,
        kol_opinion_v1.VERSION: kol_opinion_v1,
    },
}

# Prompt 版本 → 它输出的 schema 版本。Prompt 与 schema 是一对：v2 Prompt 要求新增字段，
# 拿 v1 schema 校验要么整批失败，要么被供应商的 strict schema 截掉新字段。
SCHEMA_OF = {
    comment_product_v1.VERSION: "v1",
    comment_product_v2.VERSION: "v2",
    # v3 tightens product/context semantics without changing the seven-field
    # output shape, so it intentionally reuses the proven strict v2 schema.
    comment_product_v3.VERSION: "v2",
    post_annotation_v1.VERSION: "v1",
    post_annotation_v2.VERSION: "v2",
    kol_opinion_v1.VERSION: "v1",
    kol_opinion_v2.VERSION: "v2",
}


class UnknownTask(KeyError):
    pass


def get(task, version=None, schema_version=None):
    """取该任务的 Prompt 模块（有 `VERSION` / `SYSTEM` / `user_message`）。

    `version` 给了且属于这个任务 ⇒ 用它；给了但属于**别的**任务（`AI_PROMPT_VERSION` 一个
    变量盖多个任务时常见）⇒ 优先取该任务与 `schema_version` 配对的版本，没传 schema 时
    才取默认。给了一个谁都不认识的版本 ⇒ 报错，而不是静默退回默认。
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
        if schema_version is not None:
            for module in versions.values():
                if SCHEMA_OF.get(module.VERSION) == schema_version:
                    return module
            raise UnknownTask(
                f"任务 {task!r} 没有配对 schema {schema_version!r} 的 Prompt"
            )
        return next(iter(versions.values()))
    raise UnknownTask(
        f"任务 {task!r} 没有 Prompt 版本 {version!r}，可选：{', '.join(versions)}"
    )


def schema_version_for(prompt_module):
    return SCHEMA_OF[prompt_module.VERSION]


def requires_full_reannotation(task, version=None, schema_version=None):
    """Whether a Prompt revision must replace complete historical conclusions.

    This policy is kept beside the version registry so enqueue code does not
    hard-code one release name. The write path's existing ``supersedes_id``
    rules remain the source of truth for preserving human-settled rows.
    """
    prompt_module = get(task, version, schema_version=schema_version)
    return bool(getattr(prompt_module, "REQUIRES_FULL_REANNOTATION", False))


def tasks():
    return tuple(sorted(_REGISTRY))


def versions(task):
    return tuple(_REGISTRY[task])
