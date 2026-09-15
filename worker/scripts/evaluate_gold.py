"""人工核对集评估（ADR-0021 §人工核对）—— 三套系统对 400 条人工标签的准确率，写 `meta_kv.ai_validation`。

    cd worker && python -m scripts.evaluate_gold --file gold-400.xlsx
    cd worker && python -m scripts.evaluate_gold --file D:/x/gold-400.xlsx --labels-file D:/x/gold-400-model-labels.xlsx
    cd worker && python -m scripts.evaluate_gold --file gold-400.xlsx --no-write     # 只算不写库

## 算什么

按编号把人工表与模型标签表合并，对三套系统各算：

- `relevance_accuracy`：三类相关性准确率（人工填了相关性的行）；
- `attitude_accuracy`：人工判**相关**且填了态度的行里，系统态度与人一致的比例。系统判成无关
  （没有态度）算错 —— 页面上那条评论就是没态度；
- `attitude_macro_f1`：态度三类的 macro-F1（同一批行）；
- 混淆矩阵。

三套：`student`（学生标签）、`llm`（Luna 现行标签）、`combined`（生产上的路由：学生两头概率
都 ≥ 路由阈值用学生，否则用 Luna；Luna 没标过就还是学生）。`meta_kv.ai_validation` 顶层三个数
取 `combined` —— 那是页面上实际展示的那套。

## 形状（另一位工程师在 backend/core/meta.py 读，**不能改**）

    {"level": "spot_check", "n": 400, "date": "YYYY-MM-DD",
     "relevance_accuracy": 0.91, "attitude_accuracy": 0.87, "attitude_macro_f1": 0.85,
     "by_system": {"student": {同三键}, "llm": {同三键}, "combined": {同三键}}}

算不出来的量写 `null`（例如没有人填态度），不写 0（铁律 2）。写完 `bump_revision("annotation")`
让后端缓存失效。这是量尺不是门槛：数字低不会让任何结论下线（ADR-0019）。
"""

import argparse
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import insert, select, update

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = Path(__file__).resolve().parents[2]
for cand in (REPO_ROOT, Path(__file__).resolve().parents[1]):
    if (cand / "radar_db").is_dir() and str(cand) not in sys.path:
        sys.path.insert(0, str(cand))

import clock  # noqa: E402
from models import registry  # noqa: E402
from radar_db import default_data_dir, make_engine  # noqa: E402
from radar_db.schema import meta_kv  # noqa: E402

log = logging.getLogger("worker.evaluate_gold")

OUT_DIR = REPO_ROOT / ".scratch" / "llm-90d"
REL_FROM_ZH = {"相关": "relevant", "无关": "irrelevant", "需上下文": "needs_context"}
ATT_FROM_ZH = {"积极": "positive", "消极": "negative", "中性": "neutral"}
REL_LABELS = ("relevant", "irrelevant", "needs_context")
ATT_LABELS = ("positive", "neutral", "negative")
SYSTEMS = ("student", "llm", "combined")


def _norm(v, table):
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    return table.get(s, s if s in table.values() else None)


def read_gold(path):
    """`标注` 页 → `{编号: {relevance, attitude, note}}`。没填的行不进结果。"""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb["标注"] if "标注" in wb.sheetnames else wb.active
    rows = ws.iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rows)]
    idx = {h: i for i, h in enumerate(header)}
    out = {}
    for r in rows:
        if not r or r[idx["编号"]] is None:
            continue
        rel = _norm(r[idx["相关性"]], REL_FROM_ZH)
        att = _norm(r[idx["态度"]], ATT_FROM_ZH)
        if rel is None:
            continue
        out[str(r[idx["编号"]]).strip()] = {"relevance": rel, "attitude": att if rel == "relevant" else None,
                                            "note": r[idx["备注"]] if "备注" in idx else None}
    return out


def read_model_labels(path):
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rows)]
    idx = {h: i for i, h in enumerate(header)}
    out = {}
    for r in rows:
        if not r or r[idx["编号"]] is None:
            continue
        out[str(r[idx["编号"]]).strip()] = {
            "comment_id": r[idx["comment_id"]], "code": r[idx["产品代码"]], "stratum": r[idx["层"]],
            "student": {"relevance": r[idx["学生相关性"]], "attitude": r[idx["学生态度"]],
                        "relevance_p": r[idx["学生相关性概率"]], "attitude_p": r[idx["学生态度概率"]]},
            "llm": {"relevance": r[idx["Luna相关性"]], "attitude": r[idx["Luna态度"]]},
        }
    return out


def combined_label(m, threshold=None):
    """生产路由：学生两头都够置信用学生，否则用 Luna（Luna 没有就学生）。"""
    threshold = registry.route_threshold() if threshold is None else threshold
    st, lu = m["student"], m["llm"]
    rp, ap_ = st.get("relevance_p"), st.get("attitude_p")
    confident = rp is not None and rp >= threshold and st["relevance"] != "needs_context"
    if confident and st["relevance"] == "relevant":
        confident = ap_ is not None and ap_ >= threshold
    if confident or lu.get("relevance") is None:
        return {"relevance": st["relevance"], "attitude": st["attitude"] if st["relevance"] == "relevant" else None}
    return {"relevance": lu["relevance"], "attitude": lu["attitude"] if lu["relevance"] == "relevant" else None}


