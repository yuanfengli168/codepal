"""Basic statistics helpers.

KNOWN BUG #2: average() does not guard against an empty input list and will
raise ZeroDivisionError. See ../KNOWN_BUGS.md.
"""

from __future__ import annotations


def average(values: list[float]) -> float:
    """Return the arithmetic mean of values.

    BUG: crashes with ZeroDivisionError when values is empty.
    """
    return sum(values) / len(values)


def variance(values: list[float]) -> float:
    mu = average(values)
    return sum((v - mu) ** 2 for v in values) / len(values)
