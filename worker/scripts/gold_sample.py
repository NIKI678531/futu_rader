"""人工核对集抽样（ADR-0021 §人工核对）—— 分层抽 400 条评论 × 产品，导出给人填的 Excel。

    cd worker && python -m scripts.gold_sample                    # → <数据目录>/gold-400.xlsx ＋ gold-400-model-labels.xlsx
    cd worker && python -m scripts.gold_sample --n 400 --seed 7 --out-dir D:/tmp
    cd worker && python -m scripts.gold_sample --source llm       # 无学生时，仅核对 Luna 现行结论
    cd worker && python -m scripts.gold_sample --llm-only         # → gold-llm-400.xlsx ＋ gold-llm-400-model-labels.xlsx

## 为什么分层、按什么分

随机抽 400 条，八成会是简体、高置信、无关的评论 —— 那恰恰是模型最不会错的地方，量出来的
准确率好看但没用。分层维度取模型**最可能出错的边界**：

- 自家／竞品（`own`／`peer`）：竞品别名少、语料少；
- 学生预测极性：相关时取态度（积极／中性／消极），否则取相关性（无关／需上下文）；
- 学生置信带：`<0.7`／`0.7–0.85`／`≥0.85` —— 0.85 是路由阈值，0.7 是前端「低置信」阈值；
- 简／繁／粤（`synthesize.detect_language`，与 Layer B 同一判法）。

每个非空层至少 1 条，其余按层大小比例分配。抽样池是**有学生行**的判定单元（学生跑过的才有
置信度可分层）；Luna 的现行标签一并带出，给 `evaluate_gold` 算三套系统。

## `--llm-only`：学生还没训出来时先量 Luna

学生模型是一次性 CPU 训练，训完之前库里只有 Luna 的现行结论 —— 页面上此刻展示的就是它们。
`--llm-only` 把抽样池换成**有 Luna 现行结论**的判定单元，分层去掉置信带（Luna 行没有校准概率），
极性按 Luna 的标签分：自家／竞品 × Luna 极性 × 简／繁／粤。文件名带 `llm`（`gold-llm-400.xlsx`），
`说明` 页开头两行写明「只核对 Luna 现行结论，不能据此评估学生」。`evaluate_gold` 读它时学生那套
的三个指标是 None，`combined` 等于 Luna —— 那正是没有学生时页面上实际发生的事。

## 两个文件为什么分开

`gold-400.xlsx` 里**没有**任何模型标签 —— 标注人看到「模型说相关」再判，会往模型那边靠，
量出来的是从众率不是准确率。模型标签单独放 `gold-400-model-labels.xlsx`，评估时按编号合并。
两个文件都含用户正文，写在数据目录（仓库外），不含作者昵称／uid，工作簿属性里也不写创建者。
"""

import argparse
import json
import logging
import os
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = Path(__file__).resolve().parents[2]
for cand in (REPO_ROOT, Path(__file__).resolve().parents[1]):
    if (cand / "radar_db").is_dir() and str(cand) not in sys.path:
        sys.path.insert(0, str(cand))

from jobs import annotate  # noqa: E402
from jobs.import_dump import pool_codes  # noqa: E402
from jobs.synthesize import detect_language, load_master  # noqa: E402
from models import registry  # noqa: E402
from radar_db import default_data_dir, make_engine  # noqa: E402
from radar_db.comment_routes import (  # noqa: E402
    readiness_on_connection as comment_routes_ready_on_connection,
    report_policy_fields,
    require_ready as require_comment_routes_ready,
)
from radar_db.schema import annotation_runs, annotations  # noqa: E402

log = logging.getLogger("worker.gold_sample")

