"""`jobs/annotate.py` —— 标注作业。

这份测试的重心不在「顺利跑通」，而在**失败时库里留下了什么**。标注管线的危险不是崩溃，
是安静地写进一个看起来正常的错值：一个兜底的 `neutral`、一段模型编的引文、一个被覆盖掉
的人工结论、一列写成 0 的未知用量。这些在界面上和真数据长得一模一样，没有任何一处会报警。

所以下面每一条都在钉一个「不许发生」：

- 重复排队不许重复付费；改了 Prompt 版本必须是**新的**待办。
- 一批里坏一条，不许连坐另外几条。
- 401 这类配置错，不许把整批推进 dead-letter。
- 模型编的引文不许落进证据表。
- 人工确认过的结论不许被自动重跑覆盖。
- 用量取不到不许写 0。
"""

import json
import os
import sys
from datetime import datetime, timedelta

import pytest
from sqlalchemy import insert, select, update

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from ai import config  # noqa: E402
from ai.providers.base import Completion, PermanentError, TransientError, Usage  # noqa: E402
from jobs import annotate  # noqa: E402
from radar_db import create_all, make_engine  # noqa: E402
from radar_db.schema import (  # noqa: E402
    annotation_evidence,
    annotation_jobs,
    annotation_runs,
    annotations,
    comments,
    feeds,
)

CODE = "3033"
# 真实形状的评论：有产品态度、有可引用的连续片段。
TEXT_A = "这只ETF点差太大，来回一趟就蚀掉不少，不太适合短线"
TEXT_B = "费率是同类里最低的，长期拿着很省心"


# ── 夹具 ───────────────────────────────────────────────────────────────


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "t.db").as_posix())
    create_all(eng)
    with eng.begin() as conn:
        conn.execute(
            insert(feeds).values(
                feed_id=1, code=CODE, posted_at=datetime(2026, 8, 1, 10, 0), feed_type=1,
                like_count=0, comment_count=2, image_count=0, raw_json_broken=False,
            )
        )
        conn.execute(
            insert(comments),
            [
                {"comment_id": 11, "feed_id": 1, "content": TEXT_A},
                {"comment_id": 12, "feed_id": 1, "content": TEXT_B},
            ],
        )
    return eng


@pytest.fixture()
def cfg():
    # overrides ⇒ 纯测试配置，完全不读 .env（见 config.load 的注释）。
    return config.load(model="test-model", micro_batch_size=30, max_retries=3)


class FakeProvider:
    """按脚本回答的供应商。每次 `complete_json` 取脚本的下一项：

    - `dict` → 当作模型返回的 JSON
    - `Exception` 实例 → 抛出
    - `callable` → 传入本次请求的 item_id 列表，返回上面两种之一
    """

    def __init__(self, script, usage=None):
        self.script = list(script)
        self.calls = []
        self.messages = []
        self.usage = usage or Usage(100, 50, 10, 0)
        self.model = "test-model-2026-08-snapshot"

    def complete_json(self, system, user, schema, schema_name):
        # 从 user message 里抠出这批的 item_id，脚本函数据此构造对应的返回。
        ids = [line.split('"')[3] for line in user.splitlines() if '"item_id"' in line]
        self.calls.append(ids)
        self.messages.append(user)
        step = self.script.pop(0) if self.script else {"results": []}
        if callable(step):
            step = step(ids)
        if isinstance(step, Exception):
            raise step
        return Completion(
            data=step, model=self.model, usage=self.usage,
            raw_text=json.dumps(step), response_id="resp_1",
        )


def ok_item(item_id, evidence, attitude="negative", aspects=("spread",)):
    return {
        "item_id": item_id,
        "relevance": "relevant",
        "attitude": attitude,
        "aspects": list(aspects),
        "evidence": evidence,
        "needs_review": False,
        "uncertainty_reasons": [],
    }


