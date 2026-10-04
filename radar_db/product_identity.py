"""Shared deterministic helpers for unambiguous product-name evidence."""

from __future__ import annotations

from collections.abc import Mapping


def maximal_name_codes(text: str, names: Mapping[str, str]) -> set[str]:
    """Return codes for name occurrences not nested inside a longer match.

    Product masters commonly contain a generic full name inside an
    issuer-qualified one (for example ``港元货币市场ETF`` inside
    ``博时港元货币市场ETF``).  Counting both at the same character span creates
    a false multi-product attribution.  A genuinely separate occurrence of
    the shorter name is retained.

    ``text`` and every key in ``names`` must already use the caller's chosen
    Unicode/case/whitespace normalization.
    """

    occurrences: list[tuple[int, int, str]] = []
    for name, code in names.items():
        if not name:
            continue
        start = 0
        while True:
            start = text.find(name, start)
            if start < 0:
                break
            end = start + len(name)
            occurrences.append((start, end, code))
            start += 1

    return {
        code
        for start, end, code in occurrences
        if not any(
            outer_start <= start
            and end <= outer_end
            and (outer_end - outer_start) > (end - start)
            for outer_start, outer_end, _outer_code in occurrences
        )
    }
