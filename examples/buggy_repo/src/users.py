"""User management.

KNOWN BUG #3: add_user uses a mutable default argument for `roles`, so role
state leaks across calls. See ../KNOWN_BUGS.md.
"""

from __future__ import annotations


def add_user(name: str, roles: list[str] = []) -> dict:  # BUG: mutable default
    """Create a new user record.

    Every call appends "guest" to the *same* list object, so the second call
    inherits the first call's roles.
    """
    roles.append("guest")
    return {"name": name, "roles": roles}


def is_admin(user: dict) -> bool:
    return "admin" in user.get("roles", [])