def all_ok(evidence_for):
    """脚本项：对这批每个 id 都返回合规结果，证据由 `evidence_for(id)` 给。"""
    return lambda ids: {"results": [ok_item(i, evidence_for(i)) for i in ids]}


def rows(engine, table, *where):
    with engine.connect() as conn:
        return conn.execute(select(table).where(*where)).mappings().all()


# ── 排队与幂等（runbook §11.3、§17.3） ──────────────────────────────────


def test_enqueue_creates_one_job_per_comment_product_pair(engine, cfg):
    assert annotate.enqueue_comments(engine, cfg) == 2
    jobs = rows(engine, annotation_jobs)
    assert {j["target_id"] for j in jobs} == {11, 12}
    # 判定单元是 (comment_id, subject_code)（§10.1），产品来自帖子的挂载标的。
    assert all(j["subject_code"] == CODE for j in jobs)
    assert all(j["status"] == "pending" and j["attempts"] == 0 for j in jobs)


def test_enqueue_twice_does_not_double_charge(engine, cfg):
    annotate.enqueue_comments(engine, cfg)
    assert annotate.enqueue_comments(engine, cfg) == 0, "重复排队 = 重复付费"
    assert len(rows(engine, annotation_jobs)) == 2


def test_changing_prompt_version_makes_a_new_job(engine, cfg, monkeypatch):
    annotate.enqueue_comments(engine, cfg)
    # 换 Prompt 版本 ⇒ input_hash 变 ⇒ 是一件新的待办，不是重复。
    # 反过来（被判成重复而跳过）正是「改了 Prompt 但结果没变」那类查不出原因的 bug。
    prompt = annotate.get_prompt("comment_product", cfg.prompt_version)
    monkeypatch.setattr(prompt, "VERSION", "comment-product-v2")
    assert annotate.enqueue_comments(engine, cfg) == 2
    assert len(rows(engine, annotation_jobs)) == 4


def test_changing_model_makes_a_new_job(engine, cfg):
    annotate.enqueue_comments(engine, cfg)
    assert annotate.enqueue_comments(engine, config.load(model="other-model")) == 2


# ── 上下文（runbook §11.4 许可的两项） ────────────────────────────────


@pytest.fixture()
def threaded(engine):
    """一条挂在有标题的帖子下、且回复了另一条评论的短评论。

    首轮 100 条影子运行里 40% 判成 `needs_context`，样本是「有」「劲」「是股息」
    这种一两个字的回复 —— 那个判断是对的，光看三个字确实判不出在夸哪只 ETF。
    """
    with engine.begin() as conn:
        conn.execute(insert(feeds).values(
            feed_id=2, code=CODE, posted_at=datetime(2026, 8, 2, 10, 0), feed_type=1,
            title="3033 半年定投记录", like_count=0, comment_count=1, image_count=0,
            raw_json_broken=False,
        ))
        conn.execute(insert(comments).values(
            comment_id=20, feed_id=2, content="这只的跟踪误差控制得不错"))
        conn.execute(insert(comments).values(
            comment_id=21, feed_id=2, content="同意", reply_to_comment_id=20))
    return engine


def test_parent_comment_and_post_title_are_sent(threaded, cfg):
    annotate.enqueue_comments(threaded, cfg)
    prov = FakeProvider([all_ok(lambda _: None)])
    annotate.run(threaded, cfg, max_items=50, provider=prov)
    sent = "\n".join(prov.messages)
    assert "3033 半年定投记录" in sent
    assert "这只的跟踪误差控制得不错" in sent, "「同意」的父评论必须一起发过去"


def test_comments_without_parent_or_title_still_get_queued(threaded, cfg):
    """外连接的意义：取不到帖子标题或父评论时，评论**仍然要**进队列。
    内连接会让它们悄悄消失 —— 队列少了 350k 条里的大半，而没有任何一处报错。"""
    assert annotate.enqueue_comments(threaded, cfg) == 4  # 11、12 无标题无父；20 无父


