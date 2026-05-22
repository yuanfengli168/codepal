"""Inventory pagination helpers.

KNOWN BUG #1: get_page has an off-by-one slice that over-reads the last page.
See ../KNOWN_BUGS.md.
"""

from __future__ import annotations


def get_page(items: list[str], page: int, page_size: int) -> list[str]:
    """Return a single page of items.

    Contract: page is 1-indexed; page_size > 0. The slice should be half-open
    [start, end). The +1 below is the bug — it over-reads on the last page.
    """
    start = (page - 1) * page_size
    end = start + page_size
    return items[start:end + 1]  # BUG: off-by-one — should be items[start:end]


def page_count(items: list[str], page_size: int) -> int:
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    return (len(items) + page_size - 1) // page_size
