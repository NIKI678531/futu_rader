import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import config
from scripts import calibrate


def test_nonempty_strata_report_is_json_serializable(tmp_path, monkeypatch):
    sample = SimpleNamespace(
        content="ETF fees are too high", code="3033", comment_id=1,
        author_uid="test-user", feed_id=1, parent_content=None,
        title="ETF discussion", post_content="Product discussion",
    )
    label = SimpleNamespace(
        attitude="negative", relevance="relevant", needs_review=False,
        compliance_tags=[],
    )
    cfg = config.load(model="test-model", prompt_version="comment-product-v2",
                      schema_version="v2", taxonomy_version="v2")
    monkeypatch.setattr(calibrate.config, "load", lambda: cfg)
    monkeypatch.setattr(calibrate, "make_engine", lambda: object())
    monkeypatch.setattr(calibrate, "candidates", lambda *args: [sample])
    monkeypatch.setattr(calibrate, "build_provider", lambda *args: object())
    monkeypatch.setattr(calibrate, "label_batch", lambda *args: ({"comment:1|product:3033": label}, 0))
    monkeypatch.setattr(calibrate.offpool_stocks, "load_from_db", lambda *args: [])
    monkeypatch.setattr(calibrate, "OUT_DIR", tmp_path)
    monkeypatch.setattr(calibrate, "default_data_dir", lambda: tmp_path)

    assert calibrate.main([
        "--codes", "3033", "--from", "2026-08-01", "--to", "2026-08-31", "--n", "1",
    ]) == 0

    report = json.loads(next(tmp_path.glob("calibration-*.json")).read_text(encoding="utf-8"))
    assert report["strata"] == {"zh-Hans|own": 1}
    assert report["b1_vs_b30"]["n"] == 1
    assert report["v1_vs_v2"]["n"] == 1