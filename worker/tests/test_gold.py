"""人工核对集：分层抽样与 Excel 形状、评估三套系统、`meta_kv.ai_validation` 形状；probe --concurrency 统计。"""

import json
import os
import sys
from datetime import datetime

import pytest
from sqlalchemy import delete, insert, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from radar_db import create_all, make_engine  # noqa: E402
from radar_db.comment_filter import filter_readiness_values, load_comment_filter_config  # noqa: E402
from radar_db.comment_routes import (  # noqa: E402
    COMMENT_ROUTE_VERSION,
    product_pool_digest,
    readiness_values as route_readiness_values,
)
from radar_db.schema import (  # noqa: E402
    annotation_runs, annotations, comments, feed_mentions, feeds, meta_kv,
)
from ai import release_regressions  # noqa: E402
from scripts import evaluate_gold, gold_sample  # noqa: E402

openpyxl = pytest.importorskip("openpyxl")

OWN, PEER = "3033", "2800"


def _passing_fixed_regressions():
    manifest, digest = release_regressions.load_manifest()
    comments = [{
        "caseId": row["caseId"],
        "expectedRelevance": row["expectedRelevance"],
        "humanRelevance": row["expectedRelevance"],
        "modelRelevance": row["expectedRelevance"],
        "passed": True,
    } for row in manifest["commentCases"]]
    return {
        "manifestVersion": manifest["manifestVersion"],
        "manifestSha256": digest,
        "commentCases": comments,
        "officialAttributionCases": release_regressions.official_regression_results(),
        "passed": True,
    }


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "g.db").as_posix())
    create_all(eng)
    now = datetime(2026, 8, 26)
    with eng.begin() as conn:
        conn.execute(insert(meta_kv), [
            {"k": key, "v": value}
            for key, value in filter_readiness_values(load_comment_filter_config()).items()
        ])
        for rid, prov in (("stu-1", "local_model"), ("luna-1", "openai_compatible"), ("rule-1", "rule")):
            conn.execute(insert(annotation_runs).values(
                run_id=rid, task="comment_product", provider=prov, model_id="m", prompt_version="comment-product-v3",
                taxonomy_version="v2", schema_version="v2", started_at=now, status="done",
                input_count=0, success_count=0, error_count=0))
        conn.execute(insert(feeds), [
            {"feed_id": 1, "code": OWN, "source_ticker": "03033.HK", "posted_at": now,
             "feed_type": 1, "title": "恒科", "content": "c",
             "like_count": 0, "comment_count": 0, "image_count": 0, "raw_json_broken": False},
            {"feed_id": 2, "code": PEER, "source_ticker": "02800.HK", "posted_at": now,
             "feed_type": 1, "title": "盈富", "content": "c",
             "like_count": 0, "comment_count": 0, "image_count": 0, "raw_json_broken": False},
        ])
        conn.execute(insert(feed_mentions), [
            {"feed_id": 1, "raw_ticker": "03033.HK", "market": "HK", "occurrences": 1},
            {"feed_id": 2, "raw_ticker": "02800.HK", "market": "HK", "occurrences": 1},
        ])
        rows, anns = [], []
        texts = ["费率同类最低，长期持有", "點差太大，唔會再買", "呢隻幾時派息", "恒指要崩了", "這隻不錯"]
        for i in range(60):
            cid = 100 + i
            code = OWN if i % 3 else PEER
            rows.append({"comment_id": cid, "feed_id": 1 if code == OWN else 2, "content": texts[i % 5] + f"{i}",
                         "author_uid": f"u{i}", "author_name": "不该出现的昵称"})
            rel = "irrelevant" if i % 5 == 3 else "relevant"
            p = [0.6, 0.8, 0.95][i % 3]
            att = ["positive", "negative", "neutral"][i % 3]
            anns.append({"target_type": "comment", "target_id": cid, "subject_code": code, "kind": "relevance",
                         "value_json": json.dumps(rel), "calibrated_confidence": p, "run_id": "stu-1",
                         "input_hash": f"h{cid}", "review_state": "pending", "created_at": now})
            if rel == "relevant":
                anns.append({"target_type": "comment", "target_id": cid, "subject_code": code, "kind": "attitude",
                             "value_json": json.dumps(att), "calibrated_confidence": p, "run_id": "stu-1",
                             "input_hash": f"h{cid}", "review_state": "pending", "created_at": now})
            # 一半有 Luna 现行标签（supersede 学生行）
            if i % 2 == 0:
                anns.append({"target_type": "comment", "target_id": cid, "subject_code": code, "kind": "relevance",
                             "value_json": json.dumps("relevant"), "calibrated_confidence": None, "run_id": "luna-1",
                             "input_hash": f"h{cid}", "review_state": "pending", "created_at": now})
                anns.append({"target_type": "comment", "target_id": cid, "subject_code": code, "kind": "attitude",
                             "value_json": json.dumps("positive"), "calibrated_confidence": None, "run_id": "luna-1",
                             "input_hash": f"h{cid}", "review_state": "pending", "created_at": now})
        conn.execute(insert(comments), rows)
        conn.execute(insert(annotations), anns)
    return eng