N_DEFAULT = 400
BANDS = ((0.7, "<0.7"), (0.85, "0.7–0.85"), (None, "≥0.85"))
REL_ZH = {"relevant": "相关", "irrelevant": "无关", "needs_context": "需上下文"}
ATT_ZH = {"positive": "积极", "neutral": "中性", "negative": "消极"}
LANG_ZH = {"zh-Hans": "简", "zh-Hant": "繁", "yue": "粤"}
LABEL_SHEET = "标注"
GUIDE_SHEET = "说明"
COLUMNS = ("编号", "产品代码", "产品名", "帖子标题", "父评论", "评论正文", "相关性", "态度", "备注")
LABEL_COLUMNS = ("编号", "comment_id", "产品代码", "层", "学生相关性", "学生相关性概率", "学生态度", "学生态度概率",
                 "Luna相关性", "Luna态度", "学生模型", "抽样来源",
                 "LLM模型", "LLM请求模型", "LLM Prompt", "LLM Schema", "LLM Taxonomy",
                 "评论路由版本", "产品池摘要", "回归案例ID")
# 不算 Luna 结论的写入方（ADR-0021 §8），与 evaluate_gold.NON_LLM_PROVIDERS 同一份含义。
NON_LLM_PROVIDERS = ("rule", "local_model", "propagated")

LLM_ONLY_GUIDE_LINES = (
    "本表只核对 Luna 现行结论；按自家/竞品、预测极性、简繁粤分层，无学生标签或置信带。",
    "不能据此评估学生模型或学生路由效果；请独立判断，不要查看另一份模型标签表。",
)

GUIDE_LINES = (
    "填「相关性」列：相关 / 无关 / 需上下文（下拉）。「态度」列只在相关时填：积极 / 消极 / 中性（下拉）。",
    "",
    "相关 ＝ 评论在评价这只 ETF 本身，或在说买卖／持有它，或用它的成分股解释它的涨跌。",
    "无关 ＝ 只聊个股、大盘、宏观、闲聊、表情、复读；提到 ETF 代码但没有针对它的内容也算无关。",
    "需上下文 ＝ 看完帖子标题和最多三层父评论仍判不出它在说什么、说的是不是这只产品。",
    "",
    "积极／消极 ＝ 对产品的评价或买卖意向（好／差、买／卖、加／减）。中性 ＝ 相关但没有表态（问价、问规则、转述）。",
    "大盘看跌 ≠ 产品消极：「恒指要崩」是市场方向，不是对 ETF 的态度；只有说到这只产品才算。",
    "拿不准填「需上下文」，不要用「中性」兜底 —— 中性是一个明确的判断。",
    "",
    "每条 20–30 秒，400 条约两小时，可两人各 200。不要看模型标签（它们在另一个文件里）。",
    "400 条的 95% 置信区间约 ±5 个百分点：这是量尺，不是发布门槛（ADR-0019 不变）。",
    "完整规则见 docs/gold-labeling-guide.md。",
)


def format_parent_comments(values):
    """Render nearest-first reply context exactly as a human annotator sees it."""

    parents = [str(value).strip() for value in (values or ()) if str(value or "").strip()][:3]
    lines = []
    for index, value in enumerate(parents, 1):
        label = "第1层（直接父评论）" if index == 1 else f"第{index}层"
        lines.append(f"{label}：{value}")
    return "\n".join(lines)


def band_of(p):
    if p is None:
        return "无"
    for edge, name in BANDS:
        if edge is None or p < edge:
            return name
    return BANDS[-1][1]


def polarity_of(rel, att):
    if rel == "relevant":
        return ATT_ZH.get(att, "态度缺")
    return REL_ZH.get(rel, "?")


def stratum_key(unit):
    lang = LANG_ZH.get(unit["language"], unit["language"])
    if unit.get("llm_only") or unit.get("sample_source") == "llm":
        return "|".join((unit["ownership"], polarity_of(unit["llm_relevance"], unit["llm_attitude"]), lang))
    return "|".join((unit["ownership"], polarity_of(unit["student_relevance"], unit["student_attitude"]),
                     band_of(unit["student_confidence"]), lang))


