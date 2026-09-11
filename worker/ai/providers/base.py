"""模型供应商接口 —— 只负责「把一批输入送出去、把结构化结果拿回来」。

## 职责边界（runbook §12）

这一层**不碰库、不重试业务、不判质量**：

- 领取任务、写库、dead-letter → `worker/jobs/annotate.py`
- 证据真伪 → `worker/ai/evidence.py`
- 输出合不合 schema → `worker/ai/schemas.py`

供应商层只做传输层的重试（429/5xx/超时）。把业务重试也塞进来的话，一条「模型给了
合法 JSON 但证据是编的」会在传输层被重试三次 —— 而它每次都会成功返回，重试纯属浪费。

## 为什么异常分两类

`TransientError` 值得退避重试（限流、网关抖动、超时）；`PermanentError` 重试多少次都
一样（401 密钥错、400 schema 不合法、模型名不存在）。不分开的话，一个配置错误会被当成
供应商故障退避重试三轮，最后以「dead-letter」的形态出现在报表上，排查方向全错。
"""


class ProviderError(RuntimeError):
    pass


class TransientError(ProviderError):
    """限流、5xx、超时、连接中断 —— 退避后重试。"""


class PermanentError(ProviderError):
    """认证失败、请求非法、模型不存在 —— 重试无意义，直接失败并把原因带出来。"""


class Completion:
    """一次调用的结果。

    `model` 是**供应商返回的**模型标识，不是我们请求的那个：网关会做别名转发，
    请求 `gpt-5.6-luna` 实际跑的可能是另一个快照。`annotation_runs.model_id` 记这个值，
    否则「换了模型但结果没变」这类问题查不出来。
    """

    __slots__ = ("data", "model", "usage", "raw_text", "response_id")

    def __init__(self, data, model, usage, raw_text=None, response_id=None):
        self.data = data
        self.model = model
        self.usage = usage
        self.raw_text = raw_text
        self.response_id = response_id


class Usage:
    """token 用量。取不到的写 None —— 成本算不出来就该说算不出来（铁律 2），
    不要用 0 占位，那会让成本报表显示「这批没花钱」。"""

    __slots__ = ("input_tokens", "output_tokens", "reasoning_tokens", "cached_tokens")

    def __init__(self, input_tokens=None, output_tokens=None,
                 reasoning_tokens=None, cached_tokens=None):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.reasoning_tokens = reasoning_tokens
        self.cached_tokens = cached_tokens

    def as_dict(self):
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cached_tokens": self.cached_tokens,
        }


class Provider:
    """供应商协议。实现只需要一个方法。"""

    name = "base"

    def complete_json(self, system, user, schema, schema_name):
        """送一次请求，返回 `Completion`，`data` 是已解析的 JSON 对象。

        实现必须保证：请求发出前调用 `redact.assert_clean`；
        结构化输出失败时抛 `SchemaError` 之外的异常由调用方按瞬时/永久分类处理。
        """
        raise NotImplementedError