def test_sample_is_stratified_and_workbooks_have_shape(engine, tmp_path):
    units = gold_sample.collect_units(engine, ownership={OWN: "own", PEER: "peer"})
    assert len(units) == 60
    strata = {gold_sample.stratum_key(u) for u in units}
    assert len(strata) > 6
    picked = gold_sample.sample(units, 20, seed=1)
    assert len(picked) == 20 and len({u["id"] for u in picked}) == 20
    # 每个非空层至少 1 条（层数 ≤ n 时）
    picked_strata = {u["stratum"] for u in picked}
    assert picked_strata == strata or len(strata) > 20

    gold, labels = gold_sample.write_workbooks(picked, tmp_path, names={OWN: "南方东英恒生科技", PEER: "盈富"})
    wb = openpyxl.load_workbook(gold)
    assert wb.sheetnames == ["标注", "说明"]
    ws = wb["标注"]
    assert [c.value for c in ws[1]] == list(gold_sample.COLUMNS)
    assert ws.max_row == 21
    dvs = ws.data_validations.dataValidation
    assert {dv.formula1 for dv in dvs} == {'"相关,无关,需上下文"', '"积极,消极,中性"'}
    assert wb.properties.creator == "futu-radar"
    # 人工表里没有模型标签、没有昵称
    text = "\n".join(str(c.value) for row in ws.iter_rows() for c in row if c.value is not None)
    assert "relevant" not in text and "不该出现的昵称" not in text
    assert "大盘看跌" in "\n".join(str(r[0].value) for r in wb["说明"].iter_rows())

    wb2 = openpyxl.load_workbook(labels)
    ws2 = wb2.active
    header = [c.value for c in ws2[1]]
    assert header[:4] == ["编号", "comment_id", "产品代码", "层"] and "Luna相关性" in header
    assert header[-8:] == [
        "LLM模型", "LLM请求模型", "LLM Prompt", "LLM Schema", "LLM Taxonomy",
        "评论路由版本", "产品池摘要", "回归案例ID",
    ]
    assert ws2.max_row == 21
    reread = evaluate_gold.read_model_labels(labels)
    assert {tuple(row["llm_policy"].values()) for row in reread.values() if row["llm"]["relevance"]} == {
            ("m", "m", "comment-product-v3", "v2", "v2")
    }


def test_gold_workbook_exposes_all_three_parent_levels(engine, tmp_path):
    now = datetime(2026, 8, 26)
    with engine.begin() as conn:
        conn.execute(insert(comments), [
            {"comment_id": 1000, "feed_id": 1, "content": "第三层", "author_uid": "p3",
             "reply_to_comment_id": None},
            {"comment_id": 1001, "feed_id": 1, "content": "第二层", "author_uid": "p2",
             "reply_to_comment_id": 1000},
            {"comment_id": 1002, "feed_id": 1, "content": "第一层", "author_uid": "p1",
             "reply_to_comment_id": 1001},
            {"comment_id": 1003, "feed_id": 1, "content": "同意", "author_uid": "child",
             "reply_to_comment_id": 1002},
        ])
        conn.execute(insert(annotations).values(
            target_type="comment",
            target_id=1003,
            subject_code=OWN,
            kind="relevance",
            value_json='"needs_context"',
            calibrated_confidence=0.6,
            run_id="stu-1",
            input_hash="parent-chain",
            review_state="pending",
            created_at=now,
        ))

    unit = next(
        row for row in gold_sample.collect_units(
            engine, ownership={OWN: "own", PEER: "peer"}
        )
        if row["comment_id"] == 1003
    )
    assert unit["parents"] == ["第一层", "第二层", "第三层"]
    assert unit["parent"] == "第1层（直接父评论）：第一层\n第2层：第二层\n第3层：第三层"

    picked = gold_sample.sample([unit], 1, seed=1)
    gold_path, _ = gold_sample.write_workbooks(picked, tmp_path, names={OWN: "产品"})
    ws = openpyxl.load_workbook(gold_path)["标注"]
    assert ws["E2"].value == unit["parent"]


def test_llm_sample_without_students_uses_current_labels(engine, tmp_path):
    with engine.begin() as conn:
        conn.execute(delete(annotations).where(annotations.c.run_id == "stu-1"))
        previous = conn.execute(select(annotations.c.annotation_id).where(
            annotations.c.run_id == "luna-1", annotations.c.kind == "relevance",
            annotations.c.target_id == 100)).scalar_one()
        conn.execute(insert(annotations), [
            {"target_type": "comment", "target_id": 100, "subject_code": PEER, "kind": "relevance",
             "value_json": '"relevant"', "run_id": "luna-1", "input_hash": "rejected",
             "review_state": "rejected", "supersedes_id": previous, "created_at": datetime(2026, 8, 27)},
            {"target_type": "comment", "target_id": 101, "subject_code": OWN, "kind": "relevance",
             "value_json": '"irrelevant"', "run_id": "rule-1", "input_hash": "rule",
             "review_state": "pending", "supersedes_id": None, "created_at": datetime(2026, 8, 27)},
        ])
        before = conn.execute(select(annotations)).all()

    assert gold_sample.collect_units(engine, ownership={OWN: "own", PEER: "peer"}) == []
    units = gold_sample.collect_units(engine, ownership={OWN: "own", PEER: "peer"}, source="llm")
    assert len(units) == 29
    assert not {100, 101} & {unit["comment_id"] for unit in units}
    assert all(unit["student_relevance"] is None and unit["student_confidence"] is None for unit in units)
    assert all(unit["llm_relevance"] == "relevant" for unit in units)
    picked = gold_sample.sample(units, 20, seed=42)
    assert picked == gold_sample.sample(units, 20, seed=42)
    assert all(len(unit["stratum"].split("|")) == 3 for unit in picked)
    gold_path, labels_path = gold_sample.write_workbooks(picked, tmp_path, source="llm")
    assert gold_path.name == "gold-llm-20.xlsx"
    assert labels_path.name == "gold-llm-20-model-labels.xlsx"
    workbook = openpyxl.load_workbook(gold_path)
    assert workbook["标注"].max_row == 21
    assert all(row[6].value is None and row[7].value is None
               for row in workbook["标注"].iter_rows(min_row=2))
    assert "只核对 Luna" in workbook["说明"]["A1"].value
    labels = openpyxl.load_workbook(labels_path)
    assert all(row[10].value is None and row[11].value == "llm"
               for row in labels.active.iter_rows(min_row=2))
    model = evaluate_gold.read_model_labels(labels_path)
    assert len(model) == 20
    human = {unit["id"]: {"relevance": "relevant", "attitude": "positive"} for unit in picked}
    result = evaluate_gold.evaluate(human, model)
    assert result["sample_source"] == "llm" and result["n"] == 20
    assert result["by_system"]["llm"]["relevance_accuracy"] == 1.0
    for system in ("student", "combined"):
        assert result["by_system"][system]["relevance_accuracy"] is None
        assert result["by_system"][system]["attitude_accuracy"] is None
        assert result["by_system"][system]["attitude_macro_f1"] is None
        assert result["by_system"][system]["n_relevance"] == 0
    payload = evaluate_gold.ai_validation_payload(result, "2026-09-16")
    assert payload["relevance_accuracy"] == 1.0
    assert payload["by_system"]["student"]["relevance_accuracy"] is None
    with engine.connect() as conn:
        assert conn.execute(select(annotations)).all() == before


