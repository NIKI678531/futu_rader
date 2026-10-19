"""生产产品目录。

演示 fixture 继续保持设计验收时的 120 只产品；生产目录在其上合并经人工核验的
Excel 增量。代码为空或尚未分配的产品只进入 pending_products，绝不会进入抓取池。
"""

import json
import re
import hashlib
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError

from .clock import now as clock_now
from .schema import meta_kv, product_catalog_entries, product_catalog_snapshots


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
# 仓库／worker 镜像保留 backend/ 前缀；backend 镜像把 backend/ 内容直接复制到 /app。
FIXTURES_ROOT = (
    PACKAGE_ROOT / "backend" / "fixtures"
    if (PACKAGE_ROOT / "backend" / "fixtures").is_dir()
    else PACKAGE_ROOT / "fixtures"
)
DEMO_MASTER = FIXTURES_ROOT / "demo" / "master.json"
ADDITIONS = FIXTURES_ROOT / "product_catalog_additions.json"
KOL_ROSTER_UPDATES = FIXTURES_ROOT / "kol_roster_updates.json"
_CODE_RE = re.compile(r"^[1-9][0-9]{3}$")
_NEW_LISTING_DAYS = 30
CATALOG_POLICY_VERSION = "hkex-etp-catalog-v1"
ACTIVE_STATUS = "active"
PENDING_SOURCE_STATUS = "pending_source"
PENDING_REVIEW_STATUS = "pending_review"
_SECTOR_NAMES = {
    "hk": "港股",
    "a": "A股",
    "us": "美股",
    "apac": "亚太及区域",
    "fi": "固收",
    "cm": "商品",
    "va": "虚拟资产",
    "sgl": "单一股票",
}


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _coerce_date(value, *, field):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"Invalid {field}: {value!r}") from exc
    raise TypeError(f"{field} must be an ISO date string, date, or datetime")


def _catalog_as_of(additions, as_of):
    value = as_of
    if value is None:
        value = additions.get("provenance", {}).get("reviewedAsOf")
    if value is None:
        raise ValueError("Catalog additions must define provenance.reviewedAsOf")
    return _coerce_date(value, field="catalog as_of")


def _is_new_listing(listing_date, as_of):
    listed_on = _coerce_date(listing_date, field="listingDate")
    age_days = (as_of - listed_on).days
    return 0 <= age_days < _NEW_LISTING_DAYS


def load_products(base_path=DEMO_MASTER, additions_path=ADDITIONS, *, as_of=None, engine=None):
    """返回生产产品池，并按目录审阅日重算新品状态。

    ``as_of`` 可传 ISO 日期字符串、``date`` 或 ``datetime``；省略时使用增量
    文件的 ``provenance.reviewedAsOf``。上市当天起未满 30 个日历日才算新品。
    """
    if engine is not None:
        db_products = _load_database_products(engine, as_of=as_of)
        if db_products:
            return db_products

    base = _read_json(base_path)["products"]
    additions_payload = _read_json(additions_path)
    additions = additions_payload.get("products", [])
    catalog_as_of = _catalog_as_of(additions_payload, as_of)
    products = [dict(product) for product in (*base, *additions)]

    codes = [product.get("code") for product in products]
    invalid = [code for code in codes if not isinstance(code, str) or not _CODE_RE.fullmatch(code)]
    if invalid:
        raise ValueError(f"Invalid product codes: {invalid}")
    if len(codes) != len(set(codes)):
        duplicates = sorted({code for code in codes if codes.count(code) > 1})
        raise ValueError(f"Duplicate product codes: {duplicates}")

    own_codes = {product["code"] for product in products if product.get("ownership") == "own"}
    dangling = {
        product["code"]: product.get("ownCode")
        for product in products
        if product.get("ownership") == "peer" and product.get("ownCode") not in own_codes
    }
    if dangling:
        raise ValueError(f"Peer products reference missing own products: {dangling}")

    invalid_ownership = [
        product["code"] for product in products if product.get("ownership") not in {"own", "peer"}
    ]
    if invalid_ownership:
        raise ValueError(f"Invalid product ownership: {invalid_ownership}")

    # w / power 是既有 /meta 产品契约字段；Excel 增量没有这两个演示权重，
    # 因此给中性值，避免把内部来源字段暴露给前端。
    for product in products:
        if "sectorName" not in product:
            product["sectorName"] = _SECTOR_NAMES[product["sector"]]
        product.setdefault("south", False)
        product.setdefault("w", 1)
        product.setdefault("power", 1)
        product["isNew"] = _is_new_listing(product.get("listingDate"), catalog_as_of)
        if product["ownership"] == "own":
            product.pop("ownCode", None)
    # 契约顺序是自家在前、同业在后；组内保留原主数据／增量的人工维护顺序。
    return sorted(products, key=lambda product: 0 if product["ownership"] == "own" else 1)