def test_context_is_part_of_the_fingerprint(threaded, cfg):
    """同一条「同意」，挂在不同的父评论下就是不同的输入，应当各判一次。
    指纹只取正文的话，两者会被判成同一件待办，先来的结论会被沿用到另一个语境上。"""
    annotate.enqueue_comments(threaded, cfg)
    before = {j["input_hash"] for j in rows(threaded, annotation_jobs)}

    with threaded.begin() as conn:
        conn.execute(comments.update().where(comments.c.comment_id == 20)
                     .values(content="这只的跟踪误差大得离谱"))
    annotate.enqueue_comments(threaded, cfg)

    after = rows(threaded, annotation_jobs, annotation_jobs.c.target_id == 21)
    assert len(after) == 2, "父评论变了 ⇒ 新的待办，不是重复"
    assert {j["input_hash"] for j in after} - before


def test_context_is_scrubbed_before_sending(threaded, cfg):
    with threaded.begin() as conn:
        conn.execute(comments.update().where(comments.c.comment_id == 20)
                     .values(content="@老王 说得对，见 https://x.io/a?token=zz"))
    annotate.enqueue_comments(threaded, cfg)
    prov = FakeProvider([all_ok(lambda _: None)])
    annotate.run(threaded, cfg, max_items=50, provider=prov)
    sent = "\n".join(prov.messages)
    assert "老王" not in sent and "token=zz" not in sent


# ── 领取与租约 ─────────────────────────────────────────────────────────


def test_claim_marks_lease_and_second_claim_gets_nothing(engine, cfg):
    annotate.enqueue_comments(engine, cfg)
    first = annotate.claim(engine, "comment_product", 10)
    assert len(first) == 2
    assert annotate.claim(engine, "comment_product", 10) == []
    assert all(j["lease_until"] is not None for j in rows(engine, annotation_jobs))


def test_expired_lease_is_reclaimable(engine, cfg):
    """worker 被 Ctrl-C 掉时正在处理的那一批，不能永远卡在 claimed。"""
    annotate.enqueue_comments(engine, cfg)
    annotate.claim(engine, "comment_product", 10, now=datetime(2026, 8, 1, 10, 0))
    later = datetime(2026, 8, 1, 10, 0) + timedelta(minutes=annotate.LEASE_MINUTES + 1)
    assert len(annotate.claim(engine, "comment_product", 10, now=later)) == 2


# ── 正常路径 ───────────────────────────────────────────────────────────


def test_run_writes_annotations_and_located_evidence(engine, cfg):
    annotate.enqueue_comments(engine, cfg)
    quotes = {11: "点差太大", 12: "费率是同类里最低的"}
    prov = FakeProvider([all_ok(lambda i: quotes[int(i.split(":")[1].split("|")[0])])])

    stats = annotate.run(engine, cfg, max_items=10, provider=prov)
    assert (stats["success"], stats["error"]) == (2, 0)
    assert all(j["status"] == "done" for j in rows(engine, annotation_jobs))

    # 一条输出拆成多行：relevance / attitude / aspect 是 §9 里不同的原子任务。
    kinds = {(a["target_id"], a["kind"]) for a in rows(engine, annotations)}
    assert kinds == {(11, "relevance"), (11, "attitude"), (11, "aspect"),
                     (12, "relevance"), (12, "attitude"), (12, "aspect")}

    ev = {e["source_target_id"]: e for e in rows(engine, annotation_evidence)}
    assert set(ev) == {11, 12}
    for target_id, text in ((11, TEXT_A), (12, TEXT_B)):
        row = ev[target_id]
        # 偏移由程序在原文里定位，不采信模型自报的位置。
        assert text[row["start_offset"]:row["end_offset"]] == row["quote_text"]
        assert row["quote_text"] == quotes[target_id]


def test_model_confidence_never_lands_in_calibrated_confidence(engine, cfg):
    """那一列是 Gate 3 的校准概率。填模型自报值会让前端 0.7 阈值筛出一批
    没有意义的「高置信」（runbook §11.1 末条）。"""
    annotate.enqueue_comments(engine, cfg)
    annotate.run(engine, cfg, max_items=10,
                 provider=FakeProvider([all_ok(lambda _: "点差太大")]))
    assert all(a["calibrated_confidence"] is None for a in rows(engine, annotations))


