"""近重复折叠（ADR-0021 L0 第 6 条）—— 同产品同日里几乎一样的评论只判一次。

## 为什么要有它、为什么它不是「判无关」

`prefilter.exact_duplicate` 只抓**同作者**同帖同正文。评论区里更常见的是不同人、不同帖、
差一个标点或一个表情的同一句话（「跌下来正好继续加这只」×40）。每条都问模型是同一个答案
花几十次钱；每条都过学生模型也是几十次推理。折叠之后：簇代表照常走 L1/L2，成员**复制**
代表的结论 —— 它们不是被剔掉了，页面上仍然算一条相关／积极的评论，只是判定是抄来的。

所以成员写的是 `duplicate_cluster={"of": <代表 comment_id>}` 加一份代表标签的副本
（`annotation_runs.provider='propagated'`），**不是** `relevance=irrelevant`。
`prefilter.RULES` 一条都不加。

## 算法

正文归一化（去空白、全角转半角、去表情占位与标的标签、小写）→ 字符 bigram 特征 →
64 位 simhash → 同 `(subject_code, 帖子日)` 桶内汉明距 ≤ `HAMMING_MAX` 归为一簇。
候选查找用 4 段分桶（鸽巢：距离 ≤3 的两个指纹至少有一段 16 位完全相同），
桶内两两比对只在同段命中的候选上做。纯 Python，不引第三方；实测约 8–10k 条/秒。

同日限制是有意的：三天前的同一句话是另一次表态，不该折叠。

## 传播时机

成员的标签在**代表的标签写入时**推过去（`propagate()`），学生写、Luna 写各触发一次。
所以成员不进队列：`extract` 只给它们写 `duplicate_cluster` 行，代表才排任务。Luna 后来
supersede 了代表的学生行，再推一次，成员自然跟着换 —— 成员的链和代表的链形状一样。
"""

import hashlib
import json
import re
import unicodedata
from collections import defaultdict

from sqlalchemy import insert, select

VERSION = "neardup-v1"
HAMMING_MAX = 3
MIN_CHARS = 2  # 归一化后不足这么多字的文本不折叠：它们几乎都被 sticker_only 拦下了

_PLACEHOLDER = re.compile(r"\[[^\[\]]{1,12}\]|@\[用户\]|@[\w一-鿿]{1,30}|https?://\S+|\[链接\]")
_TAG = re.compile(r"\$[A-Za-z0-9.]{1,12}\.(?:HK|US|SH|SZ|hk|us|sh|sz)\$")
_SPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[，。！？、；：,.!?;:~～…—\-「」『』“”\"'（）()\[\]【】《》<>]")


def normalize(text):
    """折叠用的正文归一化。**只用于比对**，不改库里的正文。"""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)  # 全角 → 半角、兼容字统一
    t = _TAG.sub("", t)
    t = _PLACEHOLDER.sub("", t)
    t = _PUNCT.sub("", t)
    t = _SPACE.sub("", t)
    return t.lower()


def _features(norm):
    if len(norm) < 2:
        return [norm] if norm else []
    return [norm[i:i + 2] for i in range(len(norm) - 1)]