def test_gold_db_consumers_exclude_unqualified_and_cross_product_comments(engine):
    now = datetime(2026, 8, 27)
    with engine.begin() as conn:
        conn.execute(insert(feeds), [
            {"feed_id": 3, "code": OWN, "source_ticker": "03033.HK", "posted_at": now,
             "feed_type": 1, "title": "未提及自身", "content": "普通帖子", "like_count": 0,
             "comment_count": 1, "image_count": 0, "raw_json_broken": False},
            {"feed_id": 4, "code": OWN, "source_ticker": "03033.HK", "posted_at": now,
             "feed_type": 1, "title": "$03033.HK$", "content": "合格帖子", "like_count": 0,
             "comment_count": 1, "image_count": 0, "raw_json_broken": False},
        ])
        conn.execute(insert(feed_mentions).values(
            feed_id=4, raw_ticker="03033.HK", market="HK", occurrences=1,
        ))
        conn.execute(insert(comments), [
            {"comment_id": 9001, "feed_id": 3, "content": "不合格样本正文", "author_uid": "u9001"},
            {"comment_id": 9002, "feed_id": 4, "content": "跨产品样本正文", "author_uid": "u9002"},
        ])
        extra = []
        for cid, code in ((9001, OWN), (9002, PEER)):
            for run_id in ("stu-1", "luna-1"):
                extra.append({
                    "target_type": "comment", "target_id": cid, "subject_code": code,
                    "kind": "relevance", "value_json": '"relevant"', "run_id": run_id,
                    "input_hash": f"scope-{cid}-{run_id}", "review_state": "pending",
                    "created_at": now,
                })
        conn.execute(insert(annotations), extra)

    units = gold_sample.collect_units(engine, ownership={OWN: "own", PEER: "peer"})
    assert not {9001, 9002} & {unit["comment_id"] for unit in units}

    gold = {
        "G9001": {"code": OWN, "text": "不合格样本正文", "title": "未提及自身", "parent": ""},
        "G9002": {"code": PEER, "text": "跨产品样本正文", "title": "$03033.HK$", "parent": ""},
    }
    model, stats = evaluate_gold.labels_from_db(engine, gold)
    assert model == {}
    assert stats["unmatched"] == 2


def test_gold_db_consumers_fail_closed_without_filter_readiness(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "gold-not-ready.db").as_posix())
    create_all(eng)
    with pytest.raises(RuntimeError, match="parent-feed comment filter is not ready"):
        gold_sample.collect_units(eng, ownership={OWN: "own"})
    with pytest.raises(RuntimeError, match="parent-feed comment filter is not ready"):
        evaluate_gold.labels_from_db(
            eng,
            {"G1": {"code": OWN, "text": "正文", "title": "标题", "parent": ""}},
        )


def test_evaluate_three_systems_and_validation_shape(engine, tmp_path):
    units = gold_sample.collect_units(engine, ownership={OWN: "own", PEER: "peer"})
    picked = gold_sample.sample(units, 30, seed=3)
    gold_path, labels_path = gold_sample.write_workbooks(picked, tmp_path, names={})

    # 人工：按学生标签抄一半、故意改一半
    wb = openpyxl.load_workbook(gold_path)
    ws = wb["标注"]
    by_id = {u["id"]: u for u in picked}
    for row in ws.iter_rows(min_row=2):
        u = by_id[row[0].value]
        rel, att = u["student_relevance"], u["student_attitude"]
        if int(u["id"][1:]) % 2 == 0:
            rel = "irrelevant" if rel == "relevant" else "relevant"
            att = "negative" if rel == "relevant" else None
        row[6].value = {"relevant": "相关", "irrelevant": "无关", "needs_context": "需上下文"}[rel]
        row[7].value = {"positive": "积极", "negative": "消极", "neutral": "中性"}.get(att) if rel == "relevant" else None
    wb.save(gold_path)

    gold = evaluate_gold.read_gold(gold_path)
    model = evaluate_gold.read_model_labels(labels_path)
    assert len(gold) == 30 and set(gold) == set(model)
    res = evaluate_gold.evaluate(gold, model)
    assert res["n"] == 30
    for system in ("student", "llm", "combined"):
        s = res["by_system"][system]
        assert 0 <= s["relevance_accuracy"] <= 1
        assert s["attitude_accuracy"] is None or 0 <= s["attitude_accuracy"] <= 1
        assert set(s["confusion"]["relevance"]) == {"relevant", "irrelevant", "needs_context"}
    # 学生系统：偶数编号被改了 ⇒ 相关性准确率恰好 = 奇数占比
    odd = sum(1 for k in gold if int(k[1:]) % 2 == 1)
    assert res["by_system"]["student"]["relevance_accuracy"] == pytest.approx(odd / 30, abs=1e-4)

    payload = evaluate_gold.ai_validation_payload(res, "2026-09-15")
    assert set(payload) == {"level", "n", "date", "relevance_accuracy", "attitude_accuracy", "attitude_macro_f1", "by_system"}
    assert payload["level"] == "spot_check" and payload["n"] == 30 and payload["date"] == "2026-09-15"
    assert set(payload["by_system"]) == {"student", "llm", "combined"}
    for s in payload["by_system"].values():
        assert set(s) == {"relevance_accuracy", "attitude_accuracy", "attitude_macro_f1"}
    assert payload["relevance_accuracy"] == payload["by_system"]["combined"]["relevance_accuracy"]

    with engine.connect() as conn:
        before = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "annotation_revision")).scalar()
    evaluate_gold.write_validation(engine, payload)
    with engine.connect() as conn:
        stored = json.loads(conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "ai_validation")).scalar_one())
        after = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "annotation_revision")).scalar()
    assert stored == payload and after and after != before


