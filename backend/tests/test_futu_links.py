import pytest

from core.futu_links import canonical_futu_feed_url, futu_feed_url


def test_feed_id_maps_to_the_public_futu_community_route():
    assert futu_feed_url(117149481304068) == (
        "https://q.futunn.com/feed/117149481304068?lang=zh-cn"
    )


@pytest.mark.parametrize("value", [None, "", 0, -1, True, "12x"])
def test_invalid_feed_ids_are_rejected(value):
    with pytest.raises(ValueError):
        futu_feed_url(value)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "https://www.futunn.com/post/117149481304068",
            "https://q.futunn.com/feed/117149481304068?lang=zh-cn",
        ),
        (
            "https://www.futunn.com/feed/117149481304068",
            "https://q.futunn.com/feed/117149481304068?lang=zh-cn",
        ),
        (
            "https://q.futunn.com/feed/117149481304068?lang=zh-hk",
            "https://q.futunn.com/feed/117149481304068?lang=zh-cn",
        ),
    ],
)
def test_legacy_futu_links_are_canonicalized(source, expected):
    assert canonical_futu_feed_url(source) == expected


def test_non_feed_urls_are_not_rewritten():
    value = "https://example.com/post/117149481304068"
    assert canonical_futu_feed_url(value) == value
