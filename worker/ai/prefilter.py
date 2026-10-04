"""规则预过滤 —— 分析前把**明显不用问模型**的评论挑出来。

## 立场

这层的目标是省钱和降噪，不是替模型判断。五条规则里前四条（空文本、纯表情、纯标签、
精确重复）没有任何语义判断；第五条「只聊个股、没提这只 ETF」有一点，所以它带开关，
并且步骤 11 会抽 100 条被它剔掉的评论人工看误杀率。

**判不出就放行。** 任何一条规则拿不准（比如既有个股名又有「呢隻」），一律交给模型 ——
模型 Prompt 里的 `relevance` 是第二道、也是最终的一道过滤。

## 落库形态

被剔除的评论**不是消失**，而是以 `provider="rule"` 写两行 `annotations`：

- `kind=relevance, value="irrelevant"` —— 与模型结论同一个 kind、同一套读取规则（ADR-0019），
  所以 `backend/core/` 数「无关」的时候不用区分是谁判的。
- `kind=text_quality, value={"rule": "...", "detail": ...}` —— 记下**为什么**。

这样它们可追溯（`annotation_runs.provider='rule'`）、可回滚（`review.py --reject`）、
可审计（`audit.py` 按规则分组计数）。丢掉不写的后果是「这条评论没标过」和「这条评论
被规则剔了」在库里变成同一个样子，而页面上它们相反（暂不可用 vs 已完成检查）。
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import insert, select, update

VERSION = "prefilter-v1"

RULES = ("empty", "sticker_only", "tag_only", "exact_duplicate", "offpool_stock_only")

# ETL 把表情还原成 `[捂脸]`、`[表情]`；链接与 @ 已被 redact 换成占位。
_PLACEHOLDER = re.compile(r"\[[^\[\]]{1,12}\]|@\[用户\]|@[\w一-鿿]{1,30}|https?://\S+")
_TAG = re.compile(r"\$[A-Za-z0-9.]{1,12}\.(?:HK|US|SH|SZ|hk|us|sh|sz)\$")
# Unicode 表情：杂项符号、补充符号与象形文字、变体选择符、零宽连接符、肤色修饰。
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF"
    "\U0000FE0F\U0000200D\U0001F3FB-\U0001F3FF\U00002B00-\U00002BFF\U00003030\U0000303D]"
)
# 有意义的文字：CJK、假名、谚文、拉丁字母。数字与标点不算 ——「666」「？？？」是纯灌水。
_LETTERS = re.compile(r"[一-鿿㐀-䶿぀-ヿ가-힣A-Za-z]")


@dataclass
class Decision:
    rule: Optional[str]  # None ＝ 放行
    detail: dict = field(default_factory=dict)

    @property
    def dropped(self):
        return self.rule is not None


def _strip_noise(text):
    t = _TAG.sub("", text)
    t = _PLACEHOLDER.sub("", t)
    t = _EMOJI.sub("", t)
    return t


def dup_key(author_uid, feed_id, text):
    """精确重复的键：同作者、同帖、正文去空白后一致。"""
    norm = re.sub(r"\s+", "", text or "").lower()
    return hashlib.sha256(f"{author_uid or ''}|{feed_id}|{norm}".encode("utf-8")).hexdigest()


class Prefilter:
    """带状态的过滤器（要记住见过哪些正文才能判重复）。一个 scope 一个实例。"""

    def __init__(self, plex, slex, *, drop_offpool=True):
        self.plex = plex
        self.slex = slex
        self.drop_offpool = drop_offpool
        self._seen = {}  # dup_key → 首次出现的 comment_id

    def classify(self, text, code, *, comment_id, author_uid=None, feed_id=None):
        # 1. 空
        if text is None or not text.strip():
            return Decision("empty")

        stripped = _strip_noise(text)
        letters = _LETTERS.search(stripped)

        if not letters:
            # 3. 有标的标签但没有任何文字：`$00700.HK$` 单独一条。
            if _TAG.search(text):
                return Decision("tag_only", {"tags": _TAG.findall(text)[:5]})
            # 2. 只剩表情占位／emoji／数字／标点：`[捂脸][捂脸]`、`666`、`？？？`。
            return Decision("sticker_only", {"residue": stripped.strip()[:20]})

        # 4. 精确重复（同作者同帖同正文）。作者未知时不判：两个匿名用户写同一句「同意」
        #    不是重复，是两票。
        if author_uid:
            k = dup_key(author_uid, feed_id, text)
            first = self._seen.get(k)
            if first is not None and first != comment_id:
                return Decision("exact_duplicate", {"kept_comment_id": first})
            self._seen.setdefault(k, comment_id)

        # 5. 只聊个股、没提这只 ETF
        if self.drop_offpool:
            stocks = self.slex.stocks_in(text, ignore_terms=self.plex.underlying_terms(code))
            if stocks and not self.plex.references(text, code) and not self.plex.mentions_deictic(text):
                return Decision("offpool_stock_only", {"stocks": sorted(stocks)[:5]})

        return Decision(None)


# ── 落库 ─────────────────────────────────────────────────────────────


def rule_input_hash(text, rule, version=VERSION, detail=None):
    """规则输入指纹，包含会改变 exact 结论的上下文事实。

    ``decided_at`` 是审计时间而不是判定输入，不能让每次重跑都生成新 hash。父帖资格、
    命中 ticker 与 reason 则必须进入指纹，否则正文相同但父帖被编辑后无法区分。
    """
    context = {
        key: value
        for key, value in (detail or {}).items()
        if key != "decided_at"
    }
    material = json.dumps(
        {"text": text or "", "rule": rule, "version": version, "context": context},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def open_rule_run(engine, run_id, task, now, *, taxonomy_version, schema_version):
    """给这一轮规则判定开一条 `annotation_runs`，provider='rule'。

    规则不花 token，用量三列写 NULL 而不是 0：这一轮**不存在**「用了多少 token」这个量。
    """
    from radar_db.schema import annotation_runs

    with engine.begin() as conn:
        conn.execute(
            insert(annotation_runs).values(
                run_id=run_id, task=task, provider="rule", model_id=VERSION,
                model_revision=None, prompt_version=VERSION,
                taxonomy_version=taxonomy_version, schema_version=schema_version,
                started_at=now, status="running",
                input_count=0, success_count=0, error_count=0,
                token_input=None, token_output=None, token_reasoning=None,
            )
        )


def write_rule_annotations(engine, run_id, decisions, now):
    """把一批规则剔除写成 `relevance=irrelevant` ＋ `text_quality` 两行。

    `decisions`：`[(target_id, subject_code, text, Decision), ...]`。
    幂等：同一 `(comment, code, relevance, input_hash)` **仍是现行链末**才跳过。
    历史同 hash 若已被模型 supersede，规则再次成立时必须写回新的链末结论。
    返回实际写入的判定单元数。
    """
    from radar_db.schema import annotation_jobs, annotations

    written = 0
    touched_codes = set()
    with engine.begin() as conn:
        for target_id, code, text, d in decisions:
            detail = dict(d.detail)
            detail.setdefault("reason", d.rule)
            detail.setdefault("matched_tickers", [])
            detail.setdefault("rule_version", VERSION)
            detail.setdefault("decided_at", now.isoformat(timespec="seconds"))
            h = rule_input_hash(text, d.rule, detail["rule_version"], detail)
            # A deterministic exclusion is terminal for any older model job
            # for the same comment/product unit.  Keep the job row as audit
            # history, but make it unclaimable.  Do this even when the rule
            # annotation already exists: a stale job may have been enqueued
            # after an earlier extraction run.
            conn.execute(
                update(annotation_jobs)
                .where(
                    annotation_jobs.c.target_type == "comment",
                    annotation_jobs.c.target_id == target_id,
                    annotation_jobs.c.subject_code == code,
                    annotation_jobs.c.task == "comment_product",
                    annotation_jobs.c.status.in_(("pending", "claimed", "failed", "done")),
                )
                .values(
                    status="superseded",
                    lease_until=None,
                    last_error=f"Excluded by {detail['rule_version']}:{d.rule}",
                    updated_at=now,
                )
            )
            newer = annotations.alias("newer_rule_annotation")
            current_match = conn.execute(
                select(annotations.c.annotation_id).where(
                    annotations.c.target_type == "comment",
                    annotations.c.target_id == target_id,
                    annotations.c.subject_code == code,
                    annotations.c.kind == "relevance",
                    annotations.c.input_hash == h,
                    annotations.c.review_state != "rejected",
                    ~select(newer.c.annotation_id).where(
                        newer.c.supersedes_id == annotations.c.annotation_id
                    ).exists(),
                ).limit(1)
            ).first()
            if current_match:
                continue
            prev = conn.execute(
                select(annotations.c.annotation_id, annotations.c.review_state)
                .where(
                    annotations.c.target_type == "comment",
                    annotations.c.target_id == target_id,
                    annotations.c.subject_code == code,
                    annotations.c.kind == "relevance",
                )
                .order_by(annotations.c.annotation_id.desc())
                .limit(1)
            ).first()
            # 人工裁决过的不覆盖（与 annotate._write 同一条纪律）。
            supersedes = (
                prev.annotation_id
                if prev is not None and prev.review_state not in ("approved", "corrected")
                else None
            )
            for kind, value in (
                ("relevance", "irrelevant"),
                ("text_quality", {"rule": d.rule, **detail}),
            ):
                conn.execute(
                    insert(annotations).values(
                        target_type="comment", target_id=target_id, subject_code=code,
                        kind=kind, value_json=json.dumps(value, ensure_ascii=False),
                        calibrated_confidence=None, run_id=run_id, input_hash=h,
                        review_state="pending", created_at=now,
                        supersedes_id=supersedes if kind == "relevance" else None,
                    )
                )
            written += 1
            touched_codes.add(code)
        if written:
            # Rule-only flips may have no later LLM run to invalidate caches or
            # Layer-B synthesis, so the deterministic writer owns this signal.
            from radar_db.revisions import bump_revision, mark_synthesis

            bump_revision(conn, "annotation")
            mark_synthesis(conn, touched_codes, True)
    return written


def withdraw_rule_exclusions(engine, run_id, eligible_units, now):
    """Invalidate a current machine-rule exclusion when a unit becomes eligible.

    A rejected chain-end is an explicit tombstone: the old exclusion remains in
    audit history, but readers correctly see no relevance verdict while the new
    v3 job is pending.  Human-approved/corrected conclusions are never touched.
    ``eligible_units`` contains ``(target_id, subject_code, text)`` tuples.
    """
    from radar_db.revisions import bump_revision, mark_synthesis
    from radar_db.schema import annotation_runs, annotations

    written = 0
    touched_codes = set()
    with engine.begin() as conn:
        newer = annotations.alias("newer_rule_exclusion")
        for target_id, code, text in dict.fromkeys(eligible_units):
            previous = conn.execute(
                select(annotations.c.annotation_id, annotations.c.review_state)
                .select_from(
                    annotations.join(
                        annotation_runs,
                        annotation_runs.c.run_id == annotations.c.run_id,
                    )
                )
                .where(
                    annotations.c.target_type == "comment",
                    annotations.c.target_id == target_id,
                    annotations.c.subject_code == code,
                    annotations.c.kind == "relevance",
                    annotations.c.value_json == json.dumps("irrelevant"),
                    annotations.c.review_state.notin_(("approved", "corrected", "rejected")),
                    annotation_runs.c.provider == "rule",
                    ~select(newer.c.annotation_id).where(
                        newer.c.supersedes_id == annotations.c.annotation_id
                    ).exists(),
                )
                .order_by(annotations.c.annotation_id.desc())
                .limit(1)
            ).first()
            if previous is None:
                continue
            detail = {
                "rule": "rule_exclusion_withdrawn",
                "reason": "eligible_for_ai",
                "matched_tickers": [],
                "rule_version": VERSION,
                "decided_at": now.isoformat(timespec="seconds"),
            }
            h = rule_input_hash(text, detail["rule"], VERSION, detail)
            conn.execute(insert(annotations).values(
                target_type="comment",
                target_id=target_id,
                subject_code=code,
                kind="relevance",
                value_json=json.dumps("irrelevant"),
                calibrated_confidence=None,
                run_id=run_id,
                input_hash=h,
                # A rejected chain-end deliberately means “no current verdict”.
                review_state="rejected",
                created_at=now,
                supersedes_id=previous.annotation_id,
            ))
            conn.execute(insert(annotations).values(
                target_type="comment",
                target_id=target_id,
                subject_code=code,
                kind="text_quality",
                value_json=json.dumps(detail, ensure_ascii=False),
                calibrated_confidence=None,
                run_id=run_id,
                input_hash=h,
                review_state="pending",
                created_at=now,
                supersedes_id=None,
            ))
            written += 1
            touched_codes.add(code)
        if written:
            bump_revision(conn, "annotation")
            mark_synthesis(conn, touched_codes, True)
    return written
