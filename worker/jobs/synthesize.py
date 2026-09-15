"""Layer B：产品 × 区间级生成物（ADR-0020 步骤 16）—— 模型只写字，数在 `backend/core/`。

    python -m jobs.synthesize --scope <scope_id>                 # scope 里的产品 × 五个预设区间
    python -m jobs.synthesize --codes 3033,7226 --ranges d7,d30
    python -m jobs.synthesize --codes 3033 --ranges d7 --kinds hot_summary,summary --dry-run

## 一次「产品 × 区间」做什么

1. 取该产品区间内评论的**现行结论**（`radar_db.annotations_read`，与 SqlProvider 同一条规则）。
2. 把结论按 `backend/core/` 的口径分桶计数（主题、负面类别、话题、阶段时段）—— 这里 import 的
   是 backend 的叶子模块，**不是**在 worker 里再写一遍公式。
3. 每个桶分层抽样几条证据引文（优先用程序定位过的原文片段），带我们自己的 id。
4. 把「事实 JSON ＋ 带 id 的引文」发给模型，要它起名、写句子；校验字数、比例词、
   `evidence_ids ⊆ 输入 id`。
5. 写 `synthesis_outputs`。`input_fingerprint` 覆盖参与的 annotation_id 集合＋事实 JSON＋
   Prompt 版本：底层标注一变指纹就变，旧行被 supersede；没变就跳过，不花钱。

## 不调模型的情形

- 有效态度（正＋负）< LOW_SAMPLE ⇒ `hot_summary` / `summary` 写 `{"status": "low_sample"}`
  **不发请求**。PRD §3.5：样本不足不输出倾向结论。
- 桶里少于 `MIN_BUCKET` 条 ⇒ 不给它起名（core 会用 aspect 固定名兜底）。
- 时段样本不足 ⇒ 不判类别（core 会把它并入相邻阶段）。

## 为什么 import backend/core

`backend/core/__init__.py` 写着「若 worker 确需预聚合，抽一个 common/ 顶层包，绝不在两侧
各写一份」。抽包是另一次重构；这里先以只读方式 import 那几个叶子模块（themes / lifecycle /
stages / topics / calendar / attitude），它们不 import provider，在 worker 进程里能独立运行。
"""

import argparse
from contextlib import nullcontext
import hashlib
import json
import logging
import os
import random
import sys
import threading
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import insert, select, update

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = Path(__file__).resolve().parents[2]
for cand in (REPO_ROOT, Path(__file__).resolve().parents[1]):
    if (cand / "radar_db").is_dir() and str(cand) not in sys.path:
        sys.path.insert(0, str(cand))
    if (cand / "backend" / "core").is_dir() and str(cand / "backend") not in sys.path:
        sys.path.insert(0, str(cand / "backend"))

import clock  # noqa: E402
from ai import config, redact, synth  # noqa: E402
from ai.lexicon import product_aliases  # noqa: E402
from ai.providers import PermanentError, TransientError, build as build_provider  # noqa: E402
from ai.schemas import SchemaError  # noqa: E402
from core import stages as core_stages, themes as core_themes, topics as core_topics  # noqa: E402
from core.attitude import LOW_SAMPLE  # noqa: E402
from core.calendar import PRESETS, build as build_range  # noqa: E402
from radar_db import make_engine  # noqa: E402
from radar_db.annotations_read import current_annotations  # noqa: E402
from radar_db.events import emit  # noqa: E402
from radar_db.schema import (  # noqa: E402
    NO_SUBJECT,
    analysis_scopes,
    annotation_evidence,
    annotation_runs,
    comments,
    meta_kv,
    synthesis_outputs,
)

log = logging.getLogger("worker.synthesize")

MASTER = REPO_ROOT / "backend" / "fixtures" / "demo" / "master.json"
if not MASTER.exists():
    MASTER = Path(__file__).resolve().parents[1] / "backend" / "fixtures" / "demo" / "master.json"

KINDS = synth.KINDS
DEFAULT_RANGES = tuple(PRESETS)
MIN_BUCKET = 2          # 少于这么多条的桶不起名
EVIDENCE_PER_BUCKET = 6  # 每桶抽几条引文
EVIDENCE_MAX_CHARS = 140
COMPETITOR_TOP_K = 3
COMPETITOR_MIN_EVIDENCE = 3
WORKERS = 8              # (code, range) 并行度；每对内部顺序不变


# ── 工具 ────────────────────────────────────────────────────────────────


def new_run_id():
    return "synth-" + clock.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]


def load_master(path=MASTER):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def read_anchor(engine):
    with engine.connect() as conn:
        a = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "anchor")).scalar()
    return date.fromisoformat(a[:10]) if a else None


def _window(rng):
    lo = datetime.combine(date.fromisoformat(rng["from"]), time.min)
    hi = datetime.combine(date.fromisoformat(rng["to"]) + timedelta(days=1), time.min)
    return lo, hi


