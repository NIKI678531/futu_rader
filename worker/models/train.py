"""学生模型训练（ADR-0021）—— 两头分别微调，对照集温度缩放，写 `calibration.json`。

    python -m models.train                              # mengzi-fin，读默认训练集目录
    python -m models.train --model rbt3 --epochs 3
    python -m models.train --tiny --data /tmp/ds --out /tmp/student   # 离线测试：随机极小 BERT

## 为什么两头分开训

相关性与态度不是同一个问题的两个输出：态度只在「相关」时有定义（schema 的交叉约束）。
合成一个 9 类头会让「无关」与「相关且中性」争同一批参数；分开训，态度头只见相关样本，
它的概率才是「给定相关，态度是什么」的概率 —— `calibrated_confidence` 落库时要的正是这个。

## 温度缩放

BERT 微调后的 softmax 概率普遍过于自信（Guo et al. 2017）。`calibrated_confidence` 一列的
定义是「校准后的概率」（`radar_db/schema.py`），直接落 softmax 值等于把模型自报当概率 ——
正是 ADR-0017 §4 禁止的那件事。所以在**对照集**上按 NLL 最小拟合一个标量温度 T，推理时
softmax(logits/T)。T 与对照集一致率、混淆矩阵一起写进 `calibration.json`；`infer.py` 读它。

对照集一致率是「与 Luna 的一致率」，不是准确率 —— 与 Luna 一致的错误它同样学会了。
这个数字只用来定路由阈值，不进页面（页面的量尺是 400 条人工核对集，见 evaluate_gold）。

## CPU

12 层 BERT、seq 128、batch 32 在 4 核 CPU 上约 1.5 秒一步；5 万条两轮约 1–2 小时。
`--tiny` 用随机初始化的 1 层 32 维 BERT 与字符级词表，几秒跑完，只验证管线。
"""

import argparse
import json
import logging
import math
import os
import random
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from models import registry  # noqa: E402
from models.dataset import load_jsonl  # noqa: E402

log = logging.getLogger("worker.models.train")

SPECIAL_TOKENS = ("[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]")


# ── 分词器与模型 ──────────────────────────────────────────────────────────


def build_tokenizer(spec, out_dir, texts=None):
    """真模型：按 revision 拉 HF 分词器。`tiny`：从训练文本造字符级词表（离线、可复现）。"""
    from transformers import AutoTokenizer, BertTokenizerFast

    if spec["name"] != "tiny":
        return AutoTokenizer.from_pretrained(spec["hf_id"], revision=spec["revision"])
    chars = sorted({ch for t in (texts or []) for ch in t if not ch.isspace()})
    vocab_path = Path(out_dir) / "tiny-vocab.txt"
    vocab_path.parent.mkdir(parents=True, exist_ok=True)
    vocab_path.write_text("\n".join(list(SPECIAL_TOKENS) + chars) + "\n", encoding="utf-8")
    return BertTokenizerFast(vocab_file=str(vocab_path), do_lower_case=False, tokenize_chinese_chars=True)


def build_model(spec, tokenizer, num_labels, *, multi_label=False):
    from transformers import AutoModelForSequenceClassification, BertConfig, BertForSequenceClassification

    problem = "multi_label_classification" if multi_label else "single_label_classification"
    if spec["name"] == "tiny":
        cfg = BertConfig(vocab_size=len(tokenizer), hidden_size=32, num_hidden_layers=1, num_attention_heads=2,
                         intermediate_size=64, max_position_embeddings=registry.MAX_SEQ_LEN + 2,
                         num_labels=num_labels, problem_type=problem)
        return BertForSequenceClassification(cfg)
    return AutoModelForSequenceClassification.from_pretrained(
        spec["hf_id"], revision=spec["revision"], num_labels=num_labels, problem_type=problem)


def encode(tokenizer, texts):
    return tokenizer(list(texts), truncation=True, max_length=registry.MAX_SEQ_LEN, padding=True, return_tensors="pt")


# ── 校准（纯 numpy，infer 不依赖 torch） ─────────────────────────────────────


def softmax(logits, temperature=1.0):
    z = np.asarray(logits, dtype=np.float64) / float(temperature)
    z = z - z.max(axis=-1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=-1, keepdims=True)


def nll(logits, labels, temperature):
    p = softmax(logits, temperature)
    idx = np.arange(len(labels))
    return float(-np.log(np.clip(p[idx, labels], 1e-12, 1.0)).mean())


def fit_temperature(logits, labels):
    """对照集上按 NLL 最小找标量温度。先粗网格再黄金分割，单调凸问题够用。"""
    logits = np.asarray(logits, dtype=np.float64)
    labels = np.asarray(labels)
    if len(labels) == 0:
        return 1.0
    grid = [0.25 * k for k in range(1, 41)]  # 0.25 … 10
    best = min(grid, key=lambda t: nll(logits, labels, t))
    lo, hi = max(0.05, best - 0.25), best + 0.25
    phi = (math.sqrt(5) - 1) / 2
    a, b = hi - phi * (hi - lo), lo + phi * (hi - lo)
    fa, fb = nll(logits, labels, a), nll(logits, labels, b)
    for _ in range(40):
        if fa < fb:
            hi, b, fb = b, a, fa
            a = hi - phi * (hi - lo)
            fa = nll(logits, labels, a)
        else:
            lo, a, fa = a, b, fb
            b = lo + phi * (hi - lo)
            fb = nll(logits, labels, b)
    return round((lo + hi) / 2, 4)


