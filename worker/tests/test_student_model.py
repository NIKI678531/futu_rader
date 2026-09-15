"""学生模型管线（ADR-0021）：`--tiny` 训练 → ONNX 导出 → onnxruntime 推理。需要 torch／transformers／onnxruntime，装不上就跳过。

tiny 是随机初始化的极小 BERT，这里验证的是**管线**（文件形状、概率和为 1、温度缩放生效、
int8 与 fp32 校验逻辑），不是任何准确率。
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

torch = pytest.importorskip("torch", reason="学生模型测试需要 torch")
transformers = pytest.importorskip("transformers", reason="学生模型测试需要 transformers")

from models import dataset, infer, registry, train  # noqa: E402

TEXTS = [
    ("这只ETF点差太大，来回一趟就蚀掉不少", "relevant", "negative"),
    ("费率同类最低，长期持有", "relevant", "positive"),
    ("请问几时派息", "relevant", "neutral"),
    ("恒指要崩了", "irrelevant", None),
    ("有", "needs_context", None),
    ("跟踪误差挺小的，拿着放心", "relevant", "positive"),
    ("溢价太高了先不追", "relevant", "negative"),
    ("腾讯业绩不错", "irrelevant", None),
]


def _write_dataset(out):
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(6):
        for j, (t, rel, att) in enumerate(TEXTS):
            payload = {"comment": f"{t}{i}", "product": {"code": "3033", "name": "恒生科技指数ETF", "aliases": ["恒科ETF"]},
                       "parent_comment": None, "post_title": "恒科", "post_context": None}
            rows.append({"unit": [i * 100 + j, "3033"], "group": f"3033|2026-W{30 + i}", "text": dataset.payload_text(payload),
                         "relevance": rel, "attitude": att, "aspects": []})
    train_rows, hold = rows[:-len(TEXTS)], rows[-len(TEXTS):]
    for name, rs in (("train", train_rows), ("holdout", hold)):
        with open(out / f"{name}.jsonl", "w", encoding="utf-8") as fh:
            for r in rs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out / "meta.json").write_text(json.dumps({"aspect_head": False}), encoding="utf-8")
    return train_rows, hold


@pytest.fixture(scope="module")
def tiny_model(tmp_path_factory):
    root = tmp_path_factory.mktemp("student")
    ds, out = root / "ds", root / "model"
    _write_dataset(ds)
    cal = train.train(ds, out, tiny=True, epochs=1, batch_size=8, threads=2)
    return ds, out, cal


def test_tiny_train_writes_calibration(tiny_model):
    ds, out, cal = tiny_model
    assert (out / "calibration.json").exists()
    assert cal["model"]["tiny"] is True and cal["model"]["model_id"] == "tiny-random-bert@none"
    for head in registry.HEADS:
        h = cal["heads"][head]
        assert (out / head / "config.json").exists()
        assert h["temperature"] > 0 and h["labels"] == list(registry.LABELS[head])
        assert 0.0 <= h["agreement"] <= 1.0
        cm = h["confusion"]
        assert len(cm) == len(registry.LABELS[head]) and all(len(r) == len(registry.LABELS[head]) for r in cm)
    assert "不是准确率" in cal["note"]
    # 态度头只见相关样本
    assert cal["heads"]["attitude"]["n_holdout"] == 5 and cal["heads"]["relevance"]["n_holdout"] == 8


def test_infer_probabilities_sum_to_one_torch_fallback(tiny_model):
    ds, out, _ = tiny_model
    st = infer.Student(out, threads=2, batch_size=3)
    assert set(st.backend.values()) == {"torch"}  # 还没导出 ⇒ 退到 torch
    preds = st.predict([t for t, _, _ in TEXTS])
    assert len(preds) == len(TEXTS)
    for p in preds:
        for head in registry.HEADS:
            probs = p[head]["probs"]
            assert set(probs) == set(registry.LABELS[head])
            assert sum(probs.values()) == pytest.approx(1.0, abs=1e-4)
            assert p[head]["label"] in probs and p[head]["max"] == pytest.approx(max(probs.values()), abs=1e-6)
            assert 0 <= p[head]["margin"] <= 1
    assert st.predict([]) == []


def test_export_int8_and_onnx_inference(tiny_model):
    ort = pytest.importorskip("onnxruntime", reason="导出测试需要 onnxruntime")
    from models import export

    ds, out, cal = tiny_model
    report = export.export(out, ds, sample=8)
    assert set(report["heads"]) == set(registry.HEADS)
    for head, h in report["heads"].items():
        assert (out / head / "onnx" / "model.onnx").exists()
        assert (out / head / "onnx" / "model.int8.onnx").exists()
        assert h["use"] in ("int8", "fp32")
        if h["use"] == "fp32":
            assert h["note"]  # 说明为什么保留 fp32
        else:
            assert h["int8_vs_fp32_agreement"] >= export.AGREEMENT_MIN
    assert (out / "export.json").exists()

    st = infer.Student(out, threads=2)
    assert all(b.startswith("onnx") for b in st.backend.values())
    preds = st.predict([t for t, _, _ in TEXTS])
    for p in preds:
        for head in registry.HEADS:
            assert sum(p[head]["probs"].values()) == pytest.approx(1.0, abs=1e-4)

    # 温度缩放确实生效：T 越大分布越平
    logits = [[2.0, 0.5, -1.0]]
    assert max(infer._softmax(logits, 1.0)[0]) > max(infer._softmax(logits, 3.0)[0])
    assert sum(infer._softmax(logits, 3.0)[0]) == pytest.approx(1.0)


def test_student_unavailable_without_weights(tmp_path):
    with pytest.raises(infer.StudentUnavailable):
        infer.Student(tmp_path / "nothing")
