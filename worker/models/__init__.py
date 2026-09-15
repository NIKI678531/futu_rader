"""学生模型（ADR-0021）：蒸馏自 Luna 标注的本地 CPU 分类器，只判评论 × 产品的相关性与态度。

模块分工：

- `registry.py`  模型 ID 与 revision 锁定、本地权重目录、路由阈值 —— 型号的唯一入口。
- `dataset.py`   从 `annotations` 里的 Luna 现行判定单元造训练集；按产品 × ISO 周分组切对照集。
- `train.py`     两头分别微调（相关性三类、态度三类），对照集温度缩放，写 `calibration.json`。
- `export.py`    ONNX 导出 → int8 动态量化 → 与 fp32 预测一致性校验。
- `infer.py`     onnxruntime 批推理，输出经温度缩放的各头概率。

这里**不做**任何口径计算，也不写库 —— 写库在 `jobs/classify.py`。
ML 依赖单独放 `worker/requirements-ml.txt`，backend 一个都不装。
"""
