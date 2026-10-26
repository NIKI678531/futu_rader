"""Fetch and publish the current official HKEX ETP catalog.

A complete successful daily snapshot is the membership authority for the
monitoring pool: every listed ETP is active and products absent from the latest
complete snapshot become inactive.  Completeness guards reject short or
malformed responses before membership can change.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sqlalchemy import insert, select, update

import clock
from radar_db.product_catalog import (
    ACTIVE_STATUS,
    CATALOG_POLICY_VERSION,
    INACTIVE_STATUS,
    PENDING_REVIEW_STATUS,
    PENDING_SOURCE_STATUS,
    _entry_values,
    _put_meta,
    catalog_digest,
    load_products,
    seed_catalog,
)
from radar_db.schema import product_catalog_entries, product_catalog_snapshots

HKEX_PAGE = "https://www.hkex.com.hk/Market-Data/Securities-Prices/Exchange-Traded-Products?sc_lang=en"
HKEX_EXPORT = "https://www1.hkex.com.hk/hkexwidget/data/getetpexportdata"
USER_AGENT = "Mozilla/5.0 (compatible; futu-radar-hkex-catalog/1.0)"
LIVE_MINIMUM_ROWS = 100
LIVE_MINIMUM_PREVIOUS_RATIO = 0.8


def catalog_cron(value=None):
    """Daily HKEX membership refresh; ``off`` disables the worker-owned job."""
    import os

    raw = os.getenv("HKEX_CATALOG_CRON", "0 7 * * *") if value is None else value
    raw = raw.strip()
    return None if raw.lower() in ("", "off", "false", "none") else raw


def _get(url, *, timeout=45, referer=None):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json,text/javascript,*/*"}
    if referer:
        headers["Referer"] = referer
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _token(page):
    text = page.decode("utf-8", errors="replace") if isinstance(page, bytes) else str(page)
    # HKEX currently embeds a URL-escaped Base64 token in LabCI.getToken().
    # Ignore the commented sample value and accept future token rotations.
    candidates = re.findall(
        r"return\s+['\"]([A-Za-z0-9%+/=]{40,})['\"]\s*;",
        text,
    )
    if not candidates:
        raise ValueError("HKEX ETP page did not expose a 64-character API token")
    return candidates[-1]


def _jsonp(raw):
    text = raw.decode("utf-8-sig", errors="strict") if isinstance(raw, bytes) else str(raw)
    text = text.strip()
    if text.startswith("{") or text.startswith("["):
        return json.loads(text)
    start, end = text.find("("), text.rfind(")")
    if start < 0 or end <= start:
        raise ValueError("HKEX export was neither JSON nor JSONP")
    return json.loads(text[start + 1:end])


def _find_rows(node):
    if isinstance(node, dict):
        for key in ("DBDataList", "dataList", "DataList", "data"):
            value = node.get(key)
            if isinstance(value, list) and (not value or isinstance(value[0], dict)):
                return value
        for value in node.values():
            found = _find_rows(value)
            if found is not None:
                return found
    return None


def _text(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _value(row, *names):
    lower = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        value = lower.get(name.lower())
        if value not in (None, "", "-"):
            return value
    return None


def normalize_code(value):
    match = re.search(r"(?<!\d)(\d{1,5})(?:\.HK)?(?!\d)", _text(value), re.I)
    if not match:
        return None
    code = str(int(match.group(1)))
    return code if re.fullmatch(r"[1-9]\d{3}", code) else None


def _date(value):
    if isinstance(value, (int, float)):
        # HKEX exports JavaScript epoch milliseconds.
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date()
    text = _text(value)
    epoch = re.search(r"(?<!\d)(\d{11,13})(?!\d)", text)
    if epoch:
        return datetime.fromtimestamp(int(epoch.group(1)) / 1000, tz=timezone.utc).date()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            pass
    return None


def _bool(value):
    return _text(value).lower() in {"1", "true", "yes", "y", "eligible", "是"}


def normalize_hkex_rows(payload):
    source_rows = _find_rows(payload)
    if source_rows is None:
        raise ValueError("HKEX export has no ETP data list")
    grouped = {}
    for raw in source_rows:
        code = normalize_code(_value(raw, "domain_settlement_counter", "main_stock_code", "symbol", "stock_code"))
        if code is None:
            continue
        listed = _date(_value(raw, "first_trade_date", "listing_date", "list_date"))
        name = _text(_value(raw, "full_name", "name", "product_name", "etp_name"))
        issuer = _text(_value(raw, "issuer_name", "issuer"))
        if not name or not listed:
            continue
        candidate = {
            "code": code, "name": name, "issuer": issuer or "待核实",
            "listingDate": listed.isoformat(),
            "benchmark": _text(_value(raw, "underlying_index", "benchmark")) or None,
            "securityType": _text(_value(raw, "product_type", "security_type")) or "ETP",
            "productSubcategory": _text(_value(raw, "product_sub_category", "sub_category")) or None,
            "managementStyle": _text(_value(raw, "mgmt_style", "management_style")) or None,
            "replicationMethod": _text(_value(raw, "replication_method")) or None,
            "managementFee": _text(_value(raw, "management_fee")) or None,
            "assetClass": _text(_value(raw, "asset_class")) or None,
            "geographicFocus": _text(_value(raw, "geo_focus", "geographic_focus")) or None,
            "south": _bool(_value(raw, "southbound", "southbound_eligible")),
            "currencies": sorted({_text(v) for v in (
                _value(raw, "currency"), _value(raw, "etp_base_ccy"),
            ) if _text(v)}),
            "aliases": sorted({_text(v) for v in (
                _value(raw, "short_name", "shortname"), _value(raw, "name"),
            ) if _text(v)}),
            "source": raw,
        }
        previous = grouped.get(code)
        if previous is None:
            grouped[code] = candidate
        else:
            previous["currencies"] = sorted(set(previous["currencies"] + candidate["currencies"]))
            previous["aliases"] = sorted(set(previous["aliases"] + candidate["aliases"]))
            # Prefer the row with the richer name/issuer; static fields must not
            # silently conflict across currency counters.
            for field in ("benchmark", "productSubcategory", "managementStyle", "replicationMethod"):
                if previous.get(field) and candidate.get(field) and previous[field] != candidate[field]:
                    previous.setdefault("sourceConflicts", []).append(field)
                elif not previous.get(field):
                    previous[field] = candidate.get(field)
    return list(grouped.values())


def _norm(value):
    value = unicodedata.normalize("NFKC", _text(value)).casefold()
    value = re.sub(r"(?:net|gross|total)?\s*return|净总回报|總回報|净税|淨稅", "", value)
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", value)


def _strategy(product):
    text = " ".join(_text(product.get(key)) for key in ("productSubcategory", "managementStyle", "struct", "name"))
    direction = "inverse" if re.search(r"反向|inverse|-1x|-2x", text, re.I) else "leveraged" if re.search(r"槓桿|杠杆|leveraged|2x", text, re.I) else "plain"
    return (_norm(product.get("productSubcategory")), _norm(product.get("managementStyle")), direction)


def _sector(product):
    asset = _norm(product.get("assetClass"))
    geo = _norm(product.get("geographicFocus"))
    if any(word in asset for word in ("virtual", "crypto", "虚拟", "虛擬")):
        return "va"
    if any(word in asset for word in ("bond", "fixedincome", "债", "債")):
        return "fi"
    if any(word in asset for word in ("commodity", "gold", "商品", "黄金", "黃金")):
        return "cm"
    if any(word in geo for word in ("hongkong", "香港")):
        return "hk"
    if any(word in geo for word in ("china", "中国", "中國")):
        return "a"
    if any(word in geo for word in ("unitedstates", "美国", "美國")):
        return "us"
    return "apac"


def _struct(product):
    text = " ".join(_text(product.get(key)) for key in ("productSubcategory", "managementStyle", "name"))
    if re.search(r"covered call|備兌|备兑", text, re.I):
        return "备兑高息"
    if re.search(r"leveraged|槓桿|杠杆|inverse|反向", text, re.I):
        return "杠杆反向"
    if re.search(r"active|主動|主动", text, re.I):
        return "主动 ETF"
    return "ETF"


def classify_candidates(hkex_products, active_products):
    """Classify every HKEX-listed ETP as active.

    Benchmark matching remains useful optional metadata for peer-to-own
    comparisons, but it no longer gates monitoring-pool membership.
    """
    active_by_code = {product["code"]: product for product in active_products}
    hkex_by_code = {product["code"]: product for product in hkex_products}
    own = []
    for product in active_products:
        if product.get("ownership") != "own":
            continue
        own.append({**product, **{key: value for key, value in hkex_by_code.get(product["code"], {}).items() if value}})
    exact = {}
    for product in own:
        benchmark = _norm(product.get("benchmark"))
        if benchmark:
            exact.setdefault((benchmark, _strategy(product)), []).append(product["code"])

    classified = []
    for product in hkex_products:
        if product["code"] in active_by_code:
            classified.append((product, ACTIVE_STATUS, active_by_code[product["code"]].get("ownCode"), "existing_active"))
            continue
        issuer = _norm(product.get("issuer"))
        name = _norm(product.get("name"))
        if "csop" in issuer or "南方东英" in name or "南方東英" in name:
            classified.append((product, ACTIVE_STATUS, None, "hkex_active_csop_etp"))
            continue
        benchmark = _norm(product.get("benchmark"))
        matches = exact.get((benchmark, _strategy(product)), []) if benchmark else []
        own_code = matches[0] if len(matches) == 1 else None
        if len(matches) == 1:
            reason = "hkex_active_exact_peer"
        elif benchmark and any(key[0] == benchmark for key in exact):
            product.setdefault("source", {})["catalogMatchCandidates"] = sorted({
                code for (known_benchmark, _), codes in exact.items()
                if known_benchmark == benchmark
                for code in codes
            })
            reason = "hkex_active_peer_mapping_ambiguous"
        elif product.get("sourceConflicts"):
            reason = "hkex_active_etp_source_conflict"
        else:
            reason = "hkex_active_etp"
        classified.append((product, ACTIVE_STATUS, own_code, reason))
    return classified


def fetch_snapshot(*, as_of=None, timeout=45):
    audit_now = clock.now(timezone.utc)
    as_of = as_of or audit_now.date()
    page = _get(HKEX_PAGE, timeout=timeout)
    token = _token(page)
    qid = int(audit_now.timestamp() * 1000)
    params = urlencode({
        "lang": "en",
        "date_start": f"{as_of.isoformat()} 00:00:00",
        "date_end": f"{as_of.isoformat()} 00:00:00",
        "all": 1, "sort": 0, "order": 1, "qid": qid,
        "callback": f"futuRadar{qid}", "_": qid,
    })
    # The page returns an already escaped token and the HKEX widget appends it
    # verbatim. Passing it through urlencode again produces %252f and a 403.
    raw = _get(
        f"{HKEX_EXPORT}?token={token}&{params}",
        timeout=timeout,
        referer=HKEX_PAGE,
    )
    return raw, normalize_hkex_rows(_jsonp(raw))


def _require_complete_live_snapshot(engine, products):
    count = len(products)
    if count < LIVE_MINIMUM_ROWS:
        raise ValueError(
            f"HKEX active ETP snapshot is unexpectedly short: {count} < {LIVE_MINIMUM_ROWS}"
        )
    with engine.connect() as conn:
        previous = conn.execute(
            select(product_catalog_snapshots.c.row_count)
            .where(product_catalog_snapshots.c.status == "succeeded")
            .order_by(product_catalog_snapshots.c.fetched_at.desc())
            .limit(1)
        ).scalar_one_or_none()
    if previous and count < int(previous * LIVE_MINIMUM_PREVIOUS_RATIO):
        raise ValueError(
            "HKEX active ETP snapshot shrank below the completeness threshold: "
            f"{count} < {int(previous * LIVE_MINIMUM_PREVIOUS_RATIO)}"
        )


def sync(
    engine,
    *,
    raw=None,
    payload=None,
    now=None,
    as_of=None,
    enforce_completeness=None,
):
    now = now or clock.now()
    seed_catalog(engine, now=now)
    snapshot_id = "hkex-" + now.strftime("%Y%m%dT%H%M%S%f")
    try:
        if payload is None:
            raw, products = fetch_snapshot(as_of=as_of)
        else:
            raw = raw or json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            products = normalize_hkex_rows(payload)
        digest = hashlib.sha256(raw).hexdigest()
        if enforce_completeness is None:
            enforce_completeness = payload is None
        if enforce_completeness:
            _require_complete_live_snapshot(engine, products)
        current = load_products(engine=engine)
        classified = classify_candidates(products, current)
        seen_codes = {product["code"] for product in products}
        activated = 0
        reactivated = 0
        with engine.begin() as conn:
            conn.execute(insert(product_catalog_snapshots).values(
                snapshot_id=snapshot_id, fetched_at=now, source_url=HKEX_EXPORT,
                payload_sha256=digest, status="succeeded", row_count=len(products), error=None,
            ))
            for product, status, own_code, reason in classified:
                existing = conn.execute(select(product_catalog_entries).where(
                    product_catalog_entries.c.code == product["code"]
                )).mappings().first()
                normalized_issuer = _norm(product.get("issuer"))
                normalized_name = _norm(product.get("name"))
                is_csop = "csop" in normalized_issuer or "南方东英" in normalized_name or "南方東英" in normalized_name
                product = {**product, "ownership": "own" if is_csop else (existing["ownership"] if existing else "peer"),
                           "ownCode": own_code, "sector": _sector(product), "sectorName": {
                               "hk": "港股", "a": "A股", "us": "美股", "apac": "亚太及区域",
                               "fi": "固收", "cm": "商品", "va": "虚拟资产",
                           }[_sector(product)], "struct": _struct(product)}
                values = _entry_values(product, now, status=status, snapshot_id=snapshot_id, match_reason=reason)
                if existing:
                    if existing["status"] != ACTIVE_STATUS:
                        reactivated += 1
                    conflict_fields = set(product.get("sourceConflicts") or ())

                    def observed(column, source_field=None):
                        source_field = source_field or column
                        value = values[column]
                        if source_field in conflict_fields or value in (None, "", "[]"):
                            return existing[column]
                        return value

                    conn.execute(update(product_catalog_entries).where(
                        product_catalog_entries.c.code == product["code"]
                    ).values(
                        benchmark=observed("benchmark"), security_type=observed("security_type"),
                        product_subcategory=observed("product_subcategory", "productSubcategory"),
                        management_style=observed("management_style", "managementStyle"),
                        replication_method=observed("replication_method", "replicationMethod"),
                        management_fee=observed("management_fee"),
                        asset_class=observed("asset_class"), geographic_focus=observed("geographic_focus"),
                        currencies_json=observed("currencies_json"), aliases_json=observed("aliases_json"),
                        ownership=values["ownership"],
                        own_code=values["own_code"] or existing["own_code"],
                        sector=observed("sector"), sector_name=observed("sector_name"),
                        struct=observed("struct"), listing_date=observed("listing_date"),
                        # False is a valid HKEX value; a wholly missing field is
                        # distinguishable from it in the source payload.
                        southbound=(values["southbound"] if any(
                            key.lower() in {str(k).lower() for k in product.get("source", {})}
                            for key in ("southbound", "southbound_eligible")
                        ) else existing["southbound"]),
                        status=ACTIVE_STATUS, match_reason=reason,
                        source_snapshot_id=snapshot_id, source_json=values["source_json"], last_seen_at=now,
                    ))
                else:
                    conn.execute(insert(product_catalog_entries).values(**values))
                    activated += 1
            deactivated = conn.execute(update(product_catalog_entries).where(
                product_catalog_entries.c.code.not_in(seen_codes),
                product_catalog_entries.c.status.in_((
                    ACTIVE_STATUS, PENDING_SOURCE_STATUS, PENDING_REVIEW_STATUS,
                )),
            ).values(
                status=INACTIVE_STATUS,
                match_reason="absent_from_latest_hkex_snapshot",
            )).rowcount
            _put_meta(conn, "product_catalog_last_sync_at", now.isoformat())
            _put_meta(conn, "product_catalog_last_sync_status", "succeeded")
            _put_meta(conn, "product_catalog_policy_version", CATALOG_POLICY_VERSION)
            active_rows = conn.execute(select(product_catalog_entries).where(
                product_catalog_entries.c.status == ACTIVE_STATUS
            )).mappings().all()
            _put_meta(conn, "product_catalog_revision", catalog_digest(active_rows))
        return {
            "snapshotId": snapshot_id,
            "rows": len(products),
            "active": len(products),
            "activated": activated,
            "reactivated": reactivated,
            "deactivated": deactivated,
            "candidates": 0,
        }
    except Exception as exc:
        digest = hashlib.sha256(raw or b"").hexdigest()
        with engine.begin() as conn:
            conn.execute(insert(product_catalog_snapshots).values(
                snapshot_id=snapshot_id, fetched_at=now, source_url=HKEX_EXPORT,
                payload_sha256=digest, status="failed", row_count=0, error=str(exc)[:2000],
            ))
            _put_meta(conn, "product_catalog_last_sync_at", now.isoformat())
            _put_meta(conn, "product_catalog_last_sync_status", "failed")
        raise


def activate_pending_if_receipt_matches(target_engine, source_engine, source_run_id):
    """Atomically promote pending_source rows after exact upstream proof."""
    from collection.source import MarketInsightMySqlAdapter, SourceSchemaError

    seed_catalog(target_engine)
    with target_engine.connect() as conn:
        pending = [row[0] for row in conn.execute(select(product_catalog_entries.c.code).where(
            product_catalog_entries.c.status == PENDING_SOURCE_STATUS
        ))]
    if not pending:
        return []
    desired = {product["code"] for product in load_products(engine=target_engine)} | set(pending)
    try:
        adapter = MarketInsightMySqlAdapter(source_engine, desired)
        if adapter.complete_through(source_run_id) is None:
            return []
    except SourceSchemaError:
        return []
    with target_engine.begin() as conn:
        conn.execute(update(product_catalog_entries).where(
            product_catalog_entries.c.code.in_(pending),
            product_catalog_entries.c.status == PENDING_SOURCE_STATUS,
        ).values(status=ACTIVE_STATUS, reviewed_at=clock.now(), reviewed_by="source_receipt"))
        products = [dict(row) for row in conn.execute(select(product_catalog_entries).where(
            product_catalog_entries.c.status == ACTIVE_STATUS
        )).mappings()]
        _put_meta(conn, "product_catalog_revision", catalog_digest(products))
        _put_meta(conn, "product_catalog_activated_at", clock.now().isoformat())
    return pending


def desired_manifest(engine):
    """Machine-readable symbol set requested from the upstream collector."""
    from collection.source import configured_symbols_fingerprint

    seed_catalog(engine)
    with engine.connect() as conn:
        codes = sorted(row[0] for row in conn.execute(
            select(product_catalog_entries.c.code).where(
                product_catalog_entries.c.status.in_((ACTIVE_STATUS, PENDING_SOURCE_STATUS))
            )
        ))
    return {"codes": codes, "count": len(codes),
            "fingerprint": configured_symbols_fingerprint(codes)}


def main(argv=None):
    import argparse
    from radar_db import make_engine
    from radar_db.product_catalog import catalog_status

    parser = argparse.ArgumentParser(description="HKEX official ETP catalog sync")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync")
    sub.add_parser("status")
    sub.add_parser("manifest")
    args = parser.parse_args(argv)
    engine = make_engine()
    if args.command == "sync":
        result = sync(engine)
    elif args.command == "manifest":
        result = desired_manifest(engine)
    else:
        result = catalog_status(engine)
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