def _bench_window(rng):
    lo = datetime.combine(date.fromisoformat(rng["benchFrom"]), time.min)
    hi = datetime.combine(date.fromisoformat(rng["benchTo"]) + timedelta(days=1), time.min)
    return lo, hi


def _bucket_index(rng):
    origin = date.fromisoformat(rng["from"])
    gran = rng["gran"]

    def bi(u):
        ts = u.get("posted_at")
        if ts is None:
            return None
        off = (ts.date() - origin).days
        if gran == "hour":
            return off * 24 + ts.hour
        if gran == "day":
            return off
        return off // 7
    return bi


def detect_language(texts):
    """引文主流文字：粤语 > 繁体 > 简体。只看几个高频字，够选一种写法就行。"""
    trad = simp = canto = 0
    for t in texts:
        if any(w in t for w in ("呢隻", "唔", "咁", "嘅", "係", "冇", "啲", "喺", "嚟")):
            canto += 1
        trad += sum(ch in "這隻個們說為對於會來時間過還發現經開關無麼與買賣錢" for ch in t)
        simp += sum(ch in "这只个们说为对于会来时间过还发现经开关无么与买卖钱" for ch in t)
    if canto and canto >= len(texts) / 3:
        return "yue"
    return "zh-Hant" if trad > simp else "zh-Hans"