def test_evaluate_never_writes_zero_for_missing():
    gold = {"G0001": {"relevance": "irrelevant", "attitude": None, "note": None}}
    model = {"G0001": {"stratum": "x", "student": {"relevance": "irrelevant", "attitude": None,
                                                   "relevance_p": 0.9, "attitude_p": None},
                       "llm": {"relevance": None, "attitude": None}}}
    res = evaluate_gold.evaluate(gold, model)
    s = res["by_system"]["student"]
    assert s["relevance_accuracy"] == 1.0
    assert s["attitude_accuracy"] is None and s["attitude_macro_f1"] is None  # 没有人填态度 ⇒ null 不是 0
    assert res["by_system"]["combined"]["relevance_accuracy"] == 1.0  # Luna 没标 ⇒ 用学生


def test_relevant_precision_recall_and_release_gate_cover_complaint_samples():
    gold = {
        "G0001": {"relevance": "relevant", "attitude": "positive", "note": "投诉样本"},
        "G0002": {"relevance": "relevant", "attitude": "neutral", "note": None},
        "G0003": {"relevance": "irrelevant", "attitude": None, "note": "投诉样本"},
        "G0004": {"relevance": "irrelevant", "attitude": None, "note": "投诉（自由备注不能进门禁）"},
    }
    predictions = ["relevant", "irrelevant", "relevant", "irrelevant"]
    model = {
        gid: {
            "sample_source": "llm",
            "student": {"relevance": None, "attitude": None,
                        "relevance_p": None, "attitude_p": None},
            "llm": {"relevance": prediction,
                    "attitude": "positive" if prediction == "relevant" else None},
        }
        for gid, prediction in zip(gold, predictions)
    }
    model["G0001"]["regression_case_id"] = "feedback-3037-explicit-self"
    model["G0003"]["regression_case_id"] = "feedback-3037-underlying-hsi-only"

    result = evaluate_gold.evaluate(gold, model)
    llm = result["by_system"]["llm"]
    assert llm["relevant_precision"] == 0.5
    assert llm["relevant_recall"] == 0.5
    assert llm["complaint_n"] == 2
    assert llm["complaint_passed"] is False
    gate = evaluate_gold.relevance_quality_gate(result, system="llm", min_samples=4)
    assert gate == {
        "passed": False,
        "system": "llm",
        "minSamples": 4,
        "sampleCount": 4,
        "minPrecision": 0.95,
        "precision": 0.5,
        "minRecall": 0.9,
        "recall": 0.5,
        "complaintCount": 2,
        "complaintsPassed": False,
    }


def test_release_report_recomputes_metrics_and_binds_the_v3_model_policy():
    policy = {
        "model": "provider-returned-snapshot",
        "requestedModel": "gpt-5.6-luna",
        "promptVersion": "comment-product-v3",
        "schemaVersion": "v2",
        "taxonomyVersion": "v2",
    }
    llm = {
        "n_relevance": 400,
        "relevant_precision": 0.9524,
        "relevant_recall": 0.9,
        "complaint_n": 3,
        "complaint_pass_count": 3,
        "complaint_passed": True,
        "confusion": {
            "relevance": {
                "relevant": {"relevant": 180, "irrelevant": 20, "needs_context": 0, "none": 0},
                "irrelevant": {"relevant": 9, "irrelevant": 191, "needs_context": 0, "none": 0},
                "needs_context": {"relevant": 0, "irrelevant": 0, "needs_context": 0, "none": 0},
            },
        },
    }
    report = {
        "reportType": "comment-relevance-human-gold-v1",
        "sample_source": "llm",
        "n": 400,
        "n_gold": 400,
        "policy": policy,
        "by_system": {"student": {}, "llm": llm, "combined": {}},
        "fixedRegressions": _passing_fixed_regressions(),
        "qualityGate": {"passed": True},
        "ai_validation": {"level": "spot_check", "n": 400},
    }

    checked = evaluate_gold.validate_release_report(report, expected_policy=policy)
    assert checked["passed"] is True

    forged = json.loads(json.dumps(report))
    forged["by_system"]["llm"]["relevant_precision"] = 1.0
    with pytest.raises(ValueError, match="confusion matrix"):
        evaluate_gold.validate_release_report(forged, expected_policy=policy)

    below_threshold = json.loads(json.dumps(report))
    below_threshold["by_system"]["llm"]["confusion"]["relevance"]["irrelevant"].update(
        relevant=12, irrelevant=188,
    )
    below_threshold["by_system"]["llm"]["relevant_precision"] = 0.9375
    with pytest.raises(ValueError, match="did not pass"):
        evaluate_gold.validate_release_report(below_threshold, expected_policy=policy)

    old_v2 = json.loads(json.dumps(report))
    old_v2["policy"]["promptVersion"] = "comment-product-v2"
    with pytest.raises(ValueError, match="comment-product-v3"):
        evaluate_gold.validate_release_report(old_v2)

    missing_case = json.loads(json.dumps(report))
    missing_case["fixedRegressions"]["commentCases"].pop()
    with pytest.raises(ValueError, match="every fixed complaint case"):
        evaluate_gold.validate_release_report(missing_case, expected_policy=policy)

    fake_note_case = json.loads(json.dumps(report))
    fake_note_case["fixedRegressions"]["commentCases"][-1]["caseId"] = "annotator-note-投诉"
    with pytest.raises(ValueError, match="every fixed complaint case"):
        evaluate_gold.validate_release_report(fake_note_case, expected_policy=policy)

    wrong_official = json.loads(json.dumps(report))
    wrong_official["fixedRegressions"]["officialAttributionCases"][0].update(
        actualCodes=["3068"], passed=True,
    )
    with pytest.raises(ValueError, match="Official attribution regression failed"):
        evaluate_gold.validate_release_report(wrong_official, expected_policy=policy)


