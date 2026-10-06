"""供应商注册表。

runbook §2.3：第一位实现 OpenAI-compatible Provider，**不新增 DeepSeek Provider**
（除非未来另行决策）。本地 Qwen 兜底（`local_qwen.py`）属 P1，等外部 API 的数据治理
结论或难例需求出现再建 —— 现在建一个空壳只会让人以为有兜底。
"""

from .base import (  # noqa: F401
    Completion,
    PermanentError,
    Provider,
    ProviderError,
    TransientError,
    Usage,
)
from .openai_compatible import OpenAiCompatibleProvider
from .typesafe_system_one import TypeSafeSystemOneProvider

_PROVIDERS = {
    "openai_compatible": OpenAiCompatibleProvider,
    "typesafe_system_one": TypeSafeSystemOneProvider,
}


def build(config, **kwargs):
    """按配置造供应商实例。未知名称立刻抛错，不回退到默认 —— 回退会让一个配置笔误
    表现为「用了另一个模型」，而标注结果上看不出来。"""
    try:
        cls = _PROVIDERS[config.provider]
    except KeyError:
        raise ValueError(
            f"未知的 AI_PRIMARY_PROVIDER={config.provider!r}，"
            f"可选：{', '.join(sorted(_PROVIDERS))}"
        ) from None
    return cls(config, **kwargs)


def generation_supported(config) -> bool:
    """Whether the configured Layer B provider can write text (TypeSafe cannot)."""
    from .. import config as ai_config

    cls = _PROVIDERS.get(ai_config.synthesis(config).provider)
    return cls is not None and getattr(cls, "supports_generation", True)


def build_synthesis(config, *, primary=None, control=None):
    """Return ``(cfg, provider)`` for Layer B generation.

    Reuses ``primary`` when no dedicated ``AI_SYNTH_*`` model is configured;
    otherwise builds the generation provider on the same ``RunControl`` so both
    layers share one request budget.
    """
    from .. import config as ai_config

    synth_cfg = ai_config.synthesis(config)
    if synth_cfg is config and primary is not None:
        return synth_cfg, primary
    if control is None and primary is not None:
        control = getattr(primary, "control", None)
    return synth_cfg, build(synth_cfg, control=control)
