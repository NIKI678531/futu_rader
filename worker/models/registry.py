"""学生模型的型号登记 —— 模型 ID、revision、本地目录、路由阈值的**唯一**入口（runbook §2.4）。

## 为什么锁 revision

Hugging Face 上同一个 repo 的 `main` 会被作者覆盖推送。不锁 revision 的后果是：两台机器
在不同日期 `from_pretrained` 得到两份不同权重，而 `annotation_runs.model_id` 记的都是
`Langboat/mengzi-bert-base-fin`，库里出现两种模型混写的标注且没有任何一行是错的。
所以 `model_id` 落库时带 `@<revision>`。

## 三个候选

| 键 | repo | 层数 | 为什么在名单上 |
|---|---|---|---|
| `mengzi-fin` | Langboat/mengzi-bert-base-fin | 12 | 金融语料继续预训练的中文 BERT，主选 |
| `roberta-wwm` | hfl/chinese-roberta-wwm-ext | 12 | 通用中文强基线，主选训不动时的备选 |
| `rbt3` | hfl/rbt3 | 3 | 三层，CPU 推理约 3 倍快；吞吐不够时换它 |

三个都是 BERT 架构、同一套 WordPiece 词表族，`train.py`／`export.py`／`infer.py` 一份代码
通吃，换模型只改 `--model`。
"""

import os
from pathlib import Path

MODELS = {
    "mengzi-fin": {
        "hf_id": "Langboat/mengzi-bert-base-fin",
        "revision": "3e525b4d7a7cc2f886980490ec6ca0734b104dfc",
        "layers": 12,
    },
    "roberta-wwm": {
        "hf_id": "hfl/chinese-roberta-wwm-ext",
        "revision": "5c58d0b8ec1d9014354d691c538661bf00bfdb44",
        "layers": 12,
    },
    "rbt3": {
        "hf_id": "hfl/rbt3",
        "revision": "0aa0527ff4170f29e1dfd3eb6ef60dc67e1bf75c",
        "layers": 3,
    },
    # 离线测试专用：随机初始化的极小 BERT，只验证管线，不代表任何准确率。
    "tiny": {"hf_id": "tiny-random-bert", "revision": "none", "layers": 1},
}

DEFAULT_MODEL = "mengzi-fin"
STUDENT_VERSION = "student-v1"

HEADS = ("relevance", "attitude")
LABELS = {
    "relevance": ("relevant", "irrelevant", "needs_context"),
    "attitude": ("positive", "neutral", "negative"),
}
ASPECT_HEAD = "aspect"
MAX_SEQ_LEN = 128


def spec(name=None):
    name = name or DEFAULT_MODEL
    if name not in MODELS:
        raise KeyError(f"未登记的学生模型 {name!r}，可选：{sorted(MODELS)}")
    return {"name": name, **MODELS[name]}


def model_id_string(name=None):
    """落 `annotation_runs.model_id` 的字符串：`<hf id>@<revision>`。"""
    s = spec(name)
    return f"{s['hf_id']}@{s['revision']}"


def model_dir():
    """本地权重目录。`STUDENT_MODEL_DIR` 指定，默认 `<数据目录>/models/student-v1`（仓库外）。"""
    env = os.getenv("STUDENT_MODEL_DIR", "").strip()
    if env:
        return Path(env)
    from radar_db import default_data_dir
    return default_data_dir() / "models" / STUDENT_VERSION


def dataset_dir():
    env = os.getenv("STUDENT_DATASET_DIR", "").strip()
    if env:
        return Path(env)
    from radar_db import default_data_dir
    return default_data_dir() / "datasets" / STUDENT_VERSION


def _float_env(name, default):
    v = os.getenv(name, "").strip()
    return float(v) if v else default


def route_threshold():
    """任一头最大概率低于它 ⇒ 交 Luna。默认 0.85（ADR-0021）。"""
    return _float_env("STUDENT_ROUTE_THRESHOLD", 0.85)


def margin_threshold():
    """态度前两类概率差低于它 ⇒ 交 Luna。默认 0.15。"""
    return _float_env("STUDENT_MARGIN_THRESHOLD", 0.15)


def review_threshold():
    """任一头概率低于它 ⇒ 学生行打 `needs_review`。与路由阈值同值：判不准的既送 Luna 也挂徽章。"""
    return _float_env("STUDENT_REVIEW_THRESHOLD", route_threshold())
