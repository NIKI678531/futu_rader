import json
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class BatchPolicy:
    size: int = 5
    max_input_tokens: int = 8000
    max_payload_bytes: int = 12288

    def __post_init__(self):
        if min(self.size, self.max_input_tokens, self.max_payload_bytes) < 1:
            raise ValueError("Batch limits must be positive")


def estimate_tokens(text):
    return math.ceil(sum(1.6 if ord(char) > 127 else 0.34 for char in text) * 1.15)


def measure(payloads, system, render, schema):
    user = render(payloads)
    total = system + user + json.dumps(schema, ensure_ascii=False)
    return {"inputTokensEstimate": estimate_tokens(total), "payloadBytes": len(user.encode("utf-8"))}


def pack_items(items, *, key_of, payload_of, system, render, schema, policy):
    groups = {}
    for item in items:
        groups.setdefault(key_of(item), []).append(item)
    batches, oversized = [], []
    for group in groups.values():
        batch = []
        for item in group:
            proposed = batch + [item]
            usage = measure([payload_of(row) for row in proposed], system, render, schema)
            fits = (len(proposed) <= policy.size
                    and usage["inputTokensEstimate"] <= policy.max_input_tokens
                    and usage["payloadBytes"] <= policy.max_payload_bytes)
            if not fits and batch:
                batches.append(batch)
                batch = []
                usage = measure([payload_of(item)], system, render, schema)
            if usage["inputTokensEstimate"] > policy.max_input_tokens or usage["payloadBytes"] > policy.max_payload_bytes:
                oversized.append(item)
            else:
                batch.append(item)
        if batch:
            batches.append(batch)
    return batches, oversized