def test_release_policy_comes_from_every_scored_llm_run_not_runtime_claims(engine, tmp_path):
    units = gold_sample.collect_units(engine, ownership={OWN: "own", PEER: "peer"}, source="llm")
    picked = gold_sample.sample(units, 20, seed=4)
    gold_path, labels_path = gold_sample.write_workbooks(picked, tmp_path, names={}, source="llm")
    _fill_gold(gold_path, picked, flip_every=999)
    gold = evaluate_gold.read_gold(gold_path)
    model = evaluate_gold.read_model_labels(labels_path)

    assert evaluate_gold.release_policy(gold, model) == {
            "model": "m", "requestedModel": "m", "promptVersion": "comment-product-v3",
        "schemaVersion": "v2", "taxonomyVersion": "v2",
    }

    first = next(iter(model.values()))
    first["llm_policy"] = {**first["llm_policy"], "promptVersion": "comment-product-v2"}
    with pytest.raises(ValueError, match="mixed"):
        evaluate_gold.release_policy(gold, model)


def test_combined_routing_rule():
    m = {"student": {"relevance": "relevant", "attitude": "positive", "relevance_p": 0.9, "attitude_p": 0.9},
         "llm": {"relevance": "irrelevant", "attitude": None}}
    assert evaluate_gold.combined_label(m, 0.85)["relevance"] == "relevant"
    m["student"]["attitude_p"] = 0.6
    assert evaluate_gold.combined_label(m, 0.85)["relevance"] == "irrelevant"
    m["student"].update(relevance="needs_context", relevance_p=0.99)
    assert evaluate_gold.combined_label(m, 0.85)["relevance"] == "irrelevant"


def test_combined_uses_whichever_side_has_a_label():
    only_llm = {"student": {"relevance": None, "attitude": None, "relevance_p": None, "attitude_p": None},
                "llm": {"relevance": "relevant", "attitude": "negative"}}
    assert evaluate_gold.combined_label(only_llm, 0.85) == {"relevance": "relevant", "attitude": "negative"}
    only_student = {"student": {"relevance": "irrelevant", "attitude": None, "relevance_p": 0.5, "attitude_p": None},
                    "llm": {"relevance": None, "attitude": None}}
    # 学生不够置信，但 Luna 没标过 ⇒ 仍然只能是学生
    assert evaluate_gold.combined_label(only_student, 0.85) == {"relevance": "irrelevant", "attitude": None}
    neither = {"student": {"relevance": None, "attitude": None, "relevance_p": None, "attitude_p": None},
               "llm": {"relevance": None, "attitude": None}}
    assert evaluate_gold.combined_label(neither, 0.85) == {"relevance": None, "attitude": None}


def test_systems_are_scored_only_on_rows_they_labeled():
    gold = {"G0001": {"relevance": "relevant", "attitude": "positive", "note": None},
            "G0002": {"relevance": "irrelevant", "attitude": None, "note": None}}
    model = {
        # Luna 判过且对；学生没判
        "G0001": {"stratum": "x", "student": {"relevance": None, "attitude": None, "relevance_p": None, "attitude_p": None},
                  "llm": {"relevance": "relevant", "attitude": "positive"}},
        # 两边都没判：combined 记错，student／llm 不进分母
        "G0002": {"stratum": "x", "student": {"relevance": None, "attitude": None, "relevance_p": None, "attitude_p": None},
                  "llm": {"relevance": None, "attitude": None}},
    }
    res = evaluate_gold.evaluate(gold, model)
    llm = res["by_system"]["llm"]
    assert llm["relevance_accuracy"] == 1.0 and llm["n_relevance"] == 1 and llm["n_unlabeled"] == 1
    assert llm["coverage"] == 0.5
    student = res["by_system"]["student"]
    assert student["relevance_accuracy"] is None and student["attitude_accuracy"] is None
    assert student["attitude_macro_f1"] is None and student["n_relevance"] == 0
    comb = res["by_system"]["combined"]
    assert comb["relevance_accuracy"] == 0.5 and comb["n_relevance"] == 2 and comb["n_unlabeled"] == 1
    payload = evaluate_gold.ai_validation_payload(res, "2026-09-16")
    assert payload["by_system"]["student"] == {"relevance_accuracy": None, "attitude_accuracy": None,
                                                "attitude_macro_f1": None}
    assert payload["relevance_accuracy"] == 0.5


def test_llm_only_pool_and_workbooks(engine, tmp_path):
    units = gold_sample.collect_units(engine, ownership={OWN: "own", PEER: "peer"}, llm_only=True)
    # fixture：偶数序号有 Luna 现行行 ⇒ 30 条
    assert len(units) == 30 and all(u["llm_relevance"] is not None for u in units)
    strata = {gold_sample.stratum_key(u) for u in units}
    assert all(len(k.split("|")) == 3 for k in strata)  # 没有置信带那一段
    picked = gold_sample.sample(units, 12, seed=2)
    gold, labels = gold_sample.write_workbooks(picked, tmp_path, names={}, llm_only=True)
    assert gold.name == "gold-llm-12.xlsx" and labels.name == "gold-llm-12-model-labels.xlsx"
    wb = openpyxl.load_workbook(gold)
    guide = [r[0].value for r in wb["说明"].iter_rows()]
    assert guide[0].startswith("本表只核对 Luna 现行结论") and "不能据此评估学生模型" in guide[1]
    assert "大盘看跌" in "\n".join(str(x) for x in guide)
    ws2 = openpyxl.load_workbook(labels).active
    header = [c.value for c in ws2[1]]
    assert header == list(gold_sample.LABEL_COLUMNS)
    rows = list(ws2.iter_rows(min_row=2, values_only=True))
    assert all(r[header.index("学生相关性")] is None and r[header.index("学生模型")] is None for r in rows)
    assert all(r[header.index("Luna相关性")] is not None for r in rows)