def fingerprint(kind, ann_ids, facts, cfg=None):
    material = json.dumps(
        {"kind": kind, "ann_ids": sorted(ann_ids), "facts": facts, "prompt": synth.VERSION,
         "model": cfg.model if cfg else None, "provider": cfg.provider if cfg else None,
         "taxonomy": cfg.taxonomy_version if cfg else None, "schema": "synth-v1"},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# ── 取原料 ──────────────────────────────────────────────────────────────


class Material:
    """一只产品一个区间的全部原料：判定单元、证据、基准期计数。"""

    def __init__(self, engine, code, rng, plex):
        self.code, self.rng, self.plex = code, rng, plex
        lo, hi = _window(rng)
        self.window = (lo, hi)
        rel = current_annotations(engine, "relevance", "comment", window=(lo, hi), subject_code=code)
        att = current_annotations(engine, "attitude", "comment", window=(lo, hi), subject_code=code)
        asp = current_annotations(engine, "aspect", "comment", window=(lo, hi), subject_code=code)
        mkt = current_annotations(engine, "market_direction", "comment", window=(lo, hi), subject_code=code)
        comp = current_annotations(engine, "compliance", "comment", window=(lo, hi), subject_code=code)

        self.ann_ids = set()
        self.units = []   # 相关且有态度
        for unit, a in att.items():
            r = rel.get(unit)
            if r is None or r["value"] != "relevant":
                continue
            self.ann_ids.update({a["annotation_id"], r["annotation_id"]})
            asp_row = asp.get(unit)
            if asp_row:
                self.ann_ids.add(asp_row["annotation_id"])
            self.units.append({
                "comment_id": unit[0], "attitude": a["value"],
                "aspects": (asp_row or {}).get("value") or [], "posted_at": a["posted_at"],
                "annotation_id": r["annotation_id"],
            })
        self.market_units = []
        for unit, m in mkt.items():
            if m["value"] in ("bullish", "bearish", "neutral"):
                self.ann_ids.add(m["annotation_id"])
                self.market_units.append({"comment_id": unit[0], "market_direction": m["value"],
                                          "posted_at": m["posted_at"], "annotation_id": m["annotation_id"]})
        self.compliance_hits = []
        for unit, c in comp.items():
            tags = (c["value"] or {}).get("tags") or []
            if tags:
                self.ann_ids.add(c["annotation_id"])
                self.compliance_hits.append({"comment_id": unit[0], "tags": tags,
                                             "rationale": (c["value"] or {}).get("rationale")})
        self.relevant_count = sum(1 for r in rel.values() if r["value"] == "relevant")
        self.pos = sum(1 for u in self.units if u["attitude"] == "positive")
        self.neg = sum(1 for u in self.units if u["attitude"] == "negative")
        self.neu = sum(1 for u in self.units if u["attitude"] == "neutral")

        # 基准期只要计数
        blo, bhi = _bench_window(rng)
        b_att = current_annotations(engine, "attitude", "comment", window=(blo, bhi), subject_code=code)
        b_rel = current_annotations(engine, "relevance", "comment", window=(blo, bhi), subject_code=code)
        b_asp = current_annotations(engine, "aspect", "comment", window=(blo, bhi), subject_code=code)
        self.base_units = None
        if b_att:
            self.base_units = [
                {"attitude": a["value"], "aspects": (b_asp.get(unit) or {}).get("value") or [],
                 "posted_at": a["posted_at"]}
                for unit, a in b_att.items()
                if (b_rel.get(unit) or {}).get("value") == "relevant"
            ]

        self._texts = None
        self._quotes = None
        self._engine = engine

    def texts(self):
        """`{comment_id: 脱敏正文}`，只取本区间用到的评论。"""
        if self._texts is None:
            ids = {u["comment_id"] for u in self.units} | {u["comment_id"] for u in self.market_units} \
                | {h["comment_id"] for h in self.compliance_hits}
            out = {}
            with self._engine.connect() as conn:
                for chunk in _chunks(sorted(ids), 900):
                    for cid, content in conn.execute(
                        select(comments.c.comment_id, comments.c.content).where(comments.c.comment_id.in_(chunk))
                    ):
                        out[cid] = redact.scrub_text(content or "")
            self._texts = out
        return self._texts

    def quotes(self):
        """`{annotation_id: 程序定位过的原文片段}`，有就优先当证据。"""
        if self._quotes is None:
            ids = sorted(self.ann_ids)
            out = {}
            with self._engine.connect() as conn:
                for chunk in _chunks(ids, 900):
                    for aid, q in conn.execute(
                        select(annotation_evidence.c.annotation_id, annotation_evidence.c.quote_text)
                        .where(annotation_evidence.c.annotation_id.in_(chunk))
                    ):
                        out.setdefault(aid, q)
            self._quotes = out
        return self._quotes

    def evidence_item(self, u):
        """一条判定单元 → `{id, text, day}`。id 是我们自己的，可回到 comment_id。"""
        quote = self.quotes().get(u.get("annotation_id"))
        text = quote or self.texts().get(u["comment_id"], "")
        text = text[:EVIDENCE_MAX_CHARS]
        ts = u.get("posted_at")
        return {"id": f"c{u['comment_id']}", "text": text, "day": ts.strftime("%m-%d") if ts else None}


def _chunks(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def sample_evidence(units, k, seed):
    """分层抽样：按天分组轮询取，长短各留，少数意见保留（不按点赞排）。"""
    rnd = random.Random(seed)
    by_day = defaultdict(list)
    for u in units:
        by_day[(u.get("posted_at") or datetime.min).date()].append(u)
    for b in by_day.values():
        rnd.shuffle(b)
    days = sorted(by_day)
    out, i = [], 0
    while len(out) < k and any(by_day.values()):
        d = days[i % len(days)]
        if by_day[d]:
            out.append(by_day[d].pop())
        i += 1
    return out


# ── 每种 kind 的原料组装 ───────────────────────────────────────────────────


def build_theme_payload(mat, kind):
    """`theme_label`（正负 × aspect）或 `neg_category`（负面 aspect）的桶清单与证据。"""
    bi = _bucket_index(mat.rng)
    buckets, evidence, allowed = [], [], set()
    if kind == "theme_label":
        grouped = core_themes.group_by_polarity(mat.units)
        for pol in ("positive", "negative"):
            by_asp = defaultdict(list)
            for u in grouped.get(pol, []):
                for a in (u["aspects"] or ["other"]):
                    by_asp[a].append(u)
            for asp, us in by_asp.items():
                if len(us) < MIN_BUCKET:
                    continue
                key = f"{pol}|{asp}"
                ev = [mat.evidence_item(u) for u in sample_evidence(us, EVIDENCE_PER_BUCKET, key)]
                buckets.append({"key": key, "polarity": pol, "aspect": asp,
                                "aspect_label": core_themes.ASPECT_LABEL.get(asp, asp),
                                "mentions": len(us), "evidence_ids": [e["id"] for e in ev]})
                evidence.extend(ev)
    else:
        neg = [u for u in mat.units if u["attitude"] == "negative"]
        by_asp = defaultdict(list)
        for u in neg:
            for a in (u["aspects"] or ["other"]):
                if a in core_themes.NEG_CATEGORY_ASPECTS:
                    by_asp[a].append(u)
        for asp, us in by_asp.items():
            if len(us) < MIN_BUCKET:
                continue
            ev = [mat.evidence_item(u) for u in sample_evidence(us, EVIDENCE_PER_BUCKET, asp)]
            buckets.append({"key": asp, "aspect_label": core_themes.ASPECT_LABEL.get(asp, asp),
                            "mentions": len(us), "evidence_ids": [e["id"] for e in ev]})
            evidence.extend(ev)
    for e in evidence:
        allowed.add(e["id"])
    _ = bi
    return {"buckets": buckets}, _dedupe_ev(evidence), allowed, [b["key"] for b in buckets]


def _dedupe_ev(evidence):
    seen, out = set(), []
    for e in evidence:
        if e["id"] not in seen and e["text"]:
            seen.add(e["id"])
            out.append(e)
    return out


def build_summary_payload(mat, labels):
    """`hot_summary` / `summary` 共用：态度计数、主题桶（带已生成的名字）、负面类别、合规、环比。"""
    grouped = core_themes.group_by_polarity(mat.units)
    bi = _bucket_index(mat.rng)
    th = core_themes.themes(mat.code, grouped, None, mat.rng["buckets"], bi, labels.get("theme_label"))
    nc = core_themes.neg_categories(mat.code, grouped.get("negative", []), None, mat.rng["buckets"], bi,
                                    labels.get("neg_category"))
    facts = {
        "attitude": {"positive": mat.pos, "negative": mat.neg, "neutral": mat.neu, "relevant": mat.relevant_count},
        "themes": {pol: [{"title": t["title"], "mentions": t["mentions"]} for t in th[pol][:4]] for pol in th},
        "negCategories": [{"label": c["label"], "mentions": c["mentions"]} for c in nc[:4]],
        "compliance": {"hits": len(mat.compliance_hits),
                       "tags": sorted({t for h in mat.compliance_hits for t in h["tags"]})},
    }
    if mat.base_units is not None:
        bp = sum(1 for u in mat.base_units if u["attitude"] == "positive")
        bn = sum(1 for u in mat.base_units if u["attitude"] == "negative")
        facts["baseline"] = {"positive": bp, "negative": bn}
    evidence = []
    for pol in ("positive", "negative"):
        for u in sample_evidence(grouped.get(pol, []), 8, f"sum-{pol}"):
            evidence.append({**mat.evidence_item(u), "polarity": pol})
    for h in mat.compliance_hits[:3]:
        text = mat.texts().get(h["comment_id"], "")[:EVIDENCE_MAX_CHARS]
        if text:
            evidence.append({"id": f"c{h['comment_id']}", "text": text, "polarity": "compliance", "tags": h["tags"]})
    evidence = _dedupe_ev(evidence)
    return facts, evidence, {e["id"] for e in evidence}


def build_topic_payload(mat):
    bi = _bucket_index(mat.rng)
    tp = core_topics.market_topic(mat.code, mat.market_units, mat.rng["buckets"], bi)
    if not tp:
        return None
    t = tp[0]
    ev = [mat.evidence_item(u) for u in sample_evidence(mat.market_units, 10, "topic")]
    ev = _dedupe_ev(ev)
    facts = {"topic": {"bullish": t["positive"], "bearish": t["negative"], "neutral": t["neutral"],
                       "mentions": t["mentions"]}}
    return facts, ev, {e["id"] for e in ev}


def build_stage_units(mat):
    """按标注计数造一份最小 series，只为切时段与判样本 —— 热度不在这里算（铁律 1）。"""
    rng = mat.rng
    nb = len(rng["buckets"])
    bi = _bucket_index(rng)
    counts = [[0, 0, 0, 0] for _ in range(nb)]  # mentions pos neg neu
    per_bucket_units = defaultdict(list)
    for u in mat.units:
        i = bi(u)
        if i is None or not 0 <= i < nb:
            continue
        counts[i][0] += 1
        counts[i][{"positive": 1, "negative": 2, "neutral": 3}[u["attitude"]]] += 1
        per_bucket_units[i].append(u)
    series = [
        {"i": b["i"], "day": b["day"], "hour": b.get("hour"), "label": b["label"], "tip": b["tip"],
         "heat": 0, "mentions": c[0], "comments": c[0], "positive": c[1], "negative": c[2], "neutral": c[3]}
        for b, c in zip(rng["buckets"], counts)
    ]
    half = rng["gran"] == "hour"
    units = core_stages.units_from_series(series, half)
    core_stages.classify_units(units, half)
    for u in units:
        u["members"] = [m for i in range(u["idxFrom"], u["idxTo"] + 1) for m in per_bucket_units.get(i, [])]
    return units, half


def build_stage_unit_payload(mat, units):
    facts_units, evidence = [], []
    for u in units:
        if not u["sufficient"]:
            continue
        ev = [mat.evidence_item(m) for m in sample_evidence(u["members"], EVIDENCE_PER_BUCKET, core_stages.unit_key(u))]
        facts_units.append({"key": core_stages.unit_key(u), "label": u["label"], "sub": u["sub"],
                            "positive": u["positive"], "negative": u["negative"], "neutral": u["neutral"],
                            "tone": u["tone"], "evidence_ids": [e["id"] for e in ev]})
        evidence.extend(ev)
    evidence = _dedupe_ev(evidence)
    return {"units": facts_units}, evidence, {e["id"] for e in evidence}, [f["key"] for f in facts_units]


def build_competitor_payload(mat, master, plex):
    """竞品候选：CMAP 固定对位 ∪ 本产品评论里被点名最多的池内产品。证据＝点名它的评论。"""
    by_code = {p["code"]: p for p in master["products"]}
    me = by_code.get(mat.code)
    if me is None:
        return None
    fixed = set()
    if me["ownership"] == "own":
        fixed = {p["code"] for p in master["products"] if p.get("ownCode") == mat.code}
    elif me.get("ownCode"):
        fixed = {me["ownCode"]}
    texts = mat.texts()
    hits = defaultdict(list)
    for u in mat.units:
        t = texts.get(u["comment_id"], "")
        for other in by_code:
            if other != mat.code and plex.references(t, other):
                hits[other].append(u)
    auto = sorted((c for c in hits if c not in fixed), key=lambda c: -len(hits[c]))[:COMPETITOR_TOP_K]
    comps, evidence = [], []
    for c in list(fixed) + auto:
        us = hits.get(c, [])
        if len(us) < COMPETITOR_MIN_EVIDENCE:
            continue
        ev = [mat.evidence_item(u) for u in sample_evidence(us, 8, f"comp-{c}")]
        comps.append({"code": c, "name": by_code[c]["name"], "relation": "confirmed" if c in fixed else "auto_candidate",
                      "mentions": len(us), "evidence_ids": [e["id"] for e in ev]})
        evidence.extend(ev)
    if not comps:
        return None
    evidence = _dedupe_ev(evidence)
    return {"competitors": comps}, evidence, {e["id"] for e in evidence}, [c["code"] for c in comps]


# ── 调模型与写库 ─────────────────────────────────────────────────────────


class Synthesizer:
    def __init__(self, engine, cfg, provider, master, *, dry_run=False, force=False):
        self.engine, self.cfg, self.provider = engine, cfg, provider
        self.master = master
        self.plex = product_aliases.ProductLexicon(master["products"])
        self.dry_run, self.force = dry_run, force
        self.run_id = new_run_id()
        self.stats = {"run_id": self.run_id, "calls": 0, "written": 0, "skipped_same": 0,
                      "low_sample": 0, "no_material": 0, "errors": 0,
                      "tok_in": 0, "tok_out": 0, "usage_known": True, "model": cfg.model}
        self._names = {p["code"]: p["name"] for p in master["products"]}
        # 多线程按 (code, range) 并行时，stats 的累加要加锁；每对的局部计数放线程本地
        # （一对从头到尾都在同一个线程里跑），`one()` 结束时回报给调用方写事件与标脏。
        self._lock = threading.Lock()
        self._tls = threading.local()
        self.stop = threading.Event()

    def _inc(self, key, n=1):
        with self._lock:
            self.stats[key] += n
        local = getattr(self._tls, "counts", None)
        if local is not None and key in local:
            local[key] += n

    # 一条 (code, range) 的全流程。返回本对的局部计数 `{calls, written, errors, skipped_same, low_sample}`。
    def one(self, code, range_key, anchor, kinds):
        self._tls.counts = {"calls": 0, "written": 0, "errors": 0, "skipped_same": 0, "low_sample": 0}
        if self.stop.is_set():
            return self._tls.counts
        rng = build_range(range_key, anchor)
        mat = Material(self.engine, code, rng, self.plex)
        if not mat.units and not mat.market_units:
            self._inc("no_material")
            return self._tls.counts
        product = {"code": code, "name": self._names.get(code, code)}
        rng_info = {"key": range_key, "from": rng["from"], "to": rng["to"], "label": rng["label"]}
        labels = {}

        if "theme_label" in kinds:
            labels["theme_label"] = self._bucket_kind(mat, "theme_label", product, rng_info, anchor)
        if "neg_category" in kinds:
            labels["neg_category"] = self._bucket_kind(mat, "neg_category", product, rng_info, anchor)

        sufficient = (mat.pos + mat.neg) >= LOW_SAMPLE
        for kind in ("hot_summary", "summary"):
            if kind not in kinds:
                continue
            if not sufficient:
                self._write_low_sample(code, range_key, anchor, kind, mat)
                continue
            facts, evidence, allowed = build_summary_payload(mat, labels)
            self._generate(code, range_key, anchor, kind, NO_SUBJECT, mat.ann_ids, facts, evidence, allowed,
                           product, rng_info, lambda obj: obj.model_dump())

        if "topic_label" in kinds:
            tp = build_topic_payload(mat)
            if tp:
                facts, evidence, allowed = tp
                self._generate(code, range_key, anchor, "topic_label", core_topics.MARKET_SUBKEY,
                               {u["annotation_id"] for u in mat.market_units}, facts, evidence, allowed,
                               product, rng_info, lambda obj: obj.model_dump())

        if "stage_unit" in kinds or "stage_summary" in kinds:
            self._stages(mat, product, rng_info, anchor, kinds)

        if "competitor_reason" in kinds:
            cp = build_competitor_payload(mat, self.master, self.plex)
            if cp:
                facts, evidence, allowed, keys = cp
                self._generate_batch(code, range_key, anchor, "competitor_reason", mat.ann_ids, facts, evidence,
                                     allowed, keys, product, rng_info, key_attr="code")
        return self._tls.counts

    def _bucket_kind(self, mat, kind, product, rng_info, anchor):
        facts, evidence, allowed, keys = build_theme_payload(mat, kind)
        if not keys:
            return {}
        rows = self._generate_batch(mat.code, rng_info["key"], anchor, kind, mat.ann_ids, facts, evidence,
                                    allowed, keys, product, rng_info)
        out = {}
        for r in rows or []:
            k = r["key"]
            key = tuple(k.split("|", 1)) if kind == "theme_label" else k
            out[key] = {"title": r["title"], "summary": r["summary"], "evidence_ids": r["evidence_ids"]}
        return out

    def _stages(self, mat, product, rng_info, anchor, kinds):
        units, half = build_stage_units(mat)
        cats = {}
        if "stage_unit" in kinds:
            facts, evidence, allowed, keys = build_stage_unit_payload(mat, units)
            if keys:
                rows = self._generate_batch(mat.code, rng_info["key"], anchor, "stage_unit",
                                            {m["annotation_id"] for u in units for m in u["members"]},
                                            facts, evidence, allowed, keys, product, rng_info) or []
                cats = {r["key"]: r for r in rows}
        if "stage_summary" in kinds and cats:
            for u in units:
                u["cat"] = (cats.get(core_stages.unit_key(u)) or {}).get("category")
                u["cat"] = u["cat"] if u["cat"] in core_stages.STAGE_CATS else None
            merged = core_stages.merge_units(units, half, mat.rng["days"])
            facts_st, evidence, keys = [], [], []
            for s in merged:
                if not s["cat"] or len(s["units"]) < 2:
                    continue
                key = core_stages.stage_key(s)
                digests = [{"label": u["label"], "digest": (cats.get(core_stages.unit_key(u)) or {}).get("digest")}
                           for u in s["units"] if u.get("cat")]
                ev = []
                for u in s["units"]:
                    ev += [mat.evidence_item(m) for m in sample_evidence(u["members"], 3, key + u["label"])]
                ev = _dedupe_ev(ev)
                facts_st.append({"key": key, "category": s["cat"], "from": s["units"][0]["day"],
                                 "to": s["units"][-1]["day"], "digests": digests,
                                 "evidence_ids": [e["id"] for e in ev]})
                evidence.extend(ev)
                keys.append(key)
            if keys:
                evidence = _dedupe_ev(evidence)
                self._generate_batch(mat.code, rng_info["key"], anchor, "stage_summary",
                                     {m["annotation_id"] for u in units for m in u["members"]},
                                     {"stages": facts_st}, evidence, {e["id"] for e in evidence}, keys,
                                     product, rng_info)

    # ── 调用与落库 ──

    def _call(self, kind, payload, allowed, expected_keys):
        self._inc("calls")
        comp = self.provider.complete_json(synth.system_prompt(kind), synth.user_message(kind, payload),
                                           synth.json_schema(kind), f"synth_{kind}")
        u = comp.usage
        with self._lock:
            if u.input_tokens is None or u.output_tokens is None:
                self.stats["usage_known"] = False
            else:
                self.stats["tok_in"] += u.input_tokens
                self.stats["tok_out"] += u.output_tokens
            self.stats["model"] = comp.model
        return synth.parse(kind, comp.data, allowed, expected_keys)

    def _payload(self, product, rng_info, facts, evidence):
        payload = {"product": product, "range": rng_info,
                   "language": detect_language([e["text"] for e in evidence]) if evidence else "zh-Hans",
                   "facts": facts, "evidence": evidence}
        redact.assert_clean(payload)
        return payload

    def _generate(self, code, range_key, anchor, kind, subkey, ann_ids, facts, evidence, allowed,
                  product, rng_info, to_value):
        fp = fingerprint(kind, ann_ids, facts, self.cfg)
        if not self.force and self._exists(code, range_key, anchor, kind, subkey, fp):
            self._inc("skipped_same")
            return None
        if self.dry_run:
            log.info("[dry-run] %s %s %s subkey=%s 证据 %d 条", code, range_key, kind, subkey, len(evidence))
            return None
        try:
            obj = self._call(kind, self._payload(product, rng_info, facts, evidence), allowed, None)
        except (SchemaError, TransientError) as exc:
            self._inc("errors")
            log.warning("%s %s %s 失败：%s", code, range_key, kind, str(exc)[:200])
            return None
        value = to_value(obj)
        review = "needs_review" if getattr(obj, "needs_review", False) else "pending"
        ids = getattr(obj, "evidence_ids", None)
        if ids is None and hasattr(obj, "points"):
            ids = sorted({i for p in obj.points for i in p.evidence_ids})
        self._write(code, range_key, anchor, kind, subkey, fp, value, ids, review)
        return value

    def _generate_batch(self, code, range_key, anchor, kind, ann_ids, facts, evidence, allowed, keys,
                        product, rng_info, key_attr="key"):
        """批式 kind：一次调用给全部桶／时段／竞品，每个键落一行。指纹按整批算。"""
        fp = fingerprint(kind, ann_ids, facts, self.cfg)
        existing = self._existing_rows(code, range_key, anchor, kind, fp)
        if not self.force and {row[key_attr] for row in existing} == set(keys):
            self._inc("skipped_same")
            return existing
        if self.dry_run:
            log.info("[dry-run] %s %s %s ×%d 证据 %d 条", code, range_key, kind, len(keys), len(evidence))
            return None
        try:
            obj = self._call(kind, self._payload(product, rng_info, facts, evidence), allowed, keys)
        except (SchemaError, TransientError) as exc:
            self._inc("errors")
            log.warning("%s %s %s 失败：%s", code, range_key, kind, str(exc)[:200])
            return None
        rows = []
        with self.engine.begin() as conn:
            for r in obj.results:
                value = r.model_dump()
                review = "needs_review" if value.get("needs_review") else "pending"
                self._write(code, range_key, anchor, kind, getattr(r, key_attr), fp, value, r.evidence_ids, review, conn)
                rows.append(value)
        return rows

    def _write_low_sample(self, code, range_key, anchor, kind, mat):
        fp = fingerprint(kind, mat.ann_ids, {"status": "low_sample", "pos": mat.pos, "neg": mat.neg}, self.cfg)
        if self._exists(code, range_key, anchor, kind, NO_SUBJECT, fp):
            self._inc("skipped_same")
            return
        self._inc("low_sample")
        if not self.dry_run:
            self._write(code, range_key, anchor, kind, NO_SUBJECT, fp,
                        {"status": "low_sample", "sample": mat.pos + mat.neg}, [], "pending")

    def _exists(self, code, range_key, anchor, kind, subkey, fp):
        with self.engine.connect() as conn:
            return conn.execute(
                select(synthesis_outputs.c.synthesis_id).where(
                    synthesis_outputs.c.code == code, synthesis_outputs.c.range_key == range_key,
                    synthesis_outputs.c.anchor == anchor.isoformat(), synthesis_outputs.c.kind == kind,
                    synthesis_outputs.c.subkey == subkey, synthesis_outputs.c.input_fingerprint == fp,
                ).limit(1)
            ).first() is not None

    def _existing_rows(self, code, range_key, anchor, kind, fp):
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(synthesis_outputs.c.value_json).where(
                    synthesis_outputs.c.code == code, synthesis_outputs.c.range_key == range_key,
                    synthesis_outputs.c.anchor == anchor.isoformat(), synthesis_outputs.c.kind == kind,
                    synthesis_outputs.c.input_fingerprint == fp,
                )
            ).all()
        return [json.loads(r[0]) for r in rows]

    def _write(self, code, range_key, anchor, kind, subkey, fp, value, evidence_ids, review, connection=None):
        now = clock.now()
        with (nullcontext(connection) if connection is not None else self.engine.begin()) as conn:
            duplicate = conn.execute(select(synthesis_outputs.c.synthesis_id).where(
                synthesis_outputs.c.code == code, synthesis_outputs.c.range_key == range_key,
                synthesis_outputs.c.anchor == anchor.isoformat(), synthesis_outputs.c.kind == kind,
                synthesis_outputs.c.subkey == subkey, synthesis_outputs.c.input_fingerprint == fp,
            )).first()
            if duplicate:
                return
            newer = synthesis_outputs.alias("newer")
            prev = conn.execute(
                select(synthesis_outputs.c.synthesis_id).where(
                    synthesis_outputs.c.code == code, synthesis_outputs.c.range_key == range_key,
                    synthesis_outputs.c.anchor == anchor.isoformat(), synthesis_outputs.c.kind == kind,
                    synthesis_outputs.c.subkey == subkey,
                    ~select(newer.c.synthesis_id).where(newer.c.supersedes_id == synthesis_outputs.c.synthesis_id).exists(),
                ).order_by(synthesis_outputs.c.synthesis_id.desc()).limit(1)
            ).scalar()
            conn.execute(
                insert(synthesis_outputs).values(
                    code=code, range_key=range_key, anchor=anchor.isoformat(), kind=kind, subkey=subkey,
                    input_fingerprint=fp, value_json=json.dumps(value, ensure_ascii=False),
                    evidence_ids_json=json.dumps(list(evidence_ids or []), ensure_ascii=False),
                    run_id=self.run_id, review_state=review, created_at=now, supersedes_id=prev,
                )
            )
        self._inc("written")

    # ── run 记录 ──

    def open_run(self):
        if self.dry_run:
            return
        with self.engine.begin() as conn:
            conn.execute(insert(annotation_runs).values(
                run_id=self.run_id, task="synthesize", provider=self.cfg.provider, model_id=self.cfg.model,
                prompt_version=synth.VERSION, taxonomy_version=self.cfg.taxonomy_version,
                schema_version="synth-v1", started_at=clock.now(), status="running",
                input_count=0, success_count=0, error_count=0,
            ))

    def close_run(self):
        if self.dry_run:
            return
        s = self.stats
        from radar_db.revisions import bump_revision
        with self.engine.begin() as conn:
            if s["written"]:
                bump_revision(conn, "synthesis")
            conn.execute(update(annotation_runs).where(annotation_runs.c.run_id == self.run_id).values(
                finished_at=clock.now(), status="done" if s["errors"] == 0 else "partial",
                model_id=s["model"], input_count=s["calls"], success_count=s["written"], error_count=s["errors"],
                token_input=s["tok_in"] if s["usage_known"] else None,
                token_output=s["tok_out"] if s["usage_known"] else None,
            ))


def run(engine, cfg, *, codes=None, ranges=DEFAULT_RANGES, kinds=KINDS, provider=None, dry_run=False, force=False,
        master=None, pairs=None, workers=WORKERS, scope_id=None):
    """按 `(code, range)` 并行跑 Layer B。

    `pairs` 给了就只做这些 `(code, range)`（`pipeline.run` 逐区间判「就绪」后传进来）；不给就是
    `codes × ranges` 的全集。每对内部顺序不变（主题起名 → 总结 → 话题 → 阶段 → 竞品），对与对
    之间用 `workers` 个线程并行：一对里的每次模型调用都在等网络，8 个线程让 61 只产品 × 6 个
    区间的尾部从 40 分钟收到 5–10 分钟（runbook §24）。写库集中在 `_write`，每次自己
    `engine.begin()` 拿连接；SQLite 的 busy_timeout 已在 `make_engine` 里设好。

    每对写完记一条 L3 事件；没出错的对把 `synth_dirty_<code>_<range>` 清零。出过错的对**不清**：
    脏标记保持「待更新」，下一轮再来。
    """
    master = master or load_master()
    anchor = read_anchor(engine)
    if anchor is None:
        raise SystemExit("meta_kv 里没有 anchor：先跑 import_dump / etl")
    if pairs is None:
        if codes is None:
            raise ValueError("给 codes 或 pairs")
        pairs = [(code, rk) for code in codes for rk in ranges]
    pairs = list(pairs)
    provider = provider or (None if dry_run else build_provider(cfg))
    s = Synthesizer(engine, cfg, provider, master, dry_run=dry_run, force=force)
    s.open_run()
    clean, permanent = [], None
    full_kinds = set(kinds) == set(KINDS)
    try:
        with ThreadPoolExecutor(max_workers=max(1, int(workers or 1)), thread_name_prefix="synth") as pool:
            futures = {pool.submit(s.one, code, rk, anchor, set(kinds)): (code, rk) for code, rk in pairs}
            for fut in as_completed(futures):
                code, rk = futures[fut]
                try:
                    counts = fut.result()
                except PermanentError as exc:
                    # 401/400 这类：别的对再发也是同一个错。让还没开始的对直接返回，整轮报错。
                    if permanent is None:
                        permanent = exc
                        log.error("永久错误，中止：%s", exc)
                    s._inc("errors")
                    s.stop.set()
                    continue
                if s.stop.is_set() and not counts["calls"] and not counts["written"]:
                    continue
                if not dry_run and counts["errors"] == 0:
                    clean.append((code, rk))
                if not dry_run and (counts["written"] or counts["errors"]):
                    msg = f"{code} {rk} 汇总写入 {counts['written']}（调用 {counts['calls']}）"
                    if counts["errors"]:
                        msg += f" / 失败 {counts['errors']}"
                    emit(engine, "L3", msg, level="warn" if counts["errors"] else "info", code=code,
                         scope_id=scope_id, run_id=s.run_id, data={"range": rk, **counts})
    finally:
        s.close_run()
    if permanent is not None:
        raise permanent
    if not dry_run and full_kinds and clean:
        from radar_db.revisions import bump_revision, mark_synthesis
        with engine.begin() as conn:
            for code, rk in clean:
                mark_synthesis(conn, [code], False, [rk])
            bump_revision(conn, "synthesis")
    s.stats["pairs"] = len(pairs)
    s.stats["pairs_clean"] = len(clean)
    return s.stats


def scope_codes(engine, scope_id):
    with engine.connect() as conn:
        row = conn.execute(select(analysis_scopes.c.codes_json).where(analysis_scopes.c.scope_id == scope_id)).scalar()
    if row is None:
        raise SystemExit(f"没有这个 scope：{scope_id}")
    return json.loads(row)


def main(argv=None):
    ap = argparse.ArgumentParser(description="产品 × 区间生成物（Layer B）")
    ap.add_argument("--scope", help="用这个抽取范围里的产品")
    ap.add_argument("--codes", help="逗号分隔的产品代码")
    ap.add_argument("--ranges", default=",".join(DEFAULT_RANGES))
    ap.add_argument("--kinds", default=",".join(KINDS))
    ap.add_argument("--dry-run", action="store_true", help="只组原料不调模型")
    ap.add_argument("--force", action="store_true", help="指纹相同也重生成")
    ap.add_argument("--workers", type=int, default=WORKERS, help="(code, range) 并行线程数")
    args = ap.parse_args(argv)
    if not args.scope and not args.codes:
        ap.error("给 --scope 或 --codes")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s %(message)s")
    engine = make_engine()
    cfg = config.load(_allow_missing_key=args.dry_run)
    codes = scope_codes(engine, args.scope) if args.scope else [c.strip() for c in args.codes.split(",")]
    stats = run(engine, cfg, codes=codes, ranges=[r.strip() for r in args.ranges.split(",")],
                kinds=[k.strip() for k in args.kinds.split(",")], dry_run=args.dry_run, force=args.force,
                workers=args.workers)
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