def _load_database_products(engine, *, as_of=None):
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                select(product_catalog_entries)
                .where(product_catalog_entries.c.status == ACTIVE_STATUS)
                .order_by(product_catalog_entries.c.ownership, product_catalog_entries.c.code)
            ).mappings().all()
            if as_of is None:
                latest = conn.execute(select(func.max(product_catalog_snapshots.c.fetched_at))).scalar()
                as_of = latest.date() if latest is not None else None
    except SQLAlchemyError:
        return []
    if not rows:
        return []
    catalog_as_of = _coerce_date(as_of, field="catalog as_of") if as_of is not None else max(
        row["last_seen_at"].date() for row in rows
    )
    products = []
    for row in rows:
        product = {
            "code": row["code"], "name": row["name"], "issuer": row["issuer"],
            "ownership": row["ownership"], "sector": row["sector"],
            "sectorName": row["sector_name"], "struct": row["struct"],
            "listingDate": row["listing_date"].isoformat(), "south": bool(row["southbound"]),
            "benchmark": row["benchmark"], "securityType": row["security_type"],
            "productSubcategory": row["product_subcategory"],
            "managementStyle": row["management_style"], "replicationMethod": row["replication_method"],
            "managementFee": row["management_fee"], "assetClass": row["asset_class"],
            "geographicFocus": row["geographic_focus"],
            "currencies": json.loads(row["currencies_json"] or "[]"),
            "aliases": json.loads(row["aliases_json"] or "[]"),
            "w": 1, "power": 1,
        }
        if row["ownership"] == "peer" and row["own_code"]:
            product["ownCode"] = row["own_code"]
        product["isNew"] = _is_new_listing(row["listing_date"], catalog_as_of)
        products.append(product)
    return sorted(products, key=lambda product: (0 if product["ownership"] == "own" else 1, product["code"]))


def _entry_values(product, now, *, status=ACTIVE_STATUS, snapshot_id=None, match_reason="initial_catalog"):
    return {
        "code": product["code"], "name": product["name"], "issuer": product["issuer"],
        "ownership": product["ownership"], "own_code": product.get("ownCode"),
        "sector": product.get("sector") or "hk",
        "sector_name": product.get("sectorName") or _SECTOR_NAMES.get(product.get("sector"), "其他"),
        "struct": product.get("struct") or "ETF", "listing_date": _coerce_date(product["listingDate"], field="listingDate"),
        "southbound": bool(product.get("south")), "benchmark": product.get("benchmark"),
        "security_type": product.get("securityType"),
        "product_subcategory": product.get("productSubcategory"),
        "management_style": product.get("managementStyle"),
        "replication_method": product.get("replicationMethod"),
        "management_fee": product.get("managementFee"), "asset_class": product.get("assetClass"),
        "geographic_focus": product.get("geographicFocus"),
        "currencies_json": json.dumps(product.get("currencies") or [], ensure_ascii=False),
        "aliases_json": json.dumps(product.get("aliases") or [], ensure_ascii=False),
        "status": status, "match_reason": match_reason, "source_snapshot_id": snapshot_id,
        "source_json": json.dumps(product.get("source") or {}, ensure_ascii=False),
        "first_seen_at": now, "last_seen_at": now,
    }


