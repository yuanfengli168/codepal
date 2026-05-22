"""Payload parsing helpers.

KNOWN BUG #4: extract_tags iterates payload.get("tags") which is None when the
key is missing, raising TypeError: 'NoneType' object is not iterable.
See ../KNOWN_BUGS.md.
"""

from __future__ import annotations


def extract_tags(payload: dict) -> list[str]:
    """Return the list of tag strings on the payload.

    BUG: payload.get("tags") returns None when the key is absent, and None is
    not iterable.
    """
    result: list[str] = []
    for tag in payload.get("tags"):  # BUG
        result.append(str(tag).strip().lower())
    return result


def has_tag(payload: dict, name: str) -> bool:
    return name in extract_tags(payload)