def _fill_gold(gold_path, picked, flip_every=2):
    """人工：按 Luna 标签抄一半、故意改一半。返回 `{编号: (rel, att)}`。"""
    wb = openpyxl.load_workbook(gold_path)
    ws = wb["标注"]
    by_id = {u["id"]: u for u in picked}
    truth = {}
    for row in ws.iter_rows(min_row=2):
        u = by_id[row[0].value]
        rel, att = u["llm_relevance"], u["llm_attitude"]
        if int(u["id"][1:]) % flip_every == 0:
            rel = "irrelevant" if rel == "relevant" else "relevant"
            att = "negative" if rel == "relevant" else None
        row[6].value = {"relevant": "相关", "irrelevant": "无关", "needs_context": "需上下文"}[rel]
        row[7].value = {"positive": "积极", "negative": "消极", "neutral": "中性"}.get(att) if rel == "relevant" else None
        truth[u["id"]] = (rel, att)
    wb.save(gold_path)
    return truth


def test_no_write_still_returns_nonzero_when_release_gate_fails(engine, tmp_path, monkeypatch):
    units = gold_sample.collect_units(engine, ownership={OWN: "own", PEER: "peer"}, source="llm")
    picked = gold_sample.sample(units, 10, seed=99)
    gold_path, _labels_path = gold_sample.write_workbooks(
        picked, tmp_path, names={}, source="llm",
    )
    _fill_gold(gold_path, picked, flip_every=999)
    monkeypatch.setattr(evaluate_gold, "OUT_DIR", tmp_path / "reports")

    assert evaluate_gold.main(["--file", str(gold_path), "--no-write"]) == 2


def test_evaluate_llm_only_sheet_student_null_and_combined_equals_llm(engine, tmp_path):
    units = gold_sample.collect_units(engine, ownership={OWN: "own", PEER: "peer"}, llm_only=True)
    picked = gold_sample.sample(units, 20, seed=5)
    gold_path, labels_path = gold_sample.write_workbooks(picked, tmp_path, names={}, llm_only=True)
    _fill_gold(gold_path, picked)
    gold = evaluate_gold.read_gold(gold_path)
    assert all(g["code"] and g["text"] for g in gold.values())  # 回库匹配要用的上下文一起读出
    model, source, stats = evaluate_gold.load_model_labels(gold, gold_path)
    assert source == "labels_file" and stats["rows"] == 20
    res = evaluate_gold.evaluate(gold, model)
    assert res["n"] == 20
    student, llm, comb = (res["by_system"][s] for s in ("student", "llm", "combined"))
    assert student["relevance_accuracy"] is None and student["n_relevance"] == 0
    assert llm["n_relevance"] == 20 and llm["coverage"] == 1.0
    odd = sum(1 for k in gold if int(k[1:]) % 2 == 1)
    assert llm["relevance_accuracy"] == pytest.approx(odd / 20, abs=1e-4)
    assert {k: comb[k] for k in ("relevance_accuracy", "attitude_accuracy", "attitude_macro_f1")} == \
        {k: llm[k] for k in ("relevance_accuracy", "attitude_accuracy", "attitude_macro_f1")}


def test_labels_fall_back_to_db_by_text_when_labels_file_missing(engine, tmp_path):
    units = gold_sample.collect_units(engine, ownership={OWN: "own", PEER: "peer"}, llm_only=True)
    picked = gold_sample.sample(units, 20, seed=9)
    gold_path, labels_path = gold_sample.write_workbooks(picked, tmp_path, names={}, llm_only=True)
    _fill_gold(gold_path, picked)
    gold = evaluate_gold.read_gold(gold_path)
    from_file = evaluate_gold.read_model_labels(labels_path)
    labels_path.unlink()
    model, source, stats = evaluate_gold.load_model_labels(gold, gold_path, engine_factory=lambda: engine)
    assert source == "db" and stats == {"matched": 20, "skipped_no_text": 0}
    for k in gold:
        assert model[k]["comment_id"] == from_file[k]["comment_id"]
        assert model[k]["llm"] == from_file[k]["llm"]
        # 回库取到的是库里的全部现行结论：fixture 里每条都有学生行，llm-only 标签表则刻意不带
        assert model[k]["student"]["relevance"] is not None and from_file[k]["student"]["relevance"] is None
    assert evaluate_gold.evaluate(gold, model)["by_system"]["llm"] == \
        evaluate_gold.evaluate(gold, from_file)["by_system"]["llm"]
    # 显式 --from-db 时即使标签表在也回库
    gold_sample.write_workbooks(picked, tmp_path, names={}, llm_only=True)
    _, source2, _ = evaluate_gold.load_model_labels(gold, gold_path, from_db=True, engine_factory=lambda: engine)
    assert source2 == "db"


