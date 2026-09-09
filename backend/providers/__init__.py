"""数据 provider 接缝（ADR-0001）。

同一份端点实现挂两种数据源：

- ``demo``  —— 取数自 fixtures/demo/，逐字来自设计源 design/radar-data.js。
              用于 100% 还原验收（ADR-0006）：页面必须与设计源静态站逐字相同。
- ``mysql`` —— 取数自真实库。第一期只留空壳，实现见 ADR-0008 / ADR-0014。

选择靠环境变量 DATA_PROVIDER（默认 demo）。两者同镜像、同端点、不同 compose 服务。

**provider 只负责「取到原始返回」，不负责判定状态。** 状态（ok/empty/unavailable/
low_sample/na）由 core/envelope.py 统一判定 —— 散在各 provider 里判，六态就有两套语义了。
"""

import os

from .demo import DemoProvider
from .mysql import MysqlProvider

_PROVIDERS = {"demo": DemoProvider, "mysql": MysqlProvider}

_instance = None


def get_provider():
    """当前进程的 provider 单例。演示 provider 会把 fixture 读进内存，不要每次请求重建。"""
    global _instance
    if _instance is None:
        name = os.getenv("DATA_PROVIDER", "demo").strip().lower()
        if name not in _PROVIDERS:
            raise ValueError(
                f"未知的 DATA_PROVIDER={name!r}，可选：{', '.join(sorted(_PROVIDERS))}"
            )
        _instance = _PROVIDERS[name]()
    return _instance


def reset_provider():
    """测试用：丢弃单例，让下一次 get_provider() 重新按环境变量构建。"""
    global _instance
    _instance = None
