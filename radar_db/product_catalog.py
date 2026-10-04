"""生产产品目录。

演示 fixture 继续保持设计验收时的 120 只产品；生产目录在其上合并经人工核验的
Excel 增量。代码为空或尚未分配的产品只进入 pending_products，绝不会进入抓取池。
"""

import json
import re
from datetime import date, datetime
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
# 仓库／worker 镜像保留 backend/ 前缀；backend 镜像把 backend/ 内容直接复制到 /app。
FIXTURES_ROOT = (
    PACKAGE_ROOT / "backend" / "fixtures"
    if (PACKAGE_ROOT / "backend" / "fixtures").is_dir()
    else PACKAGE_ROOT / "fixtures"
)
DEMO_MASTER = FIXTURES_ROOT / "demo" / "master.json"
ADDITIONS = FIXTURES_ROOT / "product_catalog_additions.json"
_CODE_RE = re.compile(r"^[1-9][0-9]{3}$")
_NEW_LISTING_DAYS = 30
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


def load_products(base_path=DEMO_MASTER, additions_path=ADDITIONS, *, as_of=None):
    """返回生产产品池，并按目录审阅日重算新品状态。

    ``as_of`` 可传 ISO 日期字符串、``date`` 或 ``datetime``；省略时使用增量
    文件的 ``provenance.reviewedAsOf``。上市当天起未满 30 个日历日才算新品。
    """
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


def load_accounts(base_path=DEMO_MASTER):
    """官号与 KOL 仍沿用演示主数据；本次 Excel 只维护产品。"""
    master = _read_json(base_path)
    return {"officials": master["officials"], "kols": master["kols"]}


def load_pending_products(additions_path=ADDITIONS, *, as_of=None):
    """返回尚无可抓取代码的候选产品，仅供人工跟进。"""
    additions = _read_json(additions_path)
    catalog_as_of = _catalog_as_of(additions, as_of)
    pending = [dict(product) for product in additions.get("pendingProducts", [])]
    for product in pending:
        product["isNew"] = _is_new_listing(product.get("listingDate"), catalog_as_of)
    return pending

