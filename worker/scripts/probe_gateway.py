"""探测 CSOP 网关对三种降本手段的支持（ADR-0020 步骤 10）。

    cd worker && .venv/Scripts/python -m scripts.probe_gateway
    cd worker && .venv/Scripts/python -m scripts.probe_gateway --burst 20

只发**脱敏假样本**（一句自编评论），每项探测最多一两次请求。结论请抄进 runbook §6.4。

探的四件事：

1. `service_tier: "flex"` —— OpenAI 的 Flex 处理（Batch 价、同步接口、可能 429）。
   网关透传 ⇒ 200 且响应里 `service_tier` 回显；不透传 ⇒ 400「Unknown parameter」。
2. `POST /batches` —— 异步 Batch API（-50%、24h 窗口）。只试 `GET /batches`（列表）判端点是否存在，
   不真建批：建批要先上传文件，且一旦建了就会计费。
3. `usage.input_tokens_details.cached_tokens` —— 系统提示 ≥1,024 token 时第二次请求
   是否命中缓存。连发两次同一系统提示，比对 `cached_tokens`。
4. `--burst N` —— 连发 N 次看有没有 429 与 `Retry-After`（限流窗口）。默认不做，做了会花 N 次的钱。

**不会**把 Key 打印出来；报告里只有 HTTP 码、字段有无与用量数字。
"""

import argparse
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai import config, redact, schemas  # noqa: E402
from ai.prompts import get as get_prompt  # noqa: E402

FAKE = [
    redact.comment_payload(
        "comment:0|product:0000",
        {"code": "0000", "name": "探测用假产品ETF", "aliases": ["假产品"]},
        "这只ETF点差太大，来回一趟就蚀掉不少",
        post_title="探测",
    )
]


def _headers(cfg):
    return {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}


def _body(cfg, prompt, extra=None):
    body = {
        "model": cfg.model,
        "input": [
            {"role": "system", "content": prompt.SYSTEM},
            {"role": "user", "content": prompt.user_message(FAKE)},
        ],
        "store": False,
        "text": {"format": {"type": "json_schema", "name": "probe", "strict": True,
                            "schema": schemas.batch_json_schema("comment_product", "v2")}},
        "reasoning": {"effort": "low"},
    }
    body.update(extra or {})
    redact.assert_clean(body)
    return body


def _post(cfg, path, body, timeout=120):
    t0 = time.time()
    r = requests.post(f"{cfg.base_url}{path}", headers=_headers(cfg), json=body, timeout=timeout)
    return r, time.time() - t0


def _usage(r):
    try:
        u = r.json().get("usage") or {}
    except ValueError:
        return {}
    return {
        "input": u.get("input_tokens"), "output": u.get("output_tokens"),
        "reasoning": (u.get("output_tokens_details") or {}).get("reasoning_tokens"),
        "cached": (u.get("input_tokens_details") or {}).get("cached_tokens"),
    }


def _err(r):
    try:
        e = r.json().get("error") or {}
        return (e.get("message") or json.dumps(e, ensure_ascii=False))[:200]
    except ValueError:
        return (r.text or "")[:200]


def main(argv=None):
    ap = argparse.ArgumentParser(description="探测网关：flex / batches / 缓存 / 限流")
    ap.add_argument("--burst", type=int, default=0, help="连发 N 次探限流（会花 N 次的钱）")
    args = ap.parse_args(argv)

    cfg = config.load()
    prompt = get_prompt("comment_product")
    report = {"base_url": cfg.base_url, "model": cfg.model,
              "system_prompt_chars": len(prompt.SYSTEM)}

    # 0. 基线：一次普通请求，确认 v2 schema 被网关接受。
    r, dt = _post(cfg, "/responses", _body(cfg, prompt))
    report["baseline"] = {"status": r.status_code, "seconds": round(dt, 1),
                          "usage": _usage(r), "error": None if r.ok else _err(r)}
    if not r.ok:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        print("\n基线请求失败，后续探测无意义；先看 error。")
        return 1

    # 1. flex
    r, dt = _post(cfg, "/responses", _body(cfg, prompt, {"service_tier": "flex"}))
    echoed = None
    try:
        echoed = r.json().get("service_tier")
    except ValueError:
        pass
    report["flex"] = {"status": r.status_code, "seconds": round(dt, 1), "service_tier_echoed": echoed,
                      "usage": _usage(r), "error": None if r.ok else _err(r),
                      "verdict": ("透传" if r.ok and echoed == "flex" else
                                  "接受但未回显（存疑）" if r.ok else "不支持")}

    # 2. batches：只 GET 列表
    rb = requests.get(f"{cfg.base_url}/batches?limit=1", headers=_headers(cfg), timeout=30)
    report["batches"] = {"status": rb.status_code, "error": None if rb.ok else _err(rb),
                         "verdict": "端点存在" if rb.ok else "不支持或未开通"}

    # 3. 缓存：同一系统提示连发两次，看第二次 cached_tokens
    r1, _ = _post(cfg, "/responses", _body(cfg, prompt))
    r2, _ = _post(cfg, "/responses", _body(cfg, prompt))
    u1, u2 = _usage(r1), _usage(r2)
    report["prompt_cache"] = {
        "first_cached": u1.get("cached"), "second_cached": u2.get("cached"),
        "verdict": ("命中" if (u2.get("cached") or 0) > 0 else
                    "字段存在但未命中" if u2.get("cached") is not None else "网关不返回 cached_tokens"),
    }

    # 4. 限流
    if args.burst:
        codes, retry_after = [], []
        for _ in range(args.burst):
            r, _dt = _post(cfg, "/responses", _body(cfg, prompt), timeout=60)
            codes.append(r.status_code)
            if r.status_code == 429:
                retry_after.append(r.headers.get("Retry-After"))
        report["burst"] = {"n": args.burst, "status_codes": codes,
                           "n_429": codes.count(429), "retry_after_headers": retry_after}

    print(json.dumps(report, ensure_ascii=False, indent=1))
    print("\n把上面 JSON 抄进 docs/ai-data-integration-runbook.md §6.4；"
          "flex 透传就在 worker/.env 里设 AI_SERVICE_TIER=flex。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
