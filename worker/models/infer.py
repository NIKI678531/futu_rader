"""学生模型推理（ADR-0021）—— onnxruntime 批推理，输出经温度缩放的各头概率。

`Student(model_dir)` 读 `calibration.json`（温度、标签顺序）与 `export.json`（每头用 int8
还是 fp32），每头一个 `InferenceSession`，`intra_op_num_threads=CPU 核数`。没有 `export.json`
（还没跑 `models.export`）时退到 torch 权重 —— 慢 3–5 倍，但管线不断。

`predict(texts)` 返回每条 `{head: {"probs": {label: p}, "label": 最大类, "max": p, "margin": 前两类差}}`。
概率已经过温度缩放：`calibrated_confidence` 落库时直接取 `max`。

不依赖 torch（除非退化路径）、不碰库：谁调它谁负责造文本（`dataset.payload_text`）与写库。
"""

import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from models import registry  # noqa: E402

BATCH_SIZE = 64


class StudentUnavailable(RuntimeError):
    """本地没有可用的学生模型：目录不存在、没训过、或依赖没装。调用方决定是放行到 Luna 还是停。"""


def _softmax(logits, temperature):
    z = np.asarray(logits, dtype=np.float64) / float(temperature or 1.0)
    z = z - z.max(axis=-1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=-1, keepdims=True)


class _OnnxHead:
    def __init__(self, path, threads):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.session.get_inputs()}

    def logits(self, enc):
        feed = {k: np.asarray(v, dtype=np.int64) for k, v in enc.items() if k in self.inputs}
        return self.session.run(None, feed)[0]


class _TorchHead:
    def __init__(self, head_dir, threads):
        import torch
        from transformers import AutoModelForSequenceClassification

        torch.set_num_threads(threads)
        self.model = AutoModelForSequenceClassification.from_pretrained(head_dir).eval()

    def logits(self, enc):
        import torch

        with torch.no_grad():
            return self.model(**{k: torch.as_tensor(v) for k, v in enc.items()}).logits.float().numpy()


class Student:
    def __init__(self, model_dir=None, *, threads=None, batch_size=BATCH_SIZE):
        self.model_dir = Path(model_dir or registry.model_dir())
        cal_path = self.model_dir / "calibration.json"
        if not cal_path.exists():
            raise StudentUnavailable(f"没有学生模型：{cal_path} 不存在（先跑 python -m models.train）")
        self.calibration = json.loads(cal_path.read_text(encoding="utf-8"))
        export = self.model_dir / "export.json"
        self.export = json.loads(export.read_text(encoding="utf-8")) if export.exists() else None
        self.threads = int(threads or os.cpu_count() or 1)
        self.batch_size = batch_size
        self.model_id = (self.calibration.get("model") or {}).get("model_id") or registry.model_id_string()
        self.backend = {}
        self.heads = {}
        self.temperature = {}
        self.labels = {}
        try:
            from transformers import AutoTokenizer
        except ImportError as exc:
            raise StudentUnavailable(f"transformers 未安装：{exc}") from exc
        first = None
        for head in registry.HEADS:
            info = (self.calibration.get("heads") or {}).get(head)
            head_dir = self.model_dir / head
            if info is None or not head_dir.is_dir():
                raise StudentUnavailable(f"学生模型缺 {head} 头：{head_dir}")
            self.temperature[head] = float(info.get("temperature") or 1.0)
            self.labels[head] = list(info.get("labels") or registry.LABELS[head])
            self.heads[head] = self._load_head(head, head_dir)
            first = first or head_dir
        self.tokenizer = AutoTokenizer.from_pretrained(first)

    def _load_head(self, head, head_dir):
        use = ((self.export or {}).get("heads") or {}).get(head, {}).get("use")
        onnx_dir = head_dir / "onnx"
        if use == "int8" and (onnx_dir / "model.int8.onnx").exists():
            self.backend[head] = "onnx-int8"
            return _OnnxHead(onnx_dir / "model.int8.onnx", self.threads)
        if (onnx_dir / "model.onnx").exists():
            self.backend[head] = "onnx-fp32"
            return _OnnxHead(onnx_dir / "model.onnx", self.threads)
        try:
            self.backend[head] = "torch"
            return _TorchHead(head_dir, self.threads)
        except ImportError as exc:
            raise StudentUnavailable(f"{head} 头既没有 ONNX 也装不上 torch：{exc}") from exc

    def predict(self, texts):
        texts = list(texts)
        out = [dict() for _ in texts]
        if not texts:
            return out
        for i in range(0, len(texts), self.batch_size):
            chunk = texts[i:i + self.batch_size]
            enc = self.tokenizer(chunk, truncation=True, max_length=registry.MAX_SEQ_LEN, padding=True,
                                 return_tensors="np")
            enc = {k: v for k, v in enc.items()}
            for head, runner in self.heads.items():
                probs = _softmax(runner.logits(enc), self.temperature[head])
                labels = self.labels[head]
                for j, p in enumerate(probs):
                    order = np.argsort(-p)
                    out[i + j][head] = {
                        "probs": {labels[k]: round(float(p[k]), 6) for k in range(len(labels))},
                        "label": labels[int(order[0])],
                        "max": round(float(p[order[0]]), 6),
                        "margin": round(float(p[order[0]] - p[order[1]]), 6) if len(p) > 1 else 1.0,
                    }
        return out