def allocate(strata, n, rnd):
    """比例分配，每个非空层至少 1；余数按小数部分大的层先补。返回 `{层: 抽几条}`。"""
    total = sum(len(v) for v in strata.values())
    if total <= n:
        return {k: len(v) for k, v in strata.items()}
    keys = sorted(strata)
    base = {k: max(1, int(n * len(strata[k]) / total)) for k in keys}
    for k in keys:
        base[k] = min(base[k], len(strata[k]))
    # 至少 1 可能让总数超出；从最大层削掉。
    while sum(base.values()) > n:
        k = max(keys, key=lambda kk: base[kk])
        base[k] -= 1
    frac = sorted(keys, key=lambda kk: -((n * len(strata[kk]) / total) % 1))
    i = 0
    while sum(base.values()) < n and i < 10 * len(keys):
        k = frac[i % len(keys)]
        if base[k] < len(strata[k]):
            base[k] += 1
        i += 1
    return base


def _latest_by_provider(engine, kind, providers):
    """`{(target_id, code): (value, confidence, run_id)}`，每个单元取该 provider 集下最新一行。"""
    with engine.connect() as conn:
        rows = conn.execute(
            select(annotations.c.target_id, annotations.c.subject_code, annotations.c.value_json,
                   annotations.c.calibrated_confidence, annotations.c.run_id, annotations.c.annotation_id)
            .select_from(annotations.join(annotation_runs, annotation_runs.c.run_id == annotations.c.run_id))
            .where(annotations.c.kind == kind, annotations.c.target_type == "comment",
                   annotation_runs.c.provider.in_(providers), annotations.c.review_state != "rejected")
            .order_by(annotations.c.annotation_id)
        )
        out = {}
        for tid, code, vj, conf, run_id, _aid in rows:
            out[(tid, code)] = (json.loads(vj), conf, run_id)
    return out




def collect_units(engine, *, ownership=None, source="student", llm_only=False):
    """默认抽有学生行的单元；source=llm 只抽 Luna 现行结论，不需要学生权重。"""
    if source not in ("student", "llm"):
        raise ValueError(f"未知抽样来源：{source}")
    llm_only = llm_only or source == "llm"
    ownership = ownership or pool_codes()
    from ai.prompts.comment_product_v3 import VERSION as comment_prompt_version
    from radar_db.annotations_read import released_annotations
    from radar_db.comment_filter import load_comment_filter_config, require_filter_ready

    filter_config = load_comment_filter_config()
    with engine.connect() as conn:
        route_active = comment_routes_ready_on_connection(conn)
    if not route_active:
        require_filter_ready(engine, filter_config)
    with engine.connect() as conn:
        run_meta = {
            row.run_id: {
                "provider": row.provider,
                "model": row.model_id,
                "promptVersion": row.prompt_version,
                "schemaVersion": row.schema_version,
                "taxonomyVersion": row.taxonomy_version,
            }
            for row in conn.execute(select(
                annotation_runs.c.run_id,
                annotation_runs.c.provider,
                annotation_runs.c.model_id,
                annotation_runs.c.prompt_version,
                annotation_runs.c.schema_version,
                annotation_runs.c.taxonomy_version,
            ))
        }
    routing_policy = report_policy_fields(engine) if route_active else {}
    annotation_scope = {
        "task": "comment_product",
        "prompt_version": comment_prompt_version,
        "parent_filter_config": filter_config,
    }
    cur_rel = released_annotations(engine, "relevance", "comment", **annotation_scope)
    cur_att = released_annotations(engine, "attitude", "comment", **annotation_scope)
    # 学生行需要保留 provider 自己的历史预测（即使后来由 Luna 接管），但它的
    # 判定单元必须仍出现在共享的现行、合格父帖集合中。这样不会另写一份父帖 SQL。
    s_rel = {} if llm_only else {
        unit: row
        for unit, row in _latest_by_provider(engine, "relevance", ("local_model",)).items()
        if unit in cur_rel
    }
    s_att = {} if llm_only else {
        unit: row
        for unit, row in _latest_by_provider(engine, "attitude", ("local_model",)).items()
        if unit in cur_rel
    }

    def luna(cur, unit):
        r = cur.get(unit)
        if r is None or run_meta.get(r["run_id"], {}).get("provider", "llm") in NON_LLM_PROVIDERS:
            return None
        return r["value"]

    def luna_policy(unit):
        row = cur_rel.get(unit)
        if row is None:
            return None
        meta = run_meta.get(row["run_id"])
        if meta is None or meta["provider"] in NON_LLM_PROVIDERS:
            return None
        return {
            "model": meta["model"],
            # Historical annotation_runs only persisted the provider-returned
            # model.  Such exports are valid only when that snapshot is also
            # the configured request identifier; calibration exports preserve
            # both values explicitly.
            "requestedModel": meta["model"],
            **{key: meta[key] for key in ("promptVersion", "schemaVersion", "taxonomyVersion")},
            **routing_policy,
        }

    if llm_only:
        keys = sorted(unit for unit in cur_rel if unit[1] in ownership and luna(cur_rel, unit) in REL_ZH)
    else:
        keys = sorted(unit for unit in s_rel if unit[1] in ownership)
    ids = sorted({unit[0] for unit in keys})
    sources = {}
    for i in range(0, len(ids), 900):
        chunk = [{"target_type": "comment", "target_id": cid} for cid in ids[i:i + 900]]
        sources.update(annotate._load_sources(engine, "comment_product", chunk))

    units = []
    for cid, code in keys:
        src = sources.get(("comment", cid))
        if not src or not (src.get("text") or "").strip():
            continue
        rel, rel_conf, _run = s_rel.get((cid, code), (None, None, None))
        att, att_conf, _ = s_att.get((cid, code), (None, None, None))
        confs = [c for c in (rel_conf, att_conf if rel == "relevant" else None) if c is not None]
        l_rel = luna(cur_rel, (cid, code))
        parents = list(src.get("parents") or ())[:3]
        units.append({
            "sample_source": source,
            "comment_id": cid, "code": code, "ownership": ownership.get(code, "peer"),
            "text": src["text"], "title": src.get("title"),
            "parent": format_parent_comments(parents), "parents": parents,
            "student_relevance": rel, "student_attitude": att if rel == "relevant" else None,
            "student_relevance_p": rel_conf, "student_attitude_p": att_conf if rel == "relevant" else None,
            "student_confidence": min(confs) if confs else None,
            "llm_relevance": l_rel, "llm_attitude": luna(cur_att, (cid, code)) if l_rel == "relevant" else None,
            "llm_policy": luna_policy((cid, code)),
            "language": detect_language([src["text"]]),
            "llm_only": llm_only,
        })
    return units


