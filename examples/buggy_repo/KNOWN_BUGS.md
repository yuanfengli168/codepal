# Known bugs in this fixture

Each entry below maps 1:1 to a file under `src/`. The **Error**, **Solution**,
and **Context** fields are sized to be pasted directly into
`POST /v1/bugs` so you can seed CodePal's bug DB and then query it back.

---

## Bug #1 — Off-by-one in pagination slice

- **File:** `src/inventory.py`
- **Symbol:** `get_page`
- **Error:** `IndexError: list index out of range when paginating items`
- **Context:** `return items[start:end + 1]  # last page over-reads`
- **Solution:** Use a half-open slice `items[start:end]`; `end` is already exclusive in our pagination contract. The `+ 1` made the last page read past the list.

## Bug #2 — Unguarded division by zero

- **File:** `src/stats.py`
- **Symbol:** `average`
- **Error:** `ZeroDivisionError: division by zero in average() when input list is empty`
- **Context:** `return sum(values) / len(values)`
- **Solution:** Return `0.0` (or raise a domain-specific `EmptyDatasetError`) when `len(values) == 0` before dividing.

## Bug #3 — Mutable default argument

- **File:** `src/users.py`
- **Symbol:** `add_user`
- **Error:** `Stale shared state across calls: roles from a previous call appear in a fresh user`
- **Context:** `def add_user(name, roles=[]): roles.append("guest"); ...`
- **Solution:** Replace the mutable default with `roles=None`, then `roles = list(roles) if roles else []` inside the body. Mutable defaults are evaluated once at function-definition time and shared across calls.

## Bug #4 — TypeError: NoneType not iterable

- **File:** `src/parser.py`
- **Symbol:** `extract_tags`
- **Error:** `TypeError: 'NoneType' object is not iterable when extracting tags`
- **Context:** `for tag in payload.get("tags"): ...  # payload may omit 'tags'`
- **Solution:** `dict.get("tags")` returns `None` when the key is absent. Use `payload.get("tags") or []` (or `payload.get("tags", [])` only if you know the value is never explicitly `None`).
