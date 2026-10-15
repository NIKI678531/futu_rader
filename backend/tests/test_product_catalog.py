"""经 Excel 核验的生产产品目录不会漏项、重复或产生悬空竞品映射。"""

from datetime import date, datetime

from radar_db.product_catalog import (
    load_accounts,
    load_kol_aliases,
    load_pending_products,
    load_products,
)


NEW_OWN_CODES = {"3447", "3121", "3537"}
NEW_PEER_MAP = {
    "3423": "3033",
    "3111": "3003",
    "3031": "3469",
    "2832": "3109",
    "2834": "3034",
    "3455": "3034",
    "3444": "3443",
    "3579": "3443",
    "3418": "3030",
    "2843": "2822",
    "3408": "3121",
    "3187": "3447",
}


def test_production_catalog_has_reviewed_counts_and_unique_codes():
    products = load_products()
    codes = [product["code"] for product in products]

    assert len(products) == 135
    assert len(codes) == len(set(codes))
    assert sum(product["ownership"] == "own" for product in products) == 64
    assert sum(product["ownership"] == "peer" for product in products) == 71
    assert [product["ownership"] for product in products] == ["own"] * 64 + ["peer"] * 71
    assert NEW_OWN_CODES <= set(codes)


def test_new_peer_mappings_point_to_own_products():
    products = load_products()
    by_code = {product["code"]: product for product in products}

    assert {code: by_code[code]["ownCode"] for code in NEW_PEER_MAP} == NEW_PEER_MAP
    assert all(by_code[own_code]["ownership"] == "own" for own_code in NEW_PEER_MAP.values())


def test_pending_product_without_counter_never_enters_code_pool():
    products = load_products()
    pending = load_pending_products()

    assert len(pending) == 1
    assert pending[0]["sourceCode"] == "-"
    assert pending[0]["code"] is None
    assert pending[0]["name"] == "南方东英 Solactive 亚洲 AI 瓶颈指数 ETF"
    assert all(product["code"] != "-" for product in products)
    assert pending[0]["name"] not in {product["name"] for product in products}


def test_new_listing_flags_are_recomputed_at_catalog_review_date():
    products = load_products()

    assert {product["code"] for product in products if product["isNew"]} == {"3408"}
    assert load_pending_products()[0]["isNew"] is True


def test_new_listing_flags_accept_an_explicit_as_of_date():
    before_listing = {product["code"]: product for product in load_products(as_of="2026-09-14")}
    at_boundary = {
        product["code"]: product
        for product in load_products(as_of=datetime(2026, 10, 15, 12, 30))
    }

    assert before_listing["3408"]["isNew"] is False
    assert at_boundary["3408"]["isNew"] is False
    assert load_pending_products(as_of=date(2026, 9, 16))[0]["isNew"] is False
    assert load_pending_products(as_of="2026-10-16")[0]["isNew"] is True
    assert load_pending_products(as_of="2026-10-17")[0]["isNew"] is False


def test_catalog_products_have_runtime_contract_fields():
    required = {
        "code",
        "name",
        "sector",
        "sectorName",
        "struct",
        "issuer",
        "ownership",
        "listingDate",
        "isNew",
        "south",
        "w",
        "power",
    }
    for product in load_products():
        assert required <= set(product), product["code"]
        if product["ownership"] == "own":
            assert "ownCode" not in product


def test_latest_kol_roster_uses_current_names_and_keeps_source_aliases():
    requested = {
        "股海中的G妹", "年頭旺到年尾", "Zona投資隨筆", "港美猎人", "財媽價值輪回",
        "价值投资叶荣添", "守猪逮兔估值模型", "圣者为王", "一颗财丸", "知常公子",
        "深夜学堂", "耀文港古", "潘驴邓晓闲缺一", "嘉哥爱投资", "杨杨得亿",
        "股市魔术师", "海梦5/不知名选手", "只做主升不做调整", "牛头法师", "逍遥游",
        "摩帥", "搏盡佢啦", "孫子的末代傳人", "中戶變大戶小牛變大牛", "丹尼斯王子",
        "站在Ju人肩上", "陈达美股投资", "六爷漫谈", "中环杰哥投资探秘",
    }
    names = {row["name"] for row in load_accounts()["kols"]}
    aliases = load_kol_aliases()

    assert requested <= names
    assert "嘉哥爱自由" not in names
    assert "不知名选手" not in names
    assert aliases["嘉哥爱投资"] == ("嘉哥爱投资", "嘉哥爱自由")
    assert aliases["海梦5/不知名选手"] == (
        "海梦5/不知名选手", "海梦5", "不知名选手",
    )

