"""学生模型训练集（ADR-0021）—— 从 Luna 的现行判定单元造 jsonl，按产品 × ISO 周切对照集。

    python -m models.dataset                       # 写到 <数据目录>/datasets/student-v1/
    python -m models.dataset --out /tmp/ds --holdout 0.1

## 三条纪律

1. **只学 Luna，不学规则、不学自己。** 训练标签取 `annotations` 里的现行结论
   （`radar_db.annotations_read.current_annotations`，与页面同一条发布规则），再按 run 的
   provider 剔掉 `rule`（规则行是关键词判的，学它等于把词表背下来）、`local_model`（学生自己）、
   `propagated`（抄来的）。剩下的就是 Luna 写的。
2. **文本＝发给 Luna 的那个 payload。** 用 `annotate._build_payload` 造出与 LLM 完全相同的
   `redact.comment_payload` dict（正文＋产品块＋父评论＋标题＋帖子开头，白名单边界在那里），
   再用 `payload_text()` 按固定顺序铺成一段文字。学生看到的信息集合与 Luna 一致 —— 少给它
   父评论，「有」「劲」这类短回复它就只能猜。
3. **对照集按 `(subject_code, ISO 周)` 分组切**，整组进对照或整组进训练。同一产品同一周里
   的评论互相极像（同一个帖子下的对话、同一句话的变体），随机按行切会让对照集的一致率虚高
   两三个百分点，而那个数字要写进 ADR 当路由阈值的依据。

## 输出

- `train.jsonl` / `holdout.jsonl`：每行 `{unit, group, text, relevance, attitude, aspects}`。
  `attitude` 只在 `relevance=relevant` 时非空（schema 的交叉约束）。
- `meta.json`：条数、标签分布、aspect 头开不开（正样本 ≥ `MIN_ASPECT_POS` 才训）。

含用户正文，**写在数据目录（仓库外）**，不进 git。
"""

import argparse
import hashlib
import json
import logging
import os
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from models import registry  # noqa: E402

log = logging.getLogger("worker.models.dataset")

MIN_ASPECT_POS = 5000
EXCLUDED_PROVIDERS = ("rule", "local_model", "propagated")
TEXT_FIELDS = ("comment", "product", "parent_comment", "post_title", "post_context")


def payload_text(payload):
    """`redact.comment_payload` 的 dict → 学生模型输入文本。

    评论正文放最前：seq 128 截断时先丢的是帖子开头这类语境，不是待判的那句话。
    产品块只铺代码＋名称＋前几个别名，别名太多会把正文挤出窗口。
    """
    p = payload.get("product") or {}
    aliases = [a for a in (p.get("aliases") or [])[:4]]
    product = " ".join(filter(None, [p.get("code"), p.get("name"), "／".join(aliases) if aliases else None]))
    parts = [f"评论：{payload.get('comment') or ''}", f"产品：{product}"]
    if payload.get("parent_comment"):
        parts.append(f"父评论：{payload['parent_comment']}")
    if payload.get("post_title"):
        parts.append(f"标题：{payload['post_title']}")
    if payload.get("post_context"):
        parts.append(f"帖子：{payload['post_context']}")
    return "\n".join(parts)


def group_key(code, posted_at):
    if posted_at is None:
        return f"{code}|unknown"
    y, w, _ = posted_at.isocalendar()
    return f"{code}|{y}-W{w:02d}"


def is_holdout(group, holdout=0.10, seed="student-v1"):
    """按组名哈希决定去向。同一组永远同一去向，重跑不会漂。"""
    h = int.from_bytes(hashlib.sha256(f"{seed}|{group}".encode("utf-8")).digest()[:8], "big")
    return (h % 10000) / 10000.0 < holdout


def split(units, holdout=0.10, seed="student-v1"):
    train, hold = [], []
    for u in units:
        (hold if is_holdout(u["group"], holdout, seed) else train).append(u)
    return train, hold


def _provider_of_runs(engine):
    from radar_db.schema import annotation_runs

    with engine.connect() as conn:
        return dict(conn.execute(select(annotation_runs.c.run_id, annotation_runs.c.provider)).all())


