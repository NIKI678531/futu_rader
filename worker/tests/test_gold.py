"""人工核对集：分层抽样与 Excel 形状、评估三套系统、`meta_kv.ai_validation` 形状；probe --concurrency 统计。"""

import json
import os
import sys
from datetime import datetime

import pytest
from sqlalchemy import insert, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from radar_db import create_all, make_engine  # noqa: E402
from radar_db.schema import annotation_runs, annotations, comments, feeds, meta_kv  # noqa: E402
from scripts import evaluate_gold, gold_sample  # noqa: E402

openpyxl = pytest.importorskip("openpyxl")

OWN, PEER = "3033", "2800"


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "g.db").as_posix())
    create_all(eng)
    now = datetime(2026, 8, 26)
    with eng.begin() as conn:
        for rid, prov in (("stu-1", "local_model"), ("luna-1", "openai_compatible"), ("rule-1", "rule")):
            conn.execute(insert(annotation_runs).values(
                run_id=rid, task="comment_product", provider=prov, model_id="m", prompt_version="p",
                taxonomy_version="v2", schema_version="v2", started_at=now, status="done",
                input_count=0, success_count=0, error_count=0))
        conn.execute(insert(feeds), [
            {"feed_id": 1, "code": OWN, "posted_at": now, "feed_type": 1, "title": "恒科", "content": "c",
             "like_count": 0, "comment_count": 0, "image_count": 0, "raw_json_broken": False},
            {"feed_id": 2, "code": PEER, "posted_at": now, "feed_type": 1, "title": "盈富", "content": "c",
             "like_count": 0, "comment_count": 0, "image_count": 0, "raw_json_broken": False},
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
    assert ws2.max_row == 21


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
        conn.execute(insert(comments), [
            {"comment_id": 901, "feed_id": 1, "content": "呢隻幾時派息X", "author_uid": "a", "author_name": "n"},
            {"comment_id": 902, "feed_id": 2, "content": "呢隻幾時派息X", "author_uid": "b", "author_name": "n"},
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
    assert "G0002" not in model and "G0003" not in model


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
