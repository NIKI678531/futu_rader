"""Official HKEX ETP catalog normalization and staging rules."""

import os
import sys
from datetime import date, datetime

from sqlalchemy import select

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "worker"))

from jobs import hkex_catalog  # noqa: E402
from radar_db import create_all, make_engine  # noqa: E402
from radar_db.product_catalog import (  # noqa: E402
    ACTIVE_STATUS,
    PENDING_REVIEW_STATUS,
    PENDING_SOURCE_STATUS,
    catalog_status,
    load_products,
    review_candidate,
)
from radar_db.schema import product_catalog_entries  # noqa: E402


def row(code, name, *, issuer="", benchmark="恒生科技指数", currency="HKD", subtype="ETF"):
    return {
        "domain_settlement_counter": code,
        "full_name": name,
        "issuer_name": issuer,
        "first_trade_date": "2026-10-02",
        "underlying_index": benchmark,
        "product_type": "Exchange Traded Fund",
        "product_sub_category": subtype,
        "management_style": "Passive",
        "currency": currency,
        "asset_class": "Equity",
        "geographic_focus": "Hong Kong",
    }


def test_token_parser_uses_the_live_url_escaped_widget_token():
    html = """
    LabCI.getToken = function () {
      // return "Base64-AES-Encrypted-Token";
      return "abcDEF0123456789abcDEF0123456789abcDEF%2fabcDEF%2bxyz";
    };
    """

    assert hkex_catalog._token(html).endswith("%2bxyz")


def test_normalization_merges_currency_counters_and_detects_conflicts():
    products = hkex_catalog.normalize_hkex_rows({
        "data": [
            row("03456", "CSOP TEST ETF", issuer="CSOP Asset Management", currency="HKD"),
            row("3456.HK", "CSOP TEST ETF", issuer="CSOP Asset Management", currency="USD", subtype="Active"),
        ]
    })

    assert len(products) == 1
    assert products[0]["code"] == "3456"
    assert products[0]["currencies"] == ["HKD", "USD"]
    assert "productSubcategory" in products[0]["sourceConflicts"]


def test_candidate_policy_auto_stages_only_csop_or_exact_strategy_peers():
    active = [{
        "code": "3033", "name": "南方恒生科技", "issuer": "CSOP 南方东英",
        "ownership": "own", "benchmark": "恒生科技指数",
        "productSubcategory": "ETF", "managementStyle": "Passive", "struct": "ETF",
    }]
    candidates = hkex_catalog.normalize_hkex_rows({"data": [
        row("3456", "CSOP NEW ETF", issuer="CSOP Asset Management"),
        row("3457", "Peer Exact", issuer="Peer"),
        row("3458", "Peer Leveraged", issuer="Peer", subtype="Leveraged"),
        row("3459", "Conflict", issuer="CSOP Asset Management"),
    ]})
    next(item for item in candidates if item["code"] == "3459")["sourceConflicts"] = ["managementStyle"]

    classified = {item[0]["code"]: item[1:] for item in hkex_catalog.classify_candidates(candidates, active)}

    assert classified["3456"][0] == PENDING_SOURCE_STATUS
    assert classified["3457"][:2] == (PENDING_SOURCE_STATUS, "3033")
    assert classified["3458"][0] == PENDING_REVIEW_STATUS
    assert classified["3458"][2] == "benchmark_match_strategy_ambiguous"
    assert candidates[2]["source"]["catalogMatchCandidates"] == ["3033"]
    assert classified["3459"] == (PENDING_REVIEW_STATUS, None, "source_field_conflict")


def test_peer_review_requires_an_explicit_active_csop_mapping(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "review.db").as_posix())
    create_all(engine)
    hkex_catalog.sync(engine, payload={"data": [
        row("03033", "CSOP Hang Seng TECH ETF", issuer="CSOP Asset Management"),
        row("03458", "Peer Leveraged", issuer="Peer", subtype="Leveraged"),
    ]}, now=datetime(2026, 10, 5, 7))

    status = catalog_status(engine)
    candidate = next(item for item in status["candidates"] if item["code"] == "3458")
    assert candidate["suggestedOwnCodes"] == ["3033"]

    try:
        review_candidate(engine, "3458", "approve")
    except ValueError as exc:
        assert "ownCode" in str(exc)
    else:
        raise AssertionError("a fuzzy peer cannot be approved without a reviewed mapping")

    result = review_candidate(engine, "3458", "approve", own_code="3033")
    assert result == {"code": "3458", "status": PENDING_SOURCE_STATUS, "ownCode": "3033"}


def test_sync_is_idempotent_and_source_failure_preserves_active_catalog(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "catalog.db").as_posix())
    create_all(engine)
    payload = {"data": [row("03456", "CSOP NEW ETF", issuer="CSOP Asset Management")]}

    first = hkex_catalog.sync(engine, payload=payload, now=datetime(2026, 10, 5, 7))
    second = hkex_catalog.sync(engine, payload=payload, now=datetime(2026, 10, 6, 7))

    assert first["candidates"] == second["candidates"] == 1
    assert len(load_products(engine=engine)) == 135
    with engine.connect() as conn:
        staged = conn.execute(select(product_catalog_entries).where(
            product_catalog_entries.c.code == "3456"
        )).mappings().one()
    assert staged["status"] == PENDING_SOURCE_STATUS

    try:
        hkex_catalog.sync(engine, payload={"unexpected": []}, now=datetime(2026, 10, 7, 7))
    except ValueError:
        pass
    else:
        raise AssertionError("malformed HKEX payload must fail closed")

    assert len(load_products(engine=engine)) == 135
    status = catalog_status(engine)
    assert status["latestStatus"] == "failed"
    assert status["counts"]["failed"] == 1


def test_pending_catalog_activates_only_after_exact_full_receipt(tmp_path, monkeypatch):
    engine = make_engine("sqlite:///" + (tmp_path / "activate.db").as_posix())
    create_all(engine)
    hkex_catalog.sync(
        engine,
        payload={"data": [row("03456", "CSOP NEW ETF", issuer="CSOP Asset Management")]},
        now=datetime(2026, 10, 5, 7),
    )
    captured = {}

    class NoReceipt:
        def __init__(self, _engine, codes):
            captured["codes"] = set(codes)

        def complete_through(self, _run_id):
            return None

    monkeypatch.setattr("collection.source.MarketInsightMySqlAdapter", NoReceipt)
    assert hkex_catalog.activate_pending_if_receipt_matches(engine, object(), "all-1") == []
    assert "3456" in captured["codes"]

    class CompleteReceipt(NoReceipt):
        def complete_through(self, _run_id):
            return date(2026, 10, 5)

    monkeypatch.setattr("collection.source.MarketInsightMySqlAdapter", CompleteReceipt)
    assert hkex_catalog.activate_pending_if_receipt_matches(engine, object(), "all-1") == ["3456"]
    assert any(product["code"] == "3456" for product in load_products(engine=engine))

