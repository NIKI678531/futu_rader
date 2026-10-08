"""d0 is a first-class range even when the current-day facts are unavailable."""

from urllib.parse import quote


def test_demo_today_range_is_known_across_every_range_scoped_business_endpoint(client):
    code = "3033"
    kol = quote("孫子的末代傳人")
    official = quote("恒生投资")
    urls = [
        "/api/v1/pool?range=d0",
        "/api/v1/ranks?range=d0",
        f"/api/v1/products/{code}/benchmark?range=d0",
        "/api/v1/hot-summaries?range=d0",
        f"/api/v1/products/{code}/summary?range=d0",
        f"/api/v1/products/{code}/market-outlook?range=d0",
        f"/api/v1/products/{code}/themes?range=d0",
        f"/api/v1/products/{code}/negative-categories?range=d0",
        f"/api/v1/products/{code}/competitors?range=d0",
        f"/api/v1/products/{code}/compliance?range=d0",
        f"/api/v1/products/{code}/issuer-opinions?range=d0",
        f"/api/v1/products/{code}/topics?range=d0",
        f"/api/v1/products/{code}/kol-mentions?range=d0",
        f"/api/v1/products/{code}/evidence?ctx=d0%7Csum&polarity=neutral&n=6",
        f"/api/v1/products/{code}/candles?range=d0",
        f"/api/v1/products/{code}/heat-series?range=d0",
        f"/api/v1/products/{code}/stages?range=d0",
        "/api/v1/kol/impact?range=d0",
        f"/api/v1/kol/{kol}/opinions?range=d0",
        "/api/v1/officials/posts?range=d0",
        f"/api/v1/officials/{official}/etf-mentions?range=d0",
    ]

    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, url
        assert response.get_json()["status"] in {"ok", "unavailable", "empty", "na"}, url


def test_d0_does_not_turn_an_unknown_product_into_unavailable(client):
    response = client.get("/api/v1/products/0000/summary?range=d0")
    assert response.status_code == 404