def _accuracy(pairs):
    pairs = [(g, p) for g, p in pairs if g is not None]
    if not pairs:
        return None, 0
    return round(sum(1 for g, p in pairs if g == p) / len(pairs), 4), len(pairs)


def _confusion(pairs, labels):
    m = {g: {p: 0 for p in labels + ("none",)} for g in labels}
    for g, p in pairs:
        if g in m:
            m[g][p if p in labels else "none"] += 1
    return m


def _macro_f1(pairs, labels):
    pairs = [(g, p) for g, p in pairs if g is not None]
    if not pairs:
        return None
    f1s = []
    for lab in labels:
        tp = sum(1 for g, p in pairs if g == lab and p == lab)
        fp = sum(1 for g, p in pairs if g != lab and p == lab)
        fn = sum(1 for g, p in pairs if g == lab and p != lab)
        if tp + fp + fn == 0:
            continue  # 这一类人工没标、系统也没判：不参与平均
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return round(sum(f1s) / len(f1s), 4) if f1s else None


def evaluate(gold, model, *, threshold=None):
    """返回 `{n, by_system: {system: {relevance_accuracy, attitude_accuracy, attitude_macro_f1, n_relevance, n_attitude, confusion}}}`。"""
    joined = [(gold[k], model[k]) for k in sorted(gold) if k in model]
    out = {"n": len(joined), "n_gold": len(gold), "n_unmatched": len(set(gold) - set(model)), "by_system": {}}
    for system in SYSTEMS:
        rel_pairs, att_pairs = [], []
        for g, m in joined:
            pred = combined_label(m, threshold) if system == "combined" else m[system]
            rel_pairs.append((g["relevance"], pred.get("relevance")))
            if g["relevance"] == "relevant" and g["attitude"] is not None:
                att_pairs.append((g["attitude"], pred.get("attitude") if pred.get("relevance") == "relevant" else None))
        rel_acc, n_rel = _accuracy(rel_pairs)
        att_acc, n_att = _accuracy(att_pairs)
        out["by_system"][system] = {
            "relevance_accuracy": rel_acc, "attitude_accuracy": att_acc,
            "attitude_macro_f1": _macro_f1(att_pairs, ATT_LABELS),
            "n_relevance": n_rel, "n_attitude": n_att,
            "confusion": {"relevance": _confusion(rel_pairs, REL_LABELS), "attitude": _confusion(att_pairs, ATT_LABELS)},
        }
    return out


def ai_validation_payload(result, date_str):
    """`meta_kv.ai_validation` 的形状（见模块 docstring）。顶层三个数＝combined。"""
    keys = ("relevance_accuracy", "attitude_accuracy", "attitude_macro_f1")
    comb = result["by_system"]["combined"]
    return {
        "level": "spot_check", "n": result["n"], "date": date_str,
        **{k: comb[k] for k in keys},
        "by_system": {s: {k: result["by_system"][s][k] for k in keys} for s in SYSTEMS},
    }


def write_validation(engine, payload):
    from radar_db.revisions import bump_revision

    value = json.dumps(payload, ensure_ascii=False)
    with engine.begin() as conn:
        if not conn.execute(update(meta_kv).where(meta_kv.c.k == "ai_validation").values(v=value)).rowcount:
            conn.execute(insert(meta_kv).values(k="ai_validation", v=value))
        bump_revision(conn, "annotation")


def _resolve(path, default_dir):
    p = Path(path)
    if p.exists():
        return p
    alt = Path(default_dir) / p.name
    if alt.exists():
        return alt
    raise SystemExit(f"找不到 {path}（也不在 {default_dir}）")


def main(argv=None):
    ap = argparse.ArgumentParser(description="评估人工核对集，写 meta_kv.ai_validation")
    ap.add_argument("--file", required=True, help="人工填好的 gold-400.xlsx（相对路径先在数据目录找）")
    ap.add_argument("--labels-file", help="模型标签表，默认同目录下 gold-<n>-model-labels.xlsx")
    ap.add_argument("--no-write", action="store_true", help="只算不写 meta_kv")
    ap.add_argument("--threshold", type=float, help="combined 路由阈值，默认 STUDENT_ROUTE_THRESHOLD")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s %(message)s")

    data_dir = default_data_dir()
    gold_path = _resolve(args.file, data_dir)
    labels_path = (_resolve(args.labels_file, data_dir) if args.labels_file
                   else gold_path.with_name(gold_path.stem + "-model-labels.xlsx"))
    if not labels_path.exists():
        raise SystemExit(f"找不到模型标签表 {labels_path}")
    gold = read_gold(gold_path)
    model = read_model_labels(labels_path)
    if not gold:
        raise SystemExit("人工表里没有填好的行（相关性列为空）")
    result = evaluate(gold, model, threshold=args.threshold)
    stamp = clock.now()
    payload = ai_validation_payload(result, stamp.strftime("%Y-%m-%d"))
    report = {"stamp": stamp.strftime("%Y%m%dT%H%M%S"), "gold_file": gold_path.name, "labels_file": labels_path.name,
              "by_stratum": dict(Counter(model[k]["stratum"] for k in gold if k in model)),
              **result, "ai_validation": payload}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"gold-eval-{report['stamp']}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if not args.no_write:
        write_validation(make_engine(), payload)
        log.info("meta_kv.ai_validation 已写入并 bump annotation revision")
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    print(f"\n完整报告：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