def sample(units, n=N_DEFAULT, seed=42):
    rnd = random.Random(seed)
    strata = defaultdict(list)
    for u in units:
        strata[stratum_key(u)].append(u)
    quota = allocate(strata, n, rnd)
    picked = []
    for k in sorted(strata):
        pool = sorted(strata[k], key=lambda u: (u["code"], u["comment_id"]))
        rnd.shuffle(pool)
        for u in pool[:quota[k]]:
            picked.append({**u, "stratum": k})
    rnd.shuffle(picked)
    for i, u in enumerate(picked, 1):
        u["id"] = f"G{i:04d}"
    return picked


def write_workbooks(picked, out_dir, *, names=None, source="student", llm_only=False):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    names = names or {p["code"]: p["name"] for p in load_master()["products"]}
    llm_only = llm_only or source == "llm"
    stem = f"gold-llm-{len(picked)}" if llm_only else f"gold-{len(picked)}"
    guide_lines = (LLM_ONLY_GUIDE_LINES + GUIDE_LINES) if llm_only else GUIDE_LINES

    wb = Workbook()
    wb.properties.creator = "futu-radar"
    wb.properties.lastModifiedBy = "futu-radar"
    ws = wb.active
    ws.title = LABEL_SHEET
    ws.append(list(COLUMNS))
    for c in ws[1]:
        c.font = Font(bold=True)
    for u in picked:
        ws.append([u["id"], u["code"], names.get(u["code"], u["code"]), u.get("title") or "", u.get("parent") or "",
                   u["text"], "", "", ""])
    n = len(picked)
    dv_rel = DataValidation(type="list", formula1='"相关,无关,需上下文"', allow_blank=True, showErrorMessage=True,
                            errorTitle="相关性", error="只能填：相关 / 无关 / 需上下文")
    dv_att = DataValidation(type="list", formula1='"积极,消极,中性"', allow_blank=True, showErrorMessage=True,
                            errorTitle="态度", error="只能填：积极 / 消极 / 中性（仅相关时填）")
    ws.add_data_validation(dv_rel)
    ws.add_data_validation(dv_att)
    if n:
        dv_rel.add(f"G2:G{n + 1}")
        dv_att.add(f"H2:H{n + 1}")
    widths = {"A": 8, "B": 9, "C": 22, "D": 28, "E": 28, "F": 60, "G": 11, "H": 9, "I": 20}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2, max_row=n + 1, min_col=4, max_col=6):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"

    guide = wb.create_sheet(GUIDE_SHEET)
    guide.column_dimensions["A"].width = 110
    for line in guide_lines:
        guide.append([line])
    for row in guide.iter_rows():
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    gold_path = out_dir / f"{stem}.xlsx"
    wb.save(gold_path)

    wb2 = Workbook()
    wb2.properties.creator = "futu-radar"
    wb2.properties.lastModifiedBy = "futu-radar"
    ws2 = wb2.active
    ws2.title = "模型标签"
    ws2.append(list(LABEL_COLUMNS))
    student_model = None if llm_only else registry.model_id_string()
    for u in picked:
        policy = u.get("llm_policy") or {}
        ws2.append([u["id"], u["comment_id"], u["code"], u["stratum"], u["student_relevance"], u["student_relevance_p"],
                    u["student_attitude"], u["student_attitude_p"], u["llm_relevance"], u["llm_attitude"],
                    student_model, source if source == "llm" or not llm_only else None,
                    policy.get("model"), policy.get("requestedModel"),
                    policy.get("promptVersion"), policy.get("schemaVersion"),
                    policy.get("taxonomyVersion"), policy.get("commentRouteVersion"),
                    policy.get("productPoolDigest"), u.get("regression_case_id")])
    for i in range(1, len(LABEL_COLUMNS) + 1):
        ws2.column_dimensions[get_column_letter(i)].width = 16
    labels_path = out_dir / f"{stem}-model-labels.xlsx"
    wb2.save(labels_path)
    return gold_path, labels_path