def test_db_fallback_disambiguates_by_context_and_refuses_to_guess(engine, tmp_path):
    now = datetime(2026, 8, 26)
    with engine.begin() as conn:
        # 两条复读：同产品同正文，一条在标题「恒科」的帖子下、一条在「盈富」下；Luna 结论不同
        conn.execute(insert(feeds).values(
            feed_id=5, code=OWN, source_ticker="03033.HK", posted_at=now, feed_type=1,
            title="盈富", content="对照上下文", like_count=0, comment_count=1,
            image_count=0, raw_json_broken=False,
        ))
        conn.execute(insert(feed_mentions).values(
            feed_id=5, raw_ticker="03033.HK", market="HK", occurrences=1,
        ))
        conn.execute(insert(comments), [
            {"comment_id": 901, "feed_id": 1, "content": "呢隻幾時派息X", "author_uid": "a", "author_name": "n"},
            {"comment_id": 902, "feed_id": 5, "content": "呢隻幾時派息X", "author_uid": "b", "author_name": "n"},
            {"comment_id": 903, "feed_id": 1, "content": "呢隻幾時派息Y", "author_uid": "c", "author_name": "n"},
            {"comment_id": 904, "feed_id": 1, "content": "呢隻幾時派息Y", "author_uid": "d", "author_name": "n"},
        ])
        rows = []
        for cid, rel in ((901, "relevant"), (902, "irrelevant"), (903, "relevant"), (904, "irrelevant")):
            rows.append({"target_type": "comment", "target_id": cid, "subject_code": OWN, "kind": "relevance",
                         "value_json": json.dumps(rel), "calibrated_confidence": None, "run_id": "luna-1",
                         "input_hash": f"h{cid}", "review_state": "pending", "created_at": now})
        conn.execute(insert(annotations), rows)
    gold = {
        "G0001": {"relevance": "relevant", "attitude": None, "note": None, "code": OWN,
                  "text": "呢隻幾時派息X", "title": "盈富", "parent": None},          # 标题能分开 ⇒ 902
        "G0002": {"relevance": "relevant", "attitude": None, "note": None, "code": OWN,
                  "text": "呢隻幾時派息Y", "title": "恒科", "parent": None},          # 同帖同文、结论不同 ⇒ 歧义
        "G0003": {"relevance": "relevant", "attitude": None, "note": None, "code": OWN,
                  "text": "库里没有这句", "title": None, "parent": None},             # 匹配不到
        "G0004": {"relevance": "relevant", "attitude": None, "note": None, "code": None, "text": None},
    }
    model, stats = evaluate_gold.labels_from_db(engine, gold)
    assert stats == {"matched": 1, "ambiguous": 1, "unmatched": 1, "skipped_no_text": 1}
    assert model["G0001"]["comment_id"] == 902 and model["G0001"]["llm"]["relevance"] == "irrelevant"
    assert model["G0001"]["llm_policy"] == {
            "model": "m", "requestedModel": "m", "promptVersion": "comment-product-v3",
        "schemaVersion": "v2", "taxonomyVersion": "v2",
    }
    assert "G0002" not in model and "G0003" not in model


def test_stratum_agreement_counts_only(engine, tmp_path):
    units = gold_sample.collect_units(engine, ownership={OWN: "own", PEER: "peer"}, llm_only=True)
    picked = gold_sample.sample(units, 20, seed=11)
    gold_path, labels_path = gold_sample.write_workbooks(picked, tmp_path, names={}, llm_only=True)
    _fill_gold(gold_path, picked)
    gold = evaluate_gold.read_gold(gold_path)
    model = evaluate_gold.read_model_labels(labels_path)
    agg = evaluate_gold.stratum_agreement(gold, model, "llm")
    assert sum(r["n"] for r in agg.values()) == 20
    total_agree = sum(r["relevance_agree"] for r in agg.values())
    assert total_agree == round(evaluate_gold.evaluate(gold, model)["by_system"]["llm"]["relevance_accuracy"] * 20)
    assert all(set(r) == {"n", "relevance_agree", "attitude_n", "attitude_agree"} for r in agg.values())
    text = json.dumps(agg, ensure_ascii=False)
    assert "費率" not in text and "點差" not in text  # 只有计数，没有原文


def test_apply_writes_report_payload_without_xlsx(engine, tmp_path, monkeypatch):
    payload = {"level": "spot_check", "n": 400, "date": "2026-09-16",
               "relevance_accuracy": 0.35, "attitude_accuracy": 0.12, "attitude_macro_f1": 0.17,
               "by_system": {"student": {"relevance_accuracy": None, "attitude_accuracy": None, "attitude_macro_f1": None},
                             "llm": {"relevance_accuracy": 0.35, "attitude_accuracy": 0.12, "attitude_macro_f1": 0.17},
                             "combined": {"relevance_accuracy": 0.35, "attitude_accuracy": 0.12, "attitude_macro_f1": 0.17}}}
    report = tmp_path / "gold-eval-x.json"
    report.write_text(json.dumps({
        "reportType": "comment-relevance-human-gold-v1",
        "stamp": "x",
        "sample_source": "llm",
        "n": 400,
        "n_gold": 400,
        "policy": {"model": "provider-returned-snapshot", "requestedModel": "m",
                   "promptVersion": "comment-product-v3",
                       "schemaVersion": "v2", "taxonomyVersion": "v2",
                       "commentRouteVersion": COMMENT_ROUTE_VERSION,
                       "productPoolDigest": product_pool_digest()},
        "qualityGate": {"passed": True},
        "fixedRegressions": _passing_fixed_regressions(),
        "by_system": {
            "student": {},
            "combined": {},
            "llm": {
                "n_relevance": 400,
                "relevant_precision": 0.9583,
                "relevant_recall": 0.92,
                "complaint_n": 3,
                "complaint_pass_count": 3,
                "complaint_passed": True,
                "confusion": {"relevance": {
                    "relevant": {"relevant": 184, "irrelevant": 16, "needs_context": 0, "none": 0},
                    "irrelevant": {"relevant": 8, "irrelevant": 192, "needs_context": 0, "none": 0},
                    "needs_context": {"relevant": 0, "irrelevant": 0, "needs_context": 0, "none": 0},
                }},
            },
        },
        "ai_validation": payload,
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(evaluate_gold, "make_engine", lambda: engine)
    cfg = evaluate_gold.config.load(
        model="m", prompt_version="comment-product-v3", schema_version="v2", taxonomy_version="v2",
    )
    monkeypatch.setattr(evaluate_gold.config, "load", lambda **_kwargs: cfg)
    with engine.begin() as conn:
        conn.execute(insert(meta_kv), [
            {"k": key, "v": value}
            for key, value in route_readiness_values().items()
        ])
    assert evaluate_gold.main(["--apply", str(report)]) == 0
    with engine.connect() as conn:
        stored = json.loads(conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "ai_validation")).scalar_one())
    assert stored == payload
    # 半份记录不写
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"ai_validation": {"level": "spot_check", "n": 1}}), encoding="utf-8")
    with pytest.raises(SystemExit):
        evaluate_gold.main(["--apply", str(bad)])
    below_gate = tmp_path / "below-gate.json"
    below_gate.write_text(json.dumps({
        "sample_source": "llm",
        "by_system": {
            "llm": {
                "n_relevance": 399,
                "relevant_precision": 1.0,
                "relevant_recall": 1.0,
                "complaint_n": 1,
                "complaint_passed": True,
            },
        },
        "ai_validation": payload,
    }), encoding="utf-8")
    with pytest.raises(SystemExit, match="未通过"):
        evaluate_gold.main(["--apply", str(below_gate)])
    with pytest.raises(SystemExit):
        evaluate_gold.main(["--apply", str(report), "--no-write"])


