"""AI 接入配置 —— 全部来自环境变量，一个默认密钥都没有。

## 为什么模型名不写在代码里

runbook §2.4 的结论：型号的唯一入口是配置与 `worker/models/registry.py`，业务模块不得
各自硬编码。换供应商时如果模型名散在五个文件里，换掉四个仍然能跑 —— 剩下那一个会安静地
继续用旧模型，而标注结果表上 `model_id` 记的是**响应返回的**那个，于是库里出现两种模型
混写的标注，且没有任何一行是错的。

## 密钥只从环境变量来

runbook §0：Key 不进代码、不进文档、不进 Git。本地放 `worker/.env`（已被 `.gitignore`
排除），生产由 Secret Manager 注入同名变量。**任何 `VITE_` 前缀的变量都会被打进前端包**，
所以这里的变量一个都不许带那个前缀。
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_ENV = Path(__file__).resolve().parents[1] / ".env"
if _ENV.exists():
    load_dotenv(_ENV, override=False)


class ConfigError(RuntimeError):
    """配置缺失。**故意抛异常而不是用默认值兜底**：没配 Key 就跑，第一批请求会全部 401，
    而重试逻辑会把它当成瞬时故障退避重试三轮，最后写进 dead-letter —— 一个配置问题会
    伪装成一次供应商故障。"""


def _require(name):
    v = os.getenv(name, "").strip()
    if not v:
        raise ConfigError(
            f"缺少环境变量 {name}。本地写进 worker/.env（已 gitignore），"
            f"生产由 Secret Manager 注入。模板见 worker/.env.example。"
        )
    return v


def _int(name, default):
    return int(os.getenv(name, "").strip() or default)


def _bool(name, default):
    v = os.getenv(name, "").strip().lower()
    return default if not v else v in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class AiConfig:
    provider: str
    base_url: str
    api_key: str
    model: str

    timeout_seconds: int
    max_retries: int
    micro_batch_size: int
    max_input_tokens: int
    concurrency: int

    prompt_version: str
    taxonomy_version: str
    schema_version: str

    structured_output: bool
    # 推理档位。gpt-5.6-luna 是推理模型，reasoning token 计费但不出现在输出里
    # （Gate 0 实测：一条 80 输出 token 里 23 个是 reasoning）。批量分类用 low。
    reasoning_effort: str
    # 供应商是否保存请求。**默认 false**：库里是真实富途用户的评论，即使已按 §11.4
    # 去标识，也没有理由让它们多躺在一个我们不控制的日志里。
    store: bool
    # OpenAI 的 `service_tier`（`flex` ＝ Batch 价、同步接口、可能 429）。网关是否透传
    # 未知，由 `scripts/probe_gateway.py` 探明后再在 .env 里开；默认空＝不带这个字段。
    service_tier: str = ""
    grouped_batches: bool = False
    max_payload_bytes: int = 12288
    max_output_tokens: int = 8192
    # 新 provider 上线时默认只补缺失 kind，不替换页面已经在用的历史结论。
    fill_missing_only: bool = False

    def redacted(self):
        """可以安全写进日志与 run 记录的形态。Key 只留尾四位，用于分辨「换过 Key 没有」。"""
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "api_key": f"...{self.api_key[-4:]}" if len(self.api_key) > 4 else "***",
            "prompt_version": self.prompt_version,
            "taxonomy_version": self.taxonomy_version,
            "schema_version": self.schema_version,
            "reasoning_effort": self.reasoning_effort,
            "store": self.store,
        }


def load(_allow_missing_key=False, **overrides):
    """从环境读配置。`overrides` 供测试注入，**不读环境**，所以测试不会因为本机
    `.env` 里恰好配了什么而变绿或变红。

    `_allow_missing_key=True` 给**不发请求**的作业用（`jobs/extract.py` 排队、`--dry-run`）：
    它们要 `model` 与三个版本号算 `input_hash`，但用不到 Key。没配 Key 就不该拦着人排队。
    """
    if overrides:
        base = dict(
            provider="openai_compatible",
            base_url="https://example.invalid/v1",
            api_key="test-key",
            model="test-model",
            timeout_seconds=60,
            max_retries=3,
            micro_batch_size=30,
            max_input_tokens=8000,
            concurrency=4,
            prompt_version="comment-product-v1",
            taxonomy_version="v1",
            schema_version="v1",
            structured_output=True,
            reasoning_effort="low",
            store=False,
        )
        base.update(overrides)
        return AiConfig(**base)

    return AiConfig(
        provider=os.getenv("AI_PRIMARY_PROVIDER", "openai_compatible").strip(),
        base_url=(os.getenv("AI_PRIMARY_BASE_URL", "").strip() if _allow_missing_key
                  else _require("AI_PRIMARY_BASE_URL")).rstrip("/"),
        api_key=os.getenv("AI_PRIMARY_API_KEY", "").strip() if _allow_missing_key
        else _require("AI_PRIMARY_API_KEY"),
        model=_require("AI_PRIMARY_MODEL"),
        timeout_seconds=_int("AI_REQUEST_TIMEOUT_SECONDS", 60),
        max_retries=_int("AI_MAX_RETRIES", 3),
        micro_batch_size=_int("AI_MICRO_BATCH_SIZE", 5),
        max_input_tokens=_int("AI_MAX_INPUT_TOKENS", 8000),
        concurrency=_int("AI_CONCURRENCY", 4),
        # 生产默认 v3：沿用 v2 七维 wire schema，但收紧产品身份与上下文消歧规则。
        # v1/v2 保留给回放与对照实验。
        prompt_version=os.getenv("AI_PROMPT_VERSION", "comment-product-v3").strip(),
        taxonomy_version=os.getenv("AI_TAXONOMY_VERSION", "v2").strip(),
        schema_version=os.getenv("AI_SCHEMA_VERSION", "v2").strip(),
        structured_output=_bool("AI_STRUCTURED_OUTPUT", True),
        reasoning_effort=os.getenv("AI_REASONING_EFFORT", "low").strip(),
        store=_bool("AI_STORE", False),
        service_tier=os.getenv("AI_SERVICE_TIER", "").strip(),
        fill_missing_only=_bool("AI_FILL_MISSING_ONLY", False),
    )