def simhash64(norm):
    """Charikar simhash，64 位。特征哈希用 blake2b（跨进程、跨版本稳定；Python 的 hash() 带随机盐）。"""
    feats = _features(norm)
    if not feats:
        return 0
    v = [0] * 64
    for f in feats:
        h = int.from_bytes(hashlib.blake2b(f.encode("utf-8"), digest_size=8).digest(), "big")
        for i in range(64):
            v[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(64):
        if v[i] > 0:
            out |= 1 << i
    return out


def hamming(a, b):
    return bin(a ^ b).count("1")


class Clusterer:
    """一个 `(subject_code, 帖子日)` 桶一个实例；`add()` 返回代表 comment_id（自己是代表则返回自己）。"""

    def __init__(self, max_distance=HAMMING_MAX):
        self.max_distance = max_distance
        self._bands = [defaultdict(list) for _ in range(4)]  # band_idx → {16 位片段: [(hash, rep_id)]}
        self._reps = {}  # comment_id → (hash, rep_id)

    def add(self, comment_id, text):
        norm = normalize(text)
        if len(norm) < MIN_CHARS:
            return comment_id, None
        h = simhash64(norm)
        best, best_d = None, None
        for b in range(4):
            seg = (h >> (16 * b)) & 0xFFFF
            for other_h, rep in self._bands[b].get(seg, ()):
                d = hamming(h, other_h)
                if d <= self.max_distance and (best_d is None or d < best_d):
                    best, best_d = rep, d
                    if d == 0:
                        break
            if best_d == 0:
                break
        if best is not None and best != comment_id:
            return best, best_d
        for b in range(4):
            self._bands[b][(h >> (16 * b)) & 0xFFFF].append((h, comment_id))
        self._reps[comment_id] = h
        return comment_id, None


def fold(rows, *, key_of, id_of, text_of):
    """把一批候选按桶折叠。返回 `(代表列表, 成员列表)`；成员元素为 `(row, 代表 id, 距离)`。

    `rows` 是任意可迭代；三个取值函数把它解耦于具体的行形状（extract 的 Row 与测试的 dict）。
    """
    by_key = defaultdict(list)
    for r in rows:
        by_key[key_of(r)].append(r)
    reps, members = [], []
    for key in sorted(by_key, key=str):
        cl = Clusterer()
        for r in by_key[key]:
            rep, d = cl.add(id_of(r), text_of(r))
            if rep == id_of(r):
                reps.append(r)
            else:
                members.append((r, rep, d))
    return reps, members


# ── 落库 ─────────────────────────────────────────────────────────────


def cluster_input_hash(member_id, code, rep_id):
    material = json.dumps({"member": member_id, "code": code, "of": rep_id, "version": VERSION}, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def cluster_value(rep_id):
    return json.dumps({"of": rep_id}, ensure_ascii=False, sort_keys=True)


def write_cluster_rows(engine, run_id, members, now):
    """给成员写 `duplicate_cluster` ＋ `text_quality(near_duplicate_of)` 两行。幂等。

    `members`：`[(comment_id, code, rep_comment_id, distance), ...]`。`run_id` 是 extract 的
    规则 run（provider='rule'）—— 折叠本身是规则判定，标签副本才是 `propagated`。
    """
    from radar_db.schema import annotations

    written = 0
    with engine.begin() as conn:
        for cid, code, rep, d in members:
            h = cluster_input_hash(cid, code, rep)
            exists = conn.execute(
                select(annotations.c.annotation_id).where(
                    annotations.c.target_type == "comment", annotations.c.target_id == cid,
                    annotations.c.subject_code == code, annotations.c.kind == "duplicate_cluster",
                    annotations.c.input_hash == h,
                ).limit(1)
            ).first()
            if exists:
                continue
            for kind, value in (
                ("duplicate_cluster", {"of": rep}),
                ("text_quality", {"rule": "near_duplicate", "near_duplicate_of": rep, "distance": d}),
            ):
                conn.execute(insert(annotations).values(
                    target_type="comment", target_id=cid, subject_code=code, kind=kind,
                    value_json=json.dumps(value, ensure_ascii=False, sort_keys=True),
                    calibrated_confidence=None, run_id=run_id, input_hash=h,
                    review_state="pending", created_at=now, supersedes_id=None,
                ))
            written += 1
    return written


def ensure_propagation_run(conn, base_run_id, now, *, taxonomy_version="", schema_version=""):
    """`prop-<base_run_id>`：一次学生／Luna 运行对应一条 propagated 运行记录。"""
    from radar_db.schema import annotation_runs

    run_id = ("prop-" + base_run_id)[:40]
    if conn.execute(select(annotation_runs.c.run_id).where(annotation_runs.c.run_id == run_id)).first() is None:
        conn.execute(insert(annotation_runs).values(
            run_id=run_id, task="comment_product", provider="propagated", model_id=VERSION,
            model_revision=None, prompt_version=VERSION, taxonomy_version=taxonomy_version,
            schema_version=schema_version, started_at=now, finished_at=None, status="running",
            input_count=0, success_count=0, error_count=0,
            token_input=None, token_output=None, token_reasoning=None,
        ))
    return run_id


PROPAGATED_KINDS = ("relevance", "attitude", "aspect")


def members_of(conn, rep_id, code):
    """当前把 `rep_id` 当代表的成员 comment_id 列表。"""
    from radar_db.schema import annotations

    newer = annotations.alias("newer")
    chain_end = ~select(newer.c.annotation_id).where(newer.c.supersedes_id == annotations.c.annotation_id).exists()
    return [r[0] for r in conn.execute(
        select(annotations.c.target_id).where(
            annotations.c.kind == "duplicate_cluster", annotations.c.target_type == "comment",
            annotations.c.subject_code == code, annotations.c.value_json == cluster_value(rep_id),
            annotations.c.review_state != "rejected", chain_end,
        ).distinct()
    )]


def propagate(conn, rep_id, code, rows, base_run_id, now, *, taxonomy_version="", schema_version=""):
    """把代表刚写下的标签推给成员。`rows`：`[(kind, value_json, calibrated_confidence, review_state, rep_annotation_id)]`。

    每个成员每个 kind 写一行副本，`input_hash` 含代表那一行的 annotation_id：代表换了一版
    结论，副本的指纹跟着变，是一条新行并 supersede 旧副本；同一版不重复写。人工裁决过的
    成员行不被 supersede（与 `annotate._write` 同一条纪律）。返回写入的行数。
    """
    from radar_db.schema import annotations

    members = members_of(conn, rep_id, code)
    if not members:
        return 0
    run_id = ensure_propagation_run(conn, base_run_id, now, taxonomy_version=taxonomy_version,
                                    schema_version=schema_version)
    written = 0
    for cid in members:
        for kind, value_json, conf, review_state, rep_ann_id in rows:
            if kind not in PROPAGATED_KINDS:
                continue
            h = hashlib.sha256(f"{cid}|{code}|{kind}|{rep_ann_id}|{VERSION}".encode("utf-8")).hexdigest()
            dup = conn.execute(select(annotations.c.annotation_id).where(
                annotations.c.target_type == "comment", annotations.c.target_id == cid,
                annotations.c.subject_code == code, annotations.c.kind == kind,
                annotations.c.input_hash == h,
            ).limit(1)).first()
            if dup:
                continue
            prev = conn.execute(
                select(annotations.c.annotation_id, annotations.c.review_state).where(
                    annotations.c.target_type == "comment", annotations.c.target_id == cid,
                    annotations.c.subject_code == code, annotations.c.kind == kind,
                ).order_by(annotations.c.annotation_id.desc()).limit(1)
            ).first()
            supersedes = prev.annotation_id if prev is not None and prev.review_state not in ("approved", "corrected") else None
            conn.execute(insert(annotations).values(
                target_type="comment", target_id=cid, subject_code=code, kind=kind,
                value_json=value_json, calibrated_confidence=conf, run_id=run_id, input_hash=h,
                review_state=review_state if review_state == "needs_review" else "pending",
                created_at=now, supersedes_id=supersedes,
            ))
            written += 1
    return written