def confusion(y_true, y_pred, n):
    m = [[0] * n for _ in range(n)]
    for t, p in zip(y_true, y_pred):
        m[int(t)][int(p)] += 1
    return m


# ── 训练一个头 ──────────────────────────────────────────────────────────────


def _rows_for_head(head, rows):
    if head == "attitude":
        return [r for r in rows if r.get("attitude")]
    return [r for r in rows if r.get("relevance")]


def _label_index(head, row):
    return registry.LABELS[head].index(row[head])


def predict_logits(model, tokenizer, texts, batch_size=64):
    import torch

    model.eval()
    out = []
    with torch.no_grad():
        for i in range(0, len(texts), batch_size):
            enc = encode(tokenizer, texts[i:i + batch_size])
            out.append(model(**enc).logits.float().cpu().numpy())
    return np.concatenate(out) if out else np.zeros((0, model.config.num_labels))


def train_head(head, train_rows, hold_rows, spec, tokenizer, out_dir, *, epochs=2, batch_size=32, lr=3e-5,
               seed=42, max_steps=None):
    import torch

    torch.manual_seed(seed)
    random.seed(seed)
    labels = registry.LABELS[head]
    tr = _rows_for_head(head, train_rows)
    ho = _rows_for_head(head, hold_rows)
    if not tr:
        raise SystemExit(f"{head} 头没有训练样本")
    model = build_model(spec, tokenizer, len(labels))
    model.train()
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    steps_per_epoch = math.ceil(len(tr) / batch_size)
    total = steps_per_epoch * epochs if max_steps is None else min(max_steps, steps_per_epoch * epochs)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: max(0.0, 1 - s / max(total, 1)))
    t0, step = time.time(), 0
    for ep in range(epochs):
        order = list(range(len(tr)))
        random.shuffle(order)
        for i in range(0, len(order), batch_size):
            batch = [tr[j] for j in order[i:i + batch_size]]
            enc = encode(tokenizer, [r["text"] for r in batch])
            y = torch.tensor([_label_index(head, r) for r in batch])
            loss = model(**enc, labels=y).loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            opt.zero_grad()
            step += 1
            if step % 50 == 0:
                log.info("%s epoch %d step %d/%d loss %.4f (%.0fs)", head, ep + 1, step, total, loss.item(), time.time() - t0)
            if max_steps is not None and step >= max_steps:
                break
        if max_steps is not None and step >= max_steps:
            break

    head_dir = Path(out_dir) / head
    head_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(head_dir)
    tokenizer.save_pretrained(head_dir)

    report = {"labels": list(labels), "n_train": len(tr), "n_holdout": len(ho), "steps": step,
              "seconds": round(time.time() - t0, 1), "temperature": 1.0, "agreement": None,
              "confusion": None, "nll_before": None, "nll_after": None}
    if ho:
        logits = predict_logits(model, tokenizer, [r["text"] for r in ho])
        y = np.array([_label_index(head, r) for r in ho])
        pred = logits.argmax(axis=1)
        T = fit_temperature(logits, y)
        report.update(temperature=T, agreement=round(float((pred == y).mean()), 4),
                      confusion=confusion(y, pred, len(labels)),
                      nll_before=round(nll(logits, y, 1.0), 4), nll_after=round(nll(logits, y, T), 4))
    return report


def train_aspect_head(train_rows, hold_rows, spec, tokenizer, out_dir, *, epochs=2, batch_size=32, lr=3e-5,
                      max_steps=None):
    """aspect 多标签头（可选）。只在正样本足够时训；报告 micro-F1，不做温度缩放。"""
    import torch
    from ai.schemas import ASPECTS

    tr = [r for r in train_rows if r.get("relevance") == "relevant"]
    ho = [r for r in hold_rows if r.get("relevance") == "relevant"]
    model = build_model(spec, tokenizer, len(ASPECTS), multi_label=True)
    model.train()
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)

    def vec(r):
        return [1.0 if a in (r.get("aspects") or []) else 0.0 for a in ASPECTS]

    step, t0 = 0, time.time()
    for _ in range(epochs):
        order = list(range(len(tr)))
        random.shuffle(order)
        for i in range(0, len(order), batch_size):
            batch = [tr[j] for j in order[i:i + batch_size]]
            enc = encode(tokenizer, [r["text"] for r in batch])
            y = torch.tensor([vec(r) for r in batch])
            loss = model(**enc, labels=y).loss
            loss.backward()
            opt.step()
            opt.zero_grad()
            step += 1
            if max_steps is not None and step >= max_steps:
                break
        if max_steps is not None and step >= max_steps:
            break
    head_dir = Path(out_dir) / registry.ASPECT_HEAD
    head_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(head_dir)
    tokenizer.save_pretrained(head_dir)
    report = {"labels": list(ASPECTS), "n_train": len(tr), "n_holdout": len(ho), "steps": step,
              "seconds": round(time.time() - t0, 1), "micro_f1": None}
    if ho:
        logits = predict_logits(model, tokenizer, [r["text"] for r in ho])
        pred = (1 / (1 + np.exp(-logits))) >= 0.5
        truth = np.array([vec(r) for r in ho]) >= 0.5
        tp = int((pred & truth).sum())
        fp = int((pred & ~truth).sum())
        fn = int((~pred & truth).sum())
        report["micro_f1"] = round(2 * tp / max(2 * tp + fp + fn, 1), 4)
    return report


