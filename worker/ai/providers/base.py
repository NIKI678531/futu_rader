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


import threading
import time
import signal
import hashlib
from collections import Counter, deque
from contextlib import contextmanager


class ProviderError(RuntimeError):
    pass


class TransientError(ProviderError):
    """限流、5xx、超时、连接中断 —— 退避后重试。"""


class TruncatedOutput(TransientError):
    pass


class PermanentError(ProviderError):
    """认证失败、请求非法、模型不存在 —— 重试无意义，直接失败并把原因带出来。"""


class RunStopped(PermanentError):
    pass


class RunControl:
    def __init__(self, max_http_requests):
        if max_http_requests < 1:
            raise ValueError("max_http_requests must be positive")
        self.limit = max_http_requests
        self.stop_event = threading.Event()
        self.reason = None
        self.attempts = 0
        self.retries = 0
        self.rate_limits = 0
        self.completed = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.usage_known = True
        self._cooldown_until = 0.0
        self._lock = threading.RLock()
        self.before_request = None
        # Optional persistent admission hook. It is called for every real HTTP
        # attempt (including transport retries) after the local cap check but
        # before the in-memory counter advances. A database-backed hook lets
        # multiple Airflow retries share one hard daily budget.
        self.reserve_hook = None
        self.batch_sizes = Counter()
        self.recent_batches = deque(maxlen=20)
        self.latencies = deque(maxlen=1000)
        self.started = time.monotonic()

    def stop(self, reason="cancelled"):
        with self._lock:
            if self.reason is None:
                self.reason = reason
            self.stop_event.set()

    def check(self):
        if self.stop_event.is_set():
            raise RunStopped(self.reason or "cancelled")

    @contextmanager
    def interruptible(self):
        previous = signal.signal(signal.SIGINT, lambda *_args: self.stop("cancelled"))
        try:
            yield self
        finally:
            signal.signal(signal.SIGINT, previous)

    def reserve(self, retry=False):
        while True:
            self.check()
            if self.before_request is not None:
                self.before_request()
                self.check()
            with self._lock:
                self.check()
                delay = self._cooldown_until - time.monotonic()
                if delay <= 0:
                    if self.attempts >= self.limit:
                        self.reason = "budget_exhausted"
                        self.stop_event.set()
                        raise RunStopped(self.reason)
                    if self.reserve_hook is not None:
                        try:
                            self.reserve_hook()
                        except RunStopped as exc:
                            self.reason = str(exc) or "budget_exhausted"
                            self.stop_event.set()
                            raise
                    self.attempts += 1
                    self.retries += int(retry)
                    return
            self.stop_event.wait(delay)

    def cooldown(self, seconds, rate_limited=False):
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + seconds)
            self.rate_limits += int(rate_limited)

    def record(self, usage):
        with self._lock:
            self.completed += 1
            if usage.input_tokens is None or usage.output_tokens is None:
                self.usage_known = False
            else:
                self.input_tokens += usage.input_tokens
                self.output_tokens += usage.output_tokens

    def record_batch(self, item_ids, response_id):
        with self._lock:
            self.batch_sizes[len(item_ids)] += 1
            self.recent_batches.append({
                "size": len(item_ids), "responseId": response_id,
                "fingerprint": hashlib.sha256("\n".join(sorted(item_ids)).encode()).hexdigest(),
            })

    def latency(self, seconds):
        with self._lock:
            self.latencies.append(seconds)

    def snapshot(self):
        with self._lock:
            latencies = sorted(self.latencies)
            return {"maxHttpRequests": self.limit, "httpAttempts": self.attempts,
                    "httpRetries": self.retries, "rateLimits": self.rate_limits,
                    "responses": self.completed, "stopReason": self.reason,
                    "inputTokens": self.input_tokens if self.usage_known else None,
                    "outputTokens": self.output_tokens if self.usage_known else None,
                    "returnedBatchSizes": dict(self.batch_sizes), "recentBatches": list(self.recent_batches),
                    "elapsedSeconds": round(time.monotonic() - self.started, 2),
                    "httpP50Seconds": latencies[len(latencies) // 2] if latencies else None,
                    "httpP95Seconds": latencies[min(len(latencies) - 1, int(len(latencies) * .95))] if latencies else None}


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