def test_run_records_model_returned_not_model_requested(engine, cfg):
    """网关会做别名转发：请求 `gpt-5.6-luna` 实际跑的可能是另一个快照。"""
    annotate.enqueue_comments(engine, cfg)
    prov = FakeProvider([all_ok(lambda _: "点差太大")])
    annotate.run(engine, cfg, max_items=10, provider=prov)
    run = rows(engine, annotation_runs)[0]
    assert run["model_id"] == prov.model != cfg.model
    assert run["status"] == "done"
    assert (run["input_count"], run["success_count"], run["error_count"]) == (2, 2, 0)


def test_usage_is_summed_and_reasoning_tokens_counted(engine, cfg):
    annotate.enqueue_comments(engine, cfg)
    annotate.run(engine, cfg, max_items=10,
                 provider=FakeProvider([all_ok(lambda _: "点差太大")],
                                       usage=Usage(100, 50, 10, 0)))
    run = rows(engine, annotation_runs)[0]
    assert (run["token_input"], run["token_output"], run["token_reasoning"]) == (100, 50, 10)


def test_unknown_usage_is_null_not_zero(engine, cfg):
    """铁律 2。写 0 会让成本报表少算而看不出来。"""
    annotate.enqueue_comments(engine, cfg)
    annotate.run(engine, cfg, max_items=10,
                 provider=FakeProvider([all_ok(lambda _: "点差太大")],
                                       usage=Usage(None, None, None, None)))
    run = rows(engine, annotation_runs)[0]
    assert run["token_input"] is None
    assert run["token_output"] is None


# ── 证据造假 ───────────────────────────────────────────────────────────


def test_paraphrased_evidence_is_not_stored_and_flags_review(engine, cfg):
    """Gate 0 首次真实调用就复现了这个：模型把原文改写成一句带结论的转述。

    它读起来比原文更像证据。落库的话，证据侧栏会显示一段原文里根本不存在的话，
    而肉眼检查会全部通过 —— 没有人会去原文里搜这段。
    """
    annotate.enqueue_comments(engine, cfg)
    fake = "评论指出该ETF点差太大，属于对产品交易成本的负面评价。"
    annotate.run(engine, cfg, max_items=10, provider=FakeProvider([all_ok(lambda _: fake)]))

    assert rows(engine, annotation_evidence) == [], "编造的引文一个字都不许落库"
    # 但结论照样落库 —— 态度判断可能是对的，只是证据不合格，交人工比整条丢掉省预算。
    anns = rows(engine, annotations)
    assert anns and all(a["review_state"] == "needs_review" for a in anns)


def test_relevant_without_any_evidence_flags_review(engine, cfg):
    annotate.enqueue_comments(engine, cfg)
    annotate.run(engine, cfg, max_items=10, provider=FakeProvider([all_ok(lambda _: None)]))
    assert all(a["review_state"] == "needs_review" for a in rows(engine, annotations))


def test_irrelevant_without_evidence_is_not_flagged(engine, cfg):
    """无关的评论本来就没有证据可给，这不是问题。把它也打 needs_review 会让
    复核队列被正确结论淹没。"""
    annotate.enqueue_comments(engine, cfg)
    script = [lambda ids: {"results": [
        {"item_id": i, "relevance": "irrelevant", "attitude": None, "aspects": [],
         "evidence": None, "needs_review": False, "uncertainty_reasons": []}
        for i in ids
    ]}]
    annotate.run(engine, cfg, max_items=10, provider=FakeProvider(script))
    anns = rows(engine, annotations)
    assert anns and all(a["review_state"] == "pending" for a in anns)
    # relevance=irrelevant 时 attitude 必须为 null，所以不该写出 attitude 行。
    assert {a["kind"] for a in anns} == {"relevance"}