def seed_catalog(engine, *, now=None):
    """Seed the reviewed 135-product baseline once; never overwrite review state."""
    now = now or clock_now()
    with engine.begin() as conn:
        existing = conn.execute(select(func.count()).select_from(product_catalog_entries)).scalar_one()
        if existing:
            return 0
        products = load_products()
        for product in products:
            conn.execute(insert(product_catalog_entries).values(**_entry_values(product, now)))
        _put_meta(conn, "product_catalog_policy_version", CATALOG_POLICY_VERSION)
        _put_meta(conn, "product_catalog_revision", catalog_digest(products))
        return len(products)


def catalog_digest(products):
    """Digest every public catalog field, excluding volatile sync metadata."""
    def value(product, camel, snake=None):
        snake = snake or camel
        return product.get(camel) if camel in product else product.get(snake)

    def json_value(product, camel, snake):
        raw = value(product, camel, snake)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                pass
        return raw or []

    normalized = []
    for product in products:
        listing = value(product, "listingDate", "listing_date")
        normalized.append({
            "code": product["code"], "name": product.get("name"), "issuer": product.get("issuer"),
            "ownership": product.get("ownership"), "ownCode": value(product, "ownCode", "own_code"),
            "sector": product.get("sector"), "sectorName": value(product, "sectorName", "sector_name"),
            "struct": product.get("struct"),
            "listingDate": listing.isoformat() if hasattr(listing, "isoformat") else listing,
            "south": bool(value(product, "south", "southbound")),
            "benchmark": product.get("benchmark"),
            "securityType": value(product, "securityType", "security_type"),
            "productSubcategory": value(product, "productSubcategory", "product_subcategory"),
            "managementStyle": value(product, "managementStyle", "management_style"),
            "replicationMethod": value(product, "replicationMethod", "replication_method"),
            "managementFee": value(product, "managementFee", "management_fee"),
            "assetClass": value(product, "assetClass", "asset_class"),
            "geographicFocus": value(product, "geographicFocus", "geographic_focus"),
            "currencies": sorted(json_value(product, "currencies", "currencies_json")),
            "aliases": sorted(json_value(product, "aliases", "aliases_json")),
        })
    payload = json.dumps(sorted(normalized, key=lambda row: row["code"]), ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _put_meta(conn, key, value):
    if not conn.execute(update(meta_kv).where(meta_kv.c.k == key).values(v=str(value))).rowcount:
        conn.execute(insert(meta_kv).values(k=key, v=str(value)))


def catalog_status(engine):
    try:
        with engine.connect() as conn:
            grouped = {status: int(count) for status, count in conn.execute(
                select(product_catalog_entries.c.status, func.count())
                .group_by(product_catalog_entries.c.status)
            )}
            latest = conn.execute(
                select(product_catalog_snapshots).order_by(product_catalog_snapshots.c.fetched_at.desc()).limit(1)
            ).mappings().first()
            candidates = [dict(row) for row in conn.execute(
                select(product_catalog_entries).where(
                    product_catalog_entries.c.status.in_((PENDING_SOURCE_STATUS, PENDING_REVIEW_STATUS, "failed"))
                ).order_by(product_catalog_entries.c.status, product_catalog_entries.c.code)
            ).mappings()]
            own_codes = [row[0] for row in conn.execute(
                select(product_catalog_entries.c.code).where(
                    product_catalog_entries.c.status == ACTIVE_STATUS,
                    product_catalog_entries.c.ownership == "own",
                ).order_by(product_catalog_entries.c.code)
            )]
    except SQLAlchemyError:
        return {"available": False, "counts": {}, "latestSyncAt": None, "candidates": []}
    for row in candidates:
        row["listing_date"] = row["listing_date"].isoformat()
        row["first_seen_at"] = row["first_seen_at"].isoformat()
        row["last_seen_at"] = row["last_seen_at"].isoformat()
        if row.get("reviewed_at"):
            row["reviewed_at"] = row["reviewed_at"].isoformat()
        try:
            source = json.loads(row.pop("source_json", "{}") or "{}")
        except (TypeError, ValueError):
            source = {}
        row["suggestedOwnCodes"] = source.get("catalogMatchCandidates") or (
            own_codes if row.get("ownership") == "peer" and row.get("status") == PENDING_REVIEW_STATUS else []
        )
    counts = {key: grouped.get(key, 0) for key in (
        ACTIVE_STATUS, PENDING_SOURCE_STATUS, PENDING_REVIEW_STATUS, "failed", "rejected"
    )}
    if latest and latest["status"] == "failed":
        counts["failed"] = max(1, counts["failed"])
    return {
        "available": True,
        "policyVersion": CATALOG_POLICY_VERSION,
        "latestSyncAt": latest["fetched_at"].isoformat() if latest else None,
        "latestStatus": latest["status"] if latest else None,
        "latestError": latest["error"] if latest else None,
        "counts": counts,
        "candidates": candidates,
    }


def review_candidate(engine, code, decision, *, own_code=None, reviewer="admin", note=None, now=None):
    if decision not in {"approve", "reject"}:
        raise ValueError("decision must be approve or reject")
    now = now or clock_now()
    with engine.begin() as conn:
        row = conn.execute(select(product_catalog_entries).where(
            product_catalog_entries.c.code == code,
            product_catalog_entries.c.status == PENDING_REVIEW_STATUS,
        )).mappings().first()
        if row is None:
            raise KeyError(code)
        selected_own_code = row["own_code"]
        if decision == "approve" and row["ownership"] == "peer":
            selected_own_code = str(own_code or selected_own_code or "").strip()
            if not selected_own_code:
                raise ValueError("approving a peer requires ownCode")
            mapped_own = conn.execute(select(product_catalog_entries.c.code).where(
                product_catalog_entries.c.code == selected_own_code,
                product_catalog_entries.c.ownership == "own",
                product_catalog_entries.c.status == ACTIVE_STATUS,
            )).scalar_one_or_none()
            if mapped_own is None:
                raise ValueError("ownCode must identify an active CSOP product")
        status = PENDING_SOURCE_STATUS if decision == "approve" else "rejected"
        conn.execute(update(product_catalog_entries).where(product_catalog_entries.c.code == code).values(
            status=status, own_code=selected_own_code,
            reviewed_at=now, reviewed_by=reviewer, review_note=note,
        ))
    return {"code": code, "status": status, "ownCode": selected_own_code}


def _kol_replacements(updates_path=KOL_ROSTER_UPDATES):
    payload = _read_json(updates_path)
    replacements = payload.get("replacements", [])
    by_alias = {}
    aliases_by_name = {}
    for row in replacements:
        name = str(row.get("name") or "").strip()
        aliases = tuple(str(value).strip() for value in row.get("aliases", []) if str(value).strip())
        if not name or not aliases:
            raise ValueError("KOL roster replacements require a current name and aliases")
        if name in by_alias or any(alias in by_alias for alias in aliases):
            raise ValueError(f"Duplicate KOL roster alias for {name!r}")
        aliases_by_name[name] = (name, *aliases)
        for alias in aliases:
            by_alias[alias] = name
    return by_alias, aliases_by_name


def load_accounts(base_path=DEMO_MASTER, updates_path=KOL_ROSTER_UPDATES):
    """返回当前官号／KOL 名单；KOL 改名不丢失源库里的历史作者名。"""
    master = _read_json(base_path)
    by_alias, _aliases_by_name = _kol_replacements(updates_path)
    kols = [
        {**row, "name": by_alias.get(row["name"], row["name"])}
        for row in master["kols"]
    ]
    names = [row["name"] for row in kols]
    if len(names) != len(set(names)):
        raise ValueError("KOL roster update produced duplicate current names")
    updates = _read_json(updates_path)
    if "activeRoster" in updates:
        raw_active = updates["activeRoster"]
        if not isinstance(raw_active, list):
            raise ValueError("KOL active roster must be a list")
        active_names = [str(value).strip() for value in raw_active]
        if any(not name for name in active_names):
            raise ValueError("KOL active roster contains a blank name")
        if len(active_names) != len(set(active_names)):
            raise ValueError("KOL active roster contains duplicate names")
        unknown = set(active_names) - set(names)
        if unknown:
            raise ValueError(
                "KOL active roster contains unknown names: " + ", ".join(sorted(unknown))
            )
        active = set(active_names)
        kols = [{**row, "active": row["name"] in active} for row in kols]
    return {"officials": master["officials"], "kols": kols}


def load_kol_aliases(base_path=DEMO_MASTER, updates_path=KOL_ROSTER_UPDATES):
    """按当前 KOL 名返回可在 MarketInsight ``author_name`` 中出现的所有写法。"""
    accounts = load_accounts(base_path=base_path, updates_path=updates_path)
    _by_alias, aliases_by_name = _kol_replacements(updates_path)
    return {
        row["name"]: aliases_by_name.get(row["name"], (row["name"],))
        for row in accounts["kols"]
    }


def load_active_kol_source_names(base_path=DEMO_MASTER, updates_path=KOL_ROSTER_UPDATES):
    """Return every source author spelling belonging to the active KOL roster."""
    accounts = load_accounts(base_path=base_path, updates_path=updates_path)
    aliases = load_kol_aliases(base_path=base_path, updates_path=updates_path)
    return list(dict.fromkeys(
        source_name
        for kol in accounts["kols"]
        if kol.get("active")
        for source_name in aliases[kol["name"]]
    ))


def load_kol_user_ids(base_path=DEMO_MASTER, updates_path=KOL_ROSTER_UPDATES):
    """Return stable Futu user IDs by canonical KOL name.

    Nicknames remain useful for historical rows whose source UID was absent,
    while IDs keep future posts attached after another display-name change.
    """
    accounts = load_accounts(base_path=base_path, updates_path=updates_path)
    known_names = {row["name"] for row in accounts["kols"]}
    payload = _read_json(updates_path)
    raw = payload.get("userIds", {})
    if not isinstance(raw, dict):
        raise ValueError("KOL userIds must be an object")
    unknown = set(raw) - known_names
    if unknown:
        raise ValueError("KOL userIds contain unknown names: " + ", ".join(sorted(unknown)))
    result = {}
    owner_by_uid = {}
    for name in known_names:
        values = raw.get(name, [])
        if not isinstance(values, list):
            raise ValueError(f"KOL userIds for {name!r} must be a list")
        ids = tuple(dict.fromkeys(str(value).strip() for value in values))
        if any(not value or not value.isdigit() for value in ids):
            raise ValueError(f"KOL userIds for {name!r} must contain numeric IDs")
        for user_id in ids:
            prior = owner_by_uid.setdefault(user_id, name)
            if prior != name:
                raise ValueError(f"KOL user ID {user_id!r} belongs to multiple names")
        result[name] = ids
    return result


def load_active_kol_user_ids(base_path=DEMO_MASTER, updates_path=KOL_ROSTER_UPDATES):
    """Return stable user IDs for only the active KOL roster."""
    accounts = load_accounts(base_path=base_path, updates_path=updates_path)
    user_ids = load_kol_user_ids(base_path=base_path, updates_path=updates_path)
    return list(dict.fromkeys(
        user_id
        for kol in accounts["kols"]
        if kol.get("active")
        for user_id in user_ids[kol["name"]]
    ))


def load_pending_products(additions_path=ADDITIONS, *, as_of=None):
    """返回尚无可抓取代码的候选产品，仅供人工跟进。"""
    additions = _read_json(additions_path)
    catalog_as_of = _catalog_as_of(additions, as_of)
    pending = [dict(product) for product in additions.get("pendingProducts", [])]
    for product in pending:
        product["isNew"] = _is_new_listing(product.get("listingDate"), catalog_as_of)
    return pending

