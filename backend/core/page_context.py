"""Per-screen clocks shown in the shared business-page header."""

from providers import get_provider
from providers.sentinel import MISSING
from .range_spec import is_valid_range_key


SCREENS = frozenset({"sector", "product", "official", "kol", "kol_detail"})


def page_context(screen, range_key, *, code=None, account=None, kol=None):
    if screen not in SCREENS or not is_valid_range_key(range_key):
        return MISSING
    return get_provider().page_context(
        screen,
        range_key,
        code=code,
        account=account,
        kol=kol,
    )