# ── 失败处理（runbook §11.3） ──────────────────────────────────────────


def test_one_bad_item_does_not_condemn_the_rest(engine, cfg):
    """id 集合对不上时整批失败，但二分之后只有真正坏的那条被判。

    不二分的话，一条坏的会连坐 29 条 —— 那 29 条要么白付一次费，要么进 dead-letter。
    """
    annotate.enqueue_comments(engine, cfg)
    bad_id = f"comment:11|product:{CODE}"

    def maybe_bad(ids):
        # 只要这批里有 11，就少返回它一条 —— id 集合不一致。
        kept = [i for i in ids if i != bad_id]
        if len(kept) == len(ids):
            return {"results": [ok_item(i, "费率是同类里最低的") for i in ids]}
        return {"results": [ok_item(i, "费率是同类里最低的") for i in kept]}

    prov = FakeProvider([maybe_bad] * 8)
    stats = annotate.run(engine, cfg, max_items=10, provider=prov)

    by_target = {j["target_id"]: j for j in rows(engine, annotation_jobs)}
    assert by_target[12]["status"] == "done", "好的那条必须跑完"
    assert by_target[11]["status"] in ("pending", "dead")
    assert stats["success"] == 1
    assert len(prov.calls) >= 3, "整批 → 二分两半，至少三次调用"


def test_permanent_error_releases_jobs_without_burning_attempts(engine, cfg):
    """401/400 多半是配置问题。把 30 条因为一个 Key 打错而判死，改完配置还得手动复活。"""
    annotate.enqueue_comments(engine, cfg)
    prov = FakeProvider([PermanentError("HTTP 401: invalid api key")])
    stats = annotate.run(engine, cfg, max_items=10, provider=prov)

    jobs = rows(engine, annotation_jobs)
    assert all(j["status"] == "pending" for j in jobs)
    assert all(j["attempts"] == 0 for j in jobs), "配置事故不该计进任务的重试次数"
    assert all("401" in (j["last_error"] or "") for j in jobs)
    assert stats["error"] == 2
    # 只调一次：永久错误既不重试，也不继续领下一批 —— 放回的任务立刻又是 pending，
    # 不中止的话 run 会一直空转到 max_items 耗尽，每轮都完整地发一次请求。
    assert len(prov.calls) == 1
    assert stats["aborted"] and "401" in stats["aborted"]
    assert rows(engine, annotation_runs)[0]["status"] == "failed"


def test_transient_failures_reach_dead_letter_after_max_retries(engine, cfg):
    annotate.enqueue_comments(engine, cfg)
    for _ in range(cfg.max_retries):
        annotate.run(engine, cfg, max_items=10,
                     provider=FakeProvider([TransientError("HTTP 503")]))
    jobs = rows(engine, annotation_jobs)
    assert all(j["status"] == "dead" for j in jobs)
    assert all(j["attempts"] == cfg.max_retries for j in jobs)
    # dead-letter 里不许有伪默认值。
    assert rows(engine, annotations) == []


def test_run_status_is_partial_when_anything_failed(engine, cfg):
    annotate.enqueue_comments(engine, cfg)
    annotate.run(engine, cfg, max_items=10,
                 provider=FakeProvider([TransientError("HTTP 503")]))
    assert rows(engine, annotation_runs)[0]["status"] == "partial"


def test_empty_source_text_is_dead_not_retried(engine, cfg):
    """空文本该在规则层就拦下（§6.5），不该进队列，更不该反复调 GPT。"""
    with engine.begin() as conn:
        conn.execute(insert(comments).values(comment_id=13, feed_id=1, content="   "))
    annotate.enqueue_comments(engine, cfg)
    # content 全空白的那条不进队列（enqueue 只取非空），手工塞一条模拟脏队列。
    with engine.begin() as conn:
        conn.execute(insert(annotation_jobs).values(
            target_type="comment", target_id=99, subject_code=CODE,
            task="comment_product", input_hash="deadbeef", status="pending",
            priority=0, attempts=0,
            created_at=datetime(2026, 8, 1, 10, 0),
            updated_at=datetime(2026, 8, 1, 10, 0),
        ))
    annotate.run(engine, cfg, max_items=10,
                 provider=FakeProvider([all_ok(lambda _: "点差太大")]))
    ghost = rows(engine, annotation_jobs, annotation_jobs.c.target_id == 99)[0]
    assert ghost["status"] == "dead"