def collect_units(engine, *, codes=None):
    """`[{unit, group, text, relevance, attitude, aspects, posted_at}]`，只含 Luna 写的现行单元。"""
    from jobs import annotate
    from ai.prompts.comment_product_v3 import VERSION as comment_prompt_version
    from radar_db.annotations_read import released_annotations
    from radar_db.comment_filter import load_comment_filter_config
    from radar_db.comment_routes import require_ready as require_comment_routes_ready

    filter_config = load_comment_filter_config()
    # 训练数据也是筛选口径的下游产物；历史回填未完成或配置漂移时宁可停，
    # 不能把旧的全量评论悄悄混入下一版学生模型。
    require_comment_routes_ready(engine)
    providers = _provider_of_runs(engine)
    annotation_scope = {
        "task": "comment_product",
        "prompt_version": comment_prompt_version,
        "parent_filter_config": filter_config,
    }
    rel = released_annotations(engine, "relevance", "comment", **annotation_scope)
    att = released_annotations(engine, "attitude", "comment", **annotation_scope)
    asp = released_annotations(engine, "aspect", "comment", **annotation_scope)

    keep = {}
    for unit, r in rel.items():
        if codes and unit[1] not in codes:
            continue
        # run 不在表里（测试夹具直接插的行）视为 LLM 写的；明确是 rule/自家/抄的才剔。
        if providers.get(r["run_id"], "llm") in EXCLUDED_PROVIDERS:
            continue
        if r["value"] not in registry.LABELS["relevance"]:
            continue
        keep[unit] = r

    ids = sorted({u[0] for u in keep})
    sources = {}
    for i in range(0, len(ids), 900):
        chunk = [{"target_type": "comment", "target_id": cid} for cid in ids[i:i + 900]]
        sources.update(annotate._load_sources(engine, "comment_product", chunk))

    out = []
    for (cid, code), r in keep.items():
        src = sources.get(("comment", cid))
        if not src or not (src.get("text") or "").strip():
            continue
        payload = annotate._build_payload("comment_product", {"target_id": cid, "subject_code": code}, src)
        a = att.get((cid, code))
        attitude = a["value"] if (a and r["value"] == "relevant" and a["value"] in registry.LABELS["attitude"]) else None
        aspects = (asp.get((cid, code)) or {}).get("value") or []
        out.append({
            "unit": [cid, code], "group": group_key(code, r["posted_at"]), "text": payload_text(payload),
            "relevance": r["value"], "attitude": attitude,
            "aspects": [x for x in aspects if isinstance(x, str)],
        })
    out.sort(key=lambda u: (u["unit"][1], u["unit"][0]))
    return out


def build(engine, out_dir=None, *, holdout=0.10, codes=None, min_aspect_pos=MIN_ASPECT_POS):
    out_dir = Path(out_dir or registry.dataset_dir())
    out_dir.mkdir(parents=True, exist_ok=True)
    units = collect_units(engine, codes=codes)
    train, hold = split(units, holdout)
    for name, rows in (("train", train), ("holdout", hold)):
        with open(out_dir / f"{name}.jsonl", "w", encoding="utf-8") as fh:
            for u in rows:
                fh.write(json.dumps(u, ensure_ascii=False) + "\n")
    aspect_pos = sum(1 for u in train if u["aspects"])
    meta = {
        "version": registry.STUDENT_VERSION, "n_units": len(units), "n_train": len(train), "n_holdout": len(hold),
        "holdout_fraction": holdout, "groups": len({u["group"] for u in units}),
        "relevance_dist": dict(Counter(u["relevance"] for u in units)),
        "attitude_dist": dict(Counter(u["attitude"] for u in units if u["attitude"])),
        "aspect_positive_train": aspect_pos, "aspect_head": aspect_pos >= min_aspect_pos,
        "min_aspect_pos": min_aspect_pos,
        "text_fields": TEXT_FIELDS,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("训练集：%d 条（训练 %d／对照 %d，%d 组）→ %s", len(units), len(train), len(hold), meta["groups"], out_dir)
    return meta


def load_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description="学生模型训练集")
    ap.add_argument("--out", help="输出目录（默认数据目录下，仓库外）")
    ap.add_argument("--holdout", type=float, default=0.10)
    ap.add_argument("--codes", help="只取这些产品的判定单元")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s %(message)s")
    from radar_db import make_engine
    meta = build(make_engine(), args.out, holdout=args.holdout,
                 codes=[c.strip() for c in args.codes.split(",")] if args.codes else None)
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
