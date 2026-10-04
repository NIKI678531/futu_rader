"""Fixed v3 release regressions used by calibration and the production gate.

The manifest is code-reviewed input, not an annotator-controlled ``备注`` field.
Comment cases are injected into the bounded calibration sample.  Official-post
cases are a separate deterministic regression because they do not belong to the
``comment_product`` LLM taxonomy.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace


MANIFEST_PATH = Path(__file__).with_name("comment_product_v3_regressions.json")
REPO_ROOT = Path(__file__).resolve().parents[2]


def load_manifest():
    raw = MANIFEST_PATH.read_bytes()
    manifest = json.loads(raw.decode("utf-8"))
    return manifest, hashlib.sha256(raw).hexdigest()


def comment_case_rows(*, posted_at=None):
    """Return synthetic, non-persisted rows for the bounded calibration call."""
    manifest, _digest = load_manifest()
    rows = []
    for case in manifest["commentCases"]:
        parents = list(case.get("parentComments") or [])[:3]
        rows.append(SimpleNamespace(
            comment_id=case["commentId"],
            code=case["targetCode"],
            content=case["commentText"],
            author_uid=f"release-regression:{case['caseId']}",
            feed_id=case["commentId"],
            posted_at=posted_at,
            title=case.get("postTitle"),
            post_content=case.get("postContent"),
            parent_content=parents[0] if parents else None,
            grandparent_content=parents[1] if len(parents) > 1 else None,
            great_grandparent_content=parents[2] if len(parents) > 2 else None,
            regression_case_id=case["caseId"],
        ))
    return rows


def official_regression_results():
    """Execute the two feedback posts through the current attribution code."""
    manifest, _digest = load_manifest()
    backend_root = REPO_ROOT / "backend"
    for path in (REPO_ROOT, backend_root):
        if str(path) not in sys.path:
            sys.path.append(str(path))
    from backend.providers.sql import _attribute_official_products

    master = json.loads(
        (backend_root / "fixtures" / "demo" / "master.json").read_text(encoding="utf-8")
    )
    officials = {row["short"]: row for row in master["officials"]}
    results = []
    for case in manifest["officialAttributionCases"]:
        official = officials.get(case["officialShort"])
        if official is None:
            actual_codes, actual_status = [], "missing_official"
        else:
            actual_codes, actual_status = _attribute_official_products(
                master["products"], official, case["text"], case.get("bodyCodes") or [],
            )
        expected_codes = list(case["expectedCodes"])
        results.append({
            "caseId": case["caseId"],
            "feedId": case["feedId"],
            "sourceAnchorCode": case["sourceAnchorCode"],
            "expectedStatus": case["expectedStatus"],
            "expectedCodes": expected_codes,
            "actualStatus": actual_status,
            "actualCodes": list(actual_codes),
            "passed": (
                actual_status == case["expectedStatus"]
                and list(actual_codes) == expected_codes
                and case["sourceAnchorCode"] not in actual_codes
            ),
        })
    return results


def build_report_evidence(gold, model):
    """Build privacy-safe, per-case evidence from the actual gold/model rows."""
    manifest, digest = load_manifest()
    rows_by_case = {}
    for gid, prediction in model.items():
        case_id = prediction.get("regression_case_id")
        if case_id:
            rows_by_case.setdefault(case_id, []).append((gid, prediction))

    comment_results = []
    for case in manifest["commentCases"]:
        matches = rows_by_case.get(case["caseId"], [])
        gid, prediction = matches[0] if len(matches) == 1 else (None, None)
        human = gold.get(gid, {}).get("relevance") if gid else None
        actual = prediction.get("llm", {}).get("relevance") if prediction else None
        expected = case["expectedRelevance"]
        comment_results.append({
            "caseId": case["caseId"],
            "expectedRelevance": expected,
            "humanRelevance": human,
            "modelRelevance": actual,
            "passed": len(matches) == 1 and human == expected and actual == expected,
        })

    official_results = official_regression_results()
    passed = bool(comment_results) and all(row["passed"] for row in comment_results)
    passed = passed and bool(official_results) and all(row["passed"] for row in official_results)
    return {
        "manifestVersion": manifest["manifestVersion"],
        "manifestSha256": digest,
        "commentCases": comment_results,
        "officialAttributionCases": official_results,
        "passed": passed,
    }


def validate_report_evidence(evidence):
    """Fail closed unless every fixed case is present exactly once and passes."""
    manifest, digest = load_manifest()
    if not isinstance(evidence, dict):
        raise ValueError("Quality report is missing fixed regression evidence")
    if evidence.get("manifestVersion") != manifest["manifestVersion"] or evidence.get("manifestSha256") != digest:
        raise ValueError("Quality report regression manifest is stale or unknown")

    expected_comments = {row["caseId"]: row for row in manifest["commentCases"]}
    reported_comments = evidence.get("commentCases")
    if not isinstance(reported_comments, list):
        raise ValueError("Quality report is missing fixed comment regressions")
    ids = [row.get("caseId") for row in reported_comments if isinstance(row, dict)]
    if len(ids) != len(set(ids)) or set(ids) != set(expected_comments):
        raise ValueError("Quality report does not cover every fixed complaint case exactly once")
    for row in reported_comments:
        expected = expected_comments[row["caseId"]]["expectedRelevance"]
        if (
            row.get("expectedRelevance") != expected
            or row.get("humanRelevance") != expected
            or row.get("modelRelevance") != expected
            or row.get("passed") is not True
        ):
            raise ValueError(f"Fixed complaint regression failed: {row['caseId']}")

    current_official = {row["caseId"]: row for row in official_regression_results()}
    reported_official = evidence.get("officialAttributionCases")
    if not isinstance(reported_official, list):
        raise ValueError("Quality report is missing official-attribution regressions")
    ids = [row.get("caseId") for row in reported_official if isinstance(row, dict)]
    if len(ids) != len(set(ids)) or set(ids) != set(current_official):
        raise ValueError("Quality report does not cover both official feedback posts exactly once")
    for row in reported_official:
        current = current_official[row["caseId"]]
        if row != current or current["passed"] is not True:
            raise ValueError(f"Official attribution regression failed: {row['caseId']}")
    if evidence.get("passed") is not True:
        raise ValueError("Quality report fixed regressions are not marked as passed")
    return True
