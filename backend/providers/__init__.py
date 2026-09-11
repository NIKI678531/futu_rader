"""数据 provider 接缝（ADR-0001）。

同一份端点实现挂两种数据源：

- ``demo`` —— 取数自 fixtures/demo/，逐字来自设计源 design/radar-data.js。
             用于 100% 还原验收（ADR-0006）：页面必须与设计源静态站逐字相同。
- ``sql``  —— 取数自真实瘦库（本地 SQLite / 生产 MySQL，`RADAR_DB_URL` 决定方言）。
             计数、内容、日历是真的；AI 标注与行情返回 None，见 providers/sql.py 模块头。

选择靠环境变量 DATA_PROVIDER（默认 demo）。两者同镜像、同端点、不同 compose 服务。
``mysql`` 是 ``sql`` 的旧名别名：本地跑的是 SQLite，叫 mysql 会让人以为本地也要起 MySQL。

**provider 只负责「取到原始返回」，不负责判定状态。** 状态（ok/empty/unavailable/
low_sample/na）由 core/envelope.py 统一判定 —— 散在各 provider 里判，六态就有两套语义了。
"""

import os

from .demo import DemoProvider
from .sql import SqlProvider

_PROVIDERS = {"demo": DemoProvider, "sql": SqlProvider, "mysql": SqlProvider}

_instance = None


def get_provider():
    """当前进程的 provider 单例。演示 provider 会把 fixture 读进内存，不要每次请求重建。

    每次取用都先 `refresh()`：单例意味着**进程启动那一刻的库状态会被一直沿用下去**。
    compose 把 backend 和 worker 一起拉起来时库还是空的，没有这一下，导入跑完之后
    页面依旧整屏「暂不可用」，直到有人重启容器。`refresh()` 自己判断有没有变化，
    没变就一行不动（见 `SqlProvider.refresh`）。
    """
    global _instance
    if _instance is None:
        name = os.getenv("DATA_PROVIDER", "demo").strip().lower()
        if name not in _PROVIDERS:
            raise ValueError(
                f"未知的 DATA_PROVIDER={name!r}，可选：{', '.join(sorted(_PROVIDERS))}"
            )
        _instance = _PROVIDERS[name]()
    else:
        _instance.refresh()
    return _instance


def reset_provider():
    """测试用：丢弃单例，让下一次 get_provider() 重新按环境变量构建。"""
    global _instance
    _instance = None