def main(argv=None):
    ap = argparse.ArgumentParser(description="分层抽人工核对集")
    ap.add_argument("--n", type=int, default=N_DEFAULT)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--source", choices=("student", "llm"), default="student",
                    help="student：学生判过的单元（默认）；llm：仅 Luna 现行结论，不训练、不写库")
    ap.add_argument("--out-dir", help="默认数据目录（仓库外）")
    ap.add_argument("--llm-only", action="store_true",
                    help="抽样池改为有 Luna 现行结论的判定单元（学生还没训出来时用），文件名带 llm")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s %(message)s")
    engine = make_engine()
    require_comment_routes_ready(engine)
    units = collect_units(engine, source=args.source, llm_only=args.llm_only)
    if not units:
        message = ("没有学生行：原方案需先准备学生模型并运行 python -m jobs.classify；"
                   "仅核对已有 Luna 结论可运行 python -m scripts.gold_sample --source llm"
                   if args.source == "student" and not args.llm_only else "没有可抽样的 Luna 现行结论（需有正文和池内产品）。")
        print(message, file=sys.stderr)
        return 2
    picked = sample(units, args.n, args.seed)
    gold, labels = write_workbooks(picked, args.out_dir or default_data_dir(), source=args.source, llm_only=args.llm_only)
    strata = Counter(u["stratum"] for u in picked)
    print(json.dumps({"source": args.source, "pool": len(units), "picked": len(picked), "strata": len(strata),
                      "llm_only": args.llm_only or args.source == "llm",
                      "gold": str(gold), "model_labels": str(labels),
                      "by_stratum": dict(sorted(strata.items()))}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