# ── 入口 ─────────────────────────────────────────────────────────────────────


def train(data_dir=None, out_dir=None, *, model=None, tiny=False, epochs=2, batch_size=32, lr=3e-5,
          max_train=None, max_steps=None, aspect=None, threads=None):
    import torch

    if threads:
        torch.set_num_threads(int(threads))
    data_dir = Path(data_dir or registry.dataset_dir())
    out_dir = Path(out_dir or registry.model_dir())
    spec = registry.spec("tiny" if tiny else model)
    train_rows = load_jsonl(data_dir / "train.jsonl")
    hold_rows = load_jsonl(data_dir / "holdout.jsonl")
    meta = json.loads((data_dir / "meta.json").read_text(encoding="utf-8")) if (data_dir / "meta.json").exists() else {}
    if max_train:
        random.Random(42).shuffle(train_rows)
        train_rows = train_rows[:max_train]
    out_dir.mkdir(parents=True, exist_ok=True)
    tokenizer = build_tokenizer(spec, out_dir, texts=[r["text"] for r in train_rows + hold_rows])

    heads = {}
    for head in registry.HEADS:
        heads[head] = train_head(head, train_rows, hold_rows, spec, tokenizer, out_dir, epochs=epochs,
                                 batch_size=batch_size, lr=lr, max_steps=max_steps)
        log.info("%s：对照集一致率 %s，T=%s", head, heads[head]["agreement"], heads[head]["temperature"])
    aspect_on = meta.get("aspect_head", False) if aspect is None else aspect
    aspect_report = None
    if aspect_on:
        aspect_report = train_aspect_head(train_rows, hold_rows, spec, tokenizer, out_dir, epochs=epochs,
                                          batch_size=batch_size, lr=lr, max_steps=max_steps)

    calibration = {
        "version": registry.STUDENT_VERSION,
        "model": {"name": spec["name"], "hf_id": spec["hf_id"], "revision": spec["revision"],
                  "model_id": f"{spec['hf_id']}@{spec['revision']}", "tiny": spec["name"] == "tiny"},
        "seq_len": registry.MAX_SEQ_LEN, "epochs": epochs, "batch_size": batch_size, "lr": lr,
        "n_train": len(train_rows), "n_holdout": len(hold_rows),
        "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "heads": heads, "aspect": aspect_report,
        "note": "agreement 是与 Luna 的一致率，不是准确率；只用于定路由阈值，不进页面。",
    }
    (out_dir / "calibration.json").write_text(json.dumps(calibration, ensure_ascii=False, indent=1), encoding="utf-8")
    return calibration


def main(argv=None):
    ap = argparse.ArgumentParser(description="学生模型训练")
    ap.add_argument("--data", help="训练集目录（models.dataset 的输出）")
    ap.add_argument("--out", help="权重输出目录（默认 STUDENT_MODEL_DIR）")
    ap.add_argument("--model", default=registry.DEFAULT_MODEL, choices=[m for m in registry.MODELS if m != "tiny"])
    ap.add_argument("--tiny", action="store_true", help="随机初始化的极小 BERT，只测管线")
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=3e-5)
    ap.add_argument("--max-train", type=int, help="只用前 N 条训练样本（冒烟）")
    ap.add_argument("--max-steps", type=int, help="每头最多训这么多步（冒烟）")
    ap.add_argument("--aspect", action="store_true", help="强制训 aspect 头（默认看 meta.json）")
    ap.add_argument("--threads", type=int, help="torch 线程数（默认 torch 自定）")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s %(message)s")
    cal = train(args.data, args.out, model=args.model, tiny=args.tiny, epochs=args.epochs,
                batch_size=args.batch_size, lr=args.lr, max_train=args.max_train, max_steps=args.max_steps,
                aspect=True if args.aspect else None, threads=args.threads)
    print(json.dumps({k: v for k, v in cal.items() if k != "heads"} | {
        "heads": {h: {"agreement": r["agreement"], "temperature": r["temperature"]} for h, r in cal["heads"].items()}
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