# ── 不覆盖人工结论（runbook §11.3 末条） ───────────────────────────────


def test_human_approved_annotation_is_not_superseded(engine, cfg):
    annotate.enqueue_comments(engine, cfg)
    annotate.run(engine, cfg, max_items=10,
                 provider=FakeProvider([all_ok(lambda _: "点差太大")]))

    with engine.begin() as conn:
        approved = conn.execute(
            select(annotations).where(annotations.c.kind == "attitude",
                                      annotations.c.target_id == 11)
        ).mappings().one()
        conn.execute(
            annotations.update()
            .where(annotations.c.annotation_id == approved["annotation_id"])
            .values(review_state="approved")
        )

    # 换 Prompt 版本重跑（否则会被幂等挡掉），这次模型给出相反的态度。
    prompt = annotate.get_prompt("comment_product", cfg.prompt_version)
    old = prompt.VERSION
    try:
        prompt.VERSION = "comment-product-v2"
        annotate.enqueue_comments(engine, cfg)
        annotate.run(engine, cfg, max_items=10, provider=FakeProvider(
            [lambda ids: {"results": [ok_item(i, "点差太大", attitude="positive")
                                      for i in ids]}]))
    finally:
        prompt.VERSION = old

    after = rows(engine, annotations, annotations.c.kind == "attitude",
                 annotations.c.target_id == 11)
    old_row = [a for a in after if a["annotation_id"] == approved["annotation_id"]][0]
    new_row = [a for a in after if a["annotation_id"] != approved["annotation_id"]][0]

    # 旧行原样留着，且没有被任何新行 supersede。
    assert old_row["review_state"] == "approved"
    assert json.loads(old_row["value_json"]) == "negative"
    assert all(a["supersedes_id"] != approved["annotation_id"] for a in after)
    # 新结论照写，但保持待复核 —— 由人再看一次，而不是自动生效或自动丢弃。
    assert new_row["review_state"] == "pending"
    assert json.loads(new_row["value_json"]) == "positive"


def test_unreviewed_annotation_is_superseded_on_rerun(engine, cfg):
    annotate.enqueue_comments(engine, cfg)
    annotate.run(engine, cfg, max_items=10,
                 provider=FakeProvider([all_ok(lambda _: "点差太大")]))
    first = {a["annotation_id"] for a in rows(engine, annotations,
                                              annotations.c.kind == "attitude")}

    prompt = annotate.get_prompt("comment_product", cfg.prompt_version)
    old = prompt.VERSION
    try:
        prompt.VERSION = "comment-product-v2"
        annotate.enqueue_comments(engine, cfg)
        annotate.run(engine, cfg, max_items=10,
                     provider=FakeProvider([all_ok(lambda _: "点差太大")]))
    finally:
        prompt.VERSION = old

    after = rows(engine, annotations, annotations.c.kind == "attitude")
    # 旧行**不删** —— 回滚时要能回到上一版。新行指回它。
    assert {a["annotation_id"] for a in after} > first
    assert {a["supersedes_id"] for a in after if a["supersedes_id"]} == first


# ── 队列计数 ───────────────────────────────────────────────────────────


def test_pending_count_excludes_finished_work(engine, cfg):
    annotate.enqueue_comments(engine, cfg)
    assert annotate.pending_count(engine, "comment_product") == 2
    annotate.run(engine, cfg, max_items=10,
                 provider=FakeProvider([all_ok(lambda _: "点差太大")]))
    assert annotate.pending_count(engine, "comment_product") == 0