def test_recheck_roundtrip_overrides_only_filled_rows(engine, tmp_path):
    units = gold_sample.collect_units(engine, ownership={OWN: "own", PEER: "peer"}, llm_only=True)
    picked = gold_sample.sample(units, 20, seed=13)
    gold_path, labels_path = gold_sample.write_workbooks(picked, tmp_path, names={}, llm_only=True)
    _fill_gold(gold_path, picked)  # 偶数编号故意改错 ⇒ 分歧行 = 偶数编号
    gold = evaluate_gold.read_gold(gold_path)
    model = evaluate_gold.read_model_labels(labels_path)
    ids = evaluate_gold.disputed_rows(gold, model, "llm")
    assert ids and all(int(i[1:]) % 2 == 0 for i in ids)

    out = tmp_path / "gold-llm-20-recheck.xlsx"
    evaluate_gold.write_recheck_workbook(gold, ids, out, names={OWN: "恒科"})
    wb = openpyxl.load_workbook(out)
    ws = wb["标注"]
    assert [c.value for c in ws[1]] == list(evaluate_gold.RECHECK_COLUMNS)
    assert ws.max_row == len(ids) + 1
    body = "\n".join(str(c.value) for row in ws.iter_rows() for c in row if c.value is not None)
    assert "relevant" not in body and "Luna" not in body  # 不带模型标签
    assert "无关" in "\n".join(str(r[0].value) for r in wb["说明"].iter_rows())

    # 复核：把前两行改回模型的判法、其余留空 ⇒ 只覆盖两行
    by_id = {u["id"]: u for u in picked}
    for i, row in enumerate(ws.iter_rows(min_row=2)):
        if i >= 2:
            row[6].value, row[7].value = None, None
            continue
        u = by_id[row[0].value]
        row[6].value = {"relevant": "相关", "irrelevant": "无关", "needs_context": "需上下文"}[u["llm_relevance"]]
        row[7].value = {"positive": "积极", "negative": "消极", "neutral": "中性"}.get(u["llm_attitude"]) \
            if u["llm_relevance"] == "relevant" else None
    wb.save(out)
    merged, n_over, n_changed = evaluate_gold.apply_recheck(gold, evaluate_gold.read_gold(out))
    assert (n_over, n_changed) == (2, 2)
    before = evaluate_gold.evaluate(gold, model)["by_system"]["llm"]["relevance_accuracy"]
    after = evaluate_gold.evaluate(merged, model)["by_system"]["llm"]["relevance_accuracy"]
    assert after == pytest.approx(before + 2 / 20, abs=1e-4)


def test_read_model_labels_rejects_sheet_without_label_columns(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["编号", "comment_id", "产品代码"])
    ws.append(["G0001", 1, OWN])
    p = tmp_path / "x.xlsx"
    wb.save(p)
    with pytest.raises(ValueError):
        evaluate_gold.read_model_labels(p)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["编号", "Luna相关性", "Luna态度"])  # 没有学生列、没有 comment_id：容缺
    ws.append(["G0001", "相关", "积极"])
    p2 = tmp_path / "y.xlsx"
    wb.save(p2)
    m = evaluate_gold.read_model_labels(p2)
    assert m["G0001"]["llm"] == {"relevance": "relevant", "attitude": "positive"}
    assert m["G0001"]["student"]["relevance"] is None and m["G0001"]["comment_id"] is None


def test_probe_concurrency_reports_429_and_percentiles():
    from scripts import probe_gateway
    from ai import config

    class R:
        def __init__(self, status, headers=None):
            self.status_code, self.headers = status, headers or {}

    calls = []

    def fake_post(cfg, path, body, timeout=120):
        import time
        i = len(calls)
        calls.append(path)
        time.sleep(0.01)
        return (R(429, {"Retry-After": "2"}) if i % 4 == 0 else R(200)), 0.1 * (i + 1)

    cfg = config.load(model="m")
    prompt = probe_gateway.get_prompt("comment_product")
    rep = probe_gateway.probe_concurrency(cfg, prompt, 8, post=fake_post)
    assert rep["n"] == 8 and len(rep["status_codes"]) == 8
    assert rep["n_429"] == 2 and rep["retry_after_headers"] == ["2", "2"]
    assert rep["p50_seconds"] is not None and rep["p95_seconds"] >= rep["p50_seconds"]
    assert "429" in rep["verdict"]
