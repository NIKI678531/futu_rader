"""学生模型导出（ADR-0021）—— 每个头：HF 权重 → ONNX fp32 → int8 动态量化 → 一致性校验。

    python -m models.export                       # 读 STUDENT_MODEL_DIR，就地写 <head>/onnx/
    python -m models.export --data /tmp/ds        # 用这份对照集做 int8 vs fp32 校验

## 为什么要校验，而不是量化完就用

动态量化把线性层权重压成 int8，CPU 上快 2–3 倍，但不是无损的。校验规则：int8 与 fp32
在对照集上的**预测类**一致 ≥ `AGREEMENT_MIN`（0.99），达标就让 `infer.py` 用 int8；
不达标保留 fp32，并把两边的一致率写进 `export.json` —— 慢一点比错一点好。

## 输出

`<model_dir>/<head>/onnx/model.onnx`（fp32）、`model.int8.onnx`（量化），
`<model_dir>/export.json`：每个头用哪份、一致率、文件大小。`infer.py` 只认这份报告。
"""

import argparse
import json
import logging
import os
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

log = logging.getLogger("worker.models.export")

AGREEMENT_MIN = 0.99


def export_onnx(head_dir, onnx_dir):
    """优先 optimum；没装或版本不合就退到 torch.onnx.export（同一份输入输出名）。"""
    onnx_dir = Path(onnx_dir)
    onnx_dir.mkdir(parents=True, exist_ok=True)
    target = onnx_dir / "model.onnx"
    try:
        from optimum.exporters.onnx import main_export

        main_export(str(head_dir), output=str(onnx_dir), task="text-classification", opset=17)
        if not target.exists():
            found = sorted(onnx_dir.glob("*.onnx"))
            if not found:
                raise RuntimeError("optimum 没有产出 .onnx 文件")
            found[0].rename(target)
        return target, "optimum"
    except Exception as exc:  # noqa: BLE001  版本不合是常态，退化路径必须可用
        log.warning("optimum 导出失败（%s），改用 torch.onnx.export", str(exc)[:160])
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    model = AutoModelForSequenceClassification.from_pretrained(head_dir).eval()
    tok = AutoTokenizer.from_pretrained(head_dir)
    enc = tok(["导出用样本"], return_tensors="pt", padding="max_length", max_length=16, truncation=True)
    names = ["input_ids", "attention_mask"] + (["token_type_ids"] if "token_type_ids" in enc else [])
    dyn = {n: {0: "batch", 1: "seq"} for n in names} | {"logits": {0: "batch"}}
    torch.onnx.export(model, tuple(enc[n] for n in names), str(target), input_names=names,
                      output_names=["logits"], dynamic_axes=dyn, opset_version=17, dynamo=False)
    return target, "torch"


def quantize(fp32_path, int8_path):
    from onnxruntime.quantization import QuantType, quantize_dynamic

    quantize_dynamic(str(fp32_path), str(int8_path), weight_type=QuantType.QInt8)
    return int8_path


def _predict(session, tokenizer, texts, batch_size=64):
    input_names = {i.name for i in session.get_inputs()}
    out = []
    for i in range(0, len(texts), batch_size):
        enc = tokenizer(texts[i:i + batch_size], truncation=True, max_length=registry.MAX_SEQ_LEN,
                        padding=True, return_tensors="np")
        feed = {k: v.astype(np.int64) for k, v in enc.items() if k in input_names}
        out.append(session.run(None, feed)[0])
    return np.concatenate(out) if out else np.zeros((0, 1))


def agreement(fp32_path, int8_path, tokenizer, texts):
    import onnxruntime as ort

    if not texts:
        return None
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = max(1, os.cpu_count() or 1)
    a = ort.InferenceSession(str(fp32_path), opts, providers=["CPUExecutionProvider"])
    b = ort.InferenceSession(str(int8_path), opts, providers=["CPUExecutionProvider"])
    pa = _predict(a, tokenizer, texts).argmax(axis=1)
    pb = _predict(b, tokenizer, texts).argmax(axis=1)
    return round(float((pa == pb).mean()), 4)


def export(model_dir=None, data_dir=None, *, heads=None, sample=2000):
    from transformers import AutoTokenizer

    model_dir = Path(model_dir or registry.model_dir())
    data_dir = Path(data_dir or registry.dataset_dir())
    cal = json.loads((model_dir / "calibration.json").read_text(encoding="utf-8"))
    texts = []
    hold = data_dir / "holdout.jsonl"
    if hold.exists():
        texts = [r["text"] for r in load_jsonl(hold)][:sample]
    heads = heads or [h for h in list(registry.HEADS) + [registry.ASPECT_HEAD] if (model_dir / h).is_dir()]
    report = {"model": cal.get("model"), "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "agreement_min": AGREEMENT_MIN, "validation_samples": len(texts), "heads": {}}
    for head in heads:
        head_dir = model_dir / head
        t0 = time.time()
        fp32, how = export_onnx(head_dir, head_dir / "onnx")
        int8 = quantize(fp32, head_dir / "onnx" / "model.int8.onnx")
        tok = AutoTokenizer.from_pretrained(head_dir)
        agree = agreement(fp32, int8, tok, texts)
        use = "int8" if (agree is not None and agree >= AGREEMENT_MIN) else "fp32"
        report["heads"][head] = {
            "exporter": how, "fp32_bytes": fp32.stat().st_size, "int8_bytes": int8.stat().st_size,
            "int8_vs_fp32_agreement": agree, "use": use, "seconds": round(time.time() - t0, 1),
            "note": None if use == "int8" else
            ("没有对照集，无法校验，保留 fp32" if agree is None else f"int8 一致率 {agree} < {AGREEMENT_MIN}，保留 fp32"),
        }
        log.info("%s：%s 导出，int8 一致率 %s → 用 %s", head, how, agree, use)
    (model_dir / "export.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="学生模型 ONNX 导出与 int8 量化")
    ap.add_argument("--model-dir")
    ap.add_argument("--data", help="对照集目录（用 holdout.jsonl 校验 int8）")
    ap.add_argument("--sample", type=int, default=2000, help="校验最多用多少条对照样本")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s %(message)s")
    print(json.dumps(export(args.model_dir, args.data, sample=args.sample), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