# ── 帖子排队（runbook §11.2） ───────────────────────────────────────────


@pytest.fixture()
def posts(engine):
    """三篇形状不同的帖子：标题＋正文、只有标题、两样都没有。

    「只有标题」不是构造出来的边界 —— 富途社区里大量帖子就长这样，而官号动态那一屏
    主要就靠它们。它们**必须**能排进队。
    """
    with engine.begin() as conn:
        conn.execute(
            insert(feeds),
            [
                {"feed_id": 2, "code": CODE, "posted_at": datetime(2026, 8, 2, 9, 0),
                 "feed_type": 1, "title": "恒科今日走势", "content": "暂时不加仓，继续观察",
                 "like_count": 0, "comment_count": 0, "image_count": 0,
                 "raw_json_broken": False},
                {"feed_id": 3, "code": CODE, "posted_at": datetime(2026, 8, 3, 9, 0),
                 "feed_type": 1, "title": "只有标题的帖子", "content": None,
                 "like_count": 0, "comment_count": 0, "image_count": 0,
                 "raw_json_broken": False},
                {"feed_id": 4, "code": CODE, "posted_at": datetime(2026, 8, 4, 9, 0),
                 "feed_type": 1, "title": None, "content": "",
                 "like_count": 0, "comment_count": 0, "image_count": 0,
                 "raw_json_broken": False},
            ],
        )
    return engine


def test_enqueue_posts_takes_the_post_itself_not_one_row_per_subject(posts, cfg):
    """帖子的判定单元只有帖子。一篇挂三只标的的行情解读仍然只是一篇行情解读 ——
    按标的展开会让它被判三次，还可能判出三个不同的类型。"""
    assert annotate.enqueue_posts(posts, cfg) == 2
    jobs = rows(posts, annotation_jobs, annotation_jobs.c.task == "post_annotation")
    assert {j["target_id"] for j in jobs} == {2, 3}
    assert all(j["target_type"] == "feed" for j in jobs)
    assert all(j["subject_code"] == annotate.NO_SUBJECT for j in jobs)


def test_title_only_posts_are_queued_and_textless_ones_are_not(posts, cfg):
    """过滤条件是「标题与正文至少一个非空」。只看正文会把整类只有标题的帖子
    悄悄排除 —— 那不会报错，只会让官号动态那一屏永远缺一块。"""
    annotate.enqueue_posts(posts, cfg)
    ids = {j["target_id"] for j in rows(posts, annotation_jobs,
                                        annotation_jobs.c.task == "post_annotation")}
    assert 3 in ids, "只有标题的帖子必须排得进来"
    assert 4 not in ids, "标题与正文都空的帖子没有东西可发，不该排队"


def test_enqueue_posts_twice_does_not_double_charge(posts, cfg):
    annotate.enqueue_posts(posts, cfg)
    assert annotate.enqueue_posts(posts, cfg) == 0, "重复排队 = 重复付费"


def test_post_fingerprint_covers_the_title_not_just_the_body(posts, cfg):
    """指纹必须覆盖真正发出去的输入（§11.3）。只按正文算的话，改了标题的帖子会被
    当成已排过队跳过 —— 而模型看到的输入其实变了。"""
    annotate.enqueue_posts(posts, cfg)
    with posts.begin() as conn:
        conn.execute(update(feeds).where(feeds.c.feed_id == 2).values(title="改过的标题"))
    assert annotate.enqueue_posts(posts, cfg) == 1


