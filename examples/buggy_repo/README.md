# buggy_repo — CodePal manual-test fixture

A tiny Python project with **four intentional, well-documented bugs**.
Used by [docs/manual-testing.md](../../docs/manual-testing.md) to exercise
CodePal's index → search → bug-DB → query flow end-to-end.

See [KNOWN_BUGS.md](KNOWN_BUGS.md) for the catalogue (error message,
location, root cause, fix).

This project is **not installed as part of CodePal** — it is purely a
test fixture. Do not import from it.

## Layout

```
src/
  inventory.py   # bug #1: off-by-one in slice
  stats.py       # bug #2: division by zero
  users.py       # bug #3: mutable default argument
  parser.py      # bug #4: TypeError: NoneType not iterable
```