def test_post_and_comment_queues_do_not_collide(posts, cfg):
    """两种任务共用一张 `annotation_jobs`，而两边的 ID 来自不同的表，**会撞号**：
    这里特意让一条评论的 `comment_id` 等于一篇帖子的 `feed_id`。区分它们的是
    唯一键里的 `target_type` 与 `task`，不是 ID 本身。"""
    with posts.begin() as conn:
        conn.execute(insert(comments).values(comment_id=2, feed_id=1, content=TEXT_A))
    annotate.enqueue_comments(posts, cfg)
    annotate.enqueue_posts(posts, cfg)
    assert annotate.pending_count(posts, "comment_product") == 3
    assert annotate.pending_count(posts, "post_annotation") == 2
    collided = rows(posts, annotation_jobs, annotation_jobs.c.target_id == 2)
    assert {(j["target_type"], j["task"]) for j in collided} == {
        ("comment", "comment_product"),
        ("feed", "post_annotation"),
    }


# ── 帖子结论：「没有」也要写下来（runbook §11.2） ───────────────────────


def post_item(item_id, **over):
    """一条合规的帖子标注。默认：行情解读、没表达操作、没有可摘要的正文。"""
    out = {
        "item_id": item_id,
        "post_type": "market",
        "direction": None,
        "direction_pending": False,
        "summary": None,
        "evidence_spans": ["暂时不加仓"],
        "needs_review": False,
    }
    out.update(over)
    return out


def run_posts(engine, cfg, **over):
    annotate.enqueue_posts(engine, cfg)
    annotate.run(engine, cfg, max_items=10, task="post_annotation",
                 provider=FakeProvider([lambda ids: {
                     "results": [post_item(i, **over) for i in ids]}]))
    # 显式按 id 升序，让「同一 kind 的最后一行」是确定的：重跑会叠新行，
    # 靠默认行序取最后一条，换个方言就不成立了。
    with engine.connect() as conn:
        got = conn.execute(
            select(annotations)
            .where(annotations.c.target_id == 2)
            .order_by(annotations.c.annotation_id.asc())
        ).mappings().all()
    return {r["kind"]: json.loads(r["value_json"]) for r in got}


def test_a_post_with_nothing_to_summarise_still_gets_a_summary_row(posts, cfg):
    """`summary: null` 是模型的**结论**（「纯图片、纯链接」，见 Prompt 的摘要一节），
    不是它没回答 —— schema 里这个键必填，模型必须显式写 null。

    丢掉这一行，「已标注、确实没得摘」和「这帖压根没标注过」在库里就是同一个样子：
    两边都查不到行。而页面上它们相反 —— 前者照常渲染，后者要显示「暂不可用」。
    """
    v = run_posts(posts, cfg)
    assert "summary" in v, "没得摘也要留下「看过了，没得摘」这句话"
    assert v["summary"] is False, "false 和摘要字符串类型不同，读取方一眼能分开"


def test_a_post_that_expressed_no_action_still_gets_a_direction_row(posts, cfg):
    """`direction=null, direction_pending=false` 是 Prompt 里的第 1 种情形：
    「帖子没有表达任何操作」。同样是结论，同样不能因为值是 null 就不写。"""
    v = run_posts(posts, cfg)
    assert v["direction"] is False


def test_the_three_direction_states_stay_three(posts, cfg):
    """判出来了 / 表达了但判不出 / 压根没表达 —— 三件事，三个值。

    这正是 `direction_pending` 当初存在的理由；少写一行就把三态压回两态了。
    """
    assert run_posts(posts, cfg, direction="add")["direction"] == "add"
    with posts.begin() as conn:  # 换一篇，免得撞上已写过的结论
        conn.execute(update(feeds).where(feeds.c.feed_id == 2).values(title="改标题一"))
    assert run_posts(posts, cfg, direction=None,
                     direction_pending=True)["direction"] == "pending"
    with posts.begin() as conn:
        conn.execute(update(feeds).where(feeds.c.feed_id == 2).values(title="改标题二"))
    assert run_posts(posts, cfg, direction=None,
                     direction_pending=False)["direction"] is False


def test_a_real_summary_is_stored_as_the_text(posts, cfg):
    """占位值不能把正常摘要也变成 false。"""
    v = run_posts(posts, cfg, summary="作者暂不加仓，继续观察恒科走势")
    assert v["summary"] == "作者暂不加仓，继续观察恒科走势"
