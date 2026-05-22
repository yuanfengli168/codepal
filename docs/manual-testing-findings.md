# Manual test findings & follow-ups

Findings logged from manual end-to-end runs of [manual-testing.md](manual-testing.md).
Each entry is actionable; none currently block the documented procedure.

Add a new dated section at the top whenever you re-run the suite.

---

## 2026-05-22 — first full pass after PR #1 + PR #2

**Environment**

- macOS arm64, Python 3.13 in `.venv`
- Ollama 11434 live; `nomic-embed-text` + `qwen3:14b` pulled
- Fresh state (`rm -rf ~/.codepal/chroma ~/.codepal/index_state.db`)
- Fixture: [examples/buggy_repo/](../examples/buggy_repo/)

**Procedure outcome** — every section of `manual-testing.md` (§1 through §9) passed as written. Detailed table below.

| § | Step | Result |
|---|---|---|
| 1 / 1a | `codepal serve` + `/v1/status` | ✅ `ollama_available=true, chroma_available=true` |
| 2 | `POST /v1/index` (initial) | ✅ `indexed=4, skipped=0, errors=[]` |
| 2 | `POST /v1/index` (re-run) | ✅ `indexed=0, skipped=4` (hash cache works) |
| 2a | `GET /v1/search` × 3 | ✅ correct top-1 for all three queries |
| 3 | `POST /v1/bugs` × 4 | ✅ all `201` with UUIDs |
| 3a | `GET /v1/bugs/search?q=NoneType not iterable` | ✅ Bug #4 top |
| 4 | `POST /v1/query` Path A × 3 | ✅ `source=bug_db`, scores 0.882 / 0.886 / 0.899 |
| 5a | `POST /v1/query` Path B (mutable default Q) | ✅ `source=local_llm`, 4 RAG chunks, qwen3 returned correct explanation |
| 5b | `POST /v1/query` Path C (no api_key) | ✅ `HTTP 503` with documented detail string |
| 6 | `pytest tests/unit/test_mcp.py` | ✅ 2 passed |
| 9 | `pytest tests/unit` | ✅ 99 passed |
| 9 | `pytest tests/integration -m "not slow"` | ✅ 3 passed |

---

### Findings

#### F1 — tree-sitter grammar version mismatch (chunker degraded)

- **Symptom (server log on first index):**
  `Failed to load tree-sitter grammar for python: Incompatible Language version 15. Must be between 13 and 14`
- **Impact:** Indexing still succeeds because `chunker.py` falls back to a single whole-file chunk per file. Consequence: `SearchResult.symbol_name` is the **filename** (e.g. `inventory.py`) instead of the function name (`get_page`). Search relevance is therefore coarser than designed, and the function-level metadata advertised in `docs/design.md` is not actually populated.
- **Likely root cause:** the bundled `tree-sitter` runtime is at v0.21+ (ABI 15) while the language pack we pin still emits ABI 14 — or vice-versa.
- **Action:** repin `tree-sitter` and `tree-sitter-languages` (or migrate to `tree-sitter-language-pack`) to a compatible matrix; add a startup assertion that grammars load, instead of swallowing the failure.
- **Priority:** medium — silent quality regression.

#### F2 — Two scoring conventions for bug-DB hits

- **Symptom:** the same `(query, bug)` pair was scored:
  - `0.628` by `GET /v1/bugs/search`
  - `0.882` by the dispatcher inside `POST /v1/query` (which then crossed the 0.85 `dispatcher.bug_score_threshold`)
- **Impact:** the default threshold is calibrated for the dispatcher's formula. A user who tunes the threshold based on what `/v1/bugs/search` returns will be off. Also confusing for anyone reading both responses.
- **Likely root cause:** one path returns `1 - cosine_distance` while the other normalises differently (e.g. `1 - distance/2`). Need to grep `dispatcher.py` and `bugs/store.py`.
- **Action:** pick one formula, apply it in both call sites, and document it in `docs/design.md` and `config.toml.example`.
- **Priority:** medium — semantic correctness of the public API.

#### F3 — Chroma telemetry noise

- **Symptom:** every collection op logs `Failed to send telemetry event … capture() takes 1 positional argument but 3 were given`.
- **Impact:** cosmetic — pollutes structured logs, makes real errors harder to spot.
- **Action:** pass `Settings(anonymized_telemetry=False)` (or equivalent env var) when constructing the PersistentClient / EphemeralClient in `db/chroma.py`.
- **Priority:** low.

#### F4 — CLI `codepal search` uses stale response field names

- **Symptom:** `cli/main.py::search` formats results with `r["file"]`, `r["lines"]`, `r["symbol"]`, but `/v1/search` returns `file_path`, `start_line`/`end_line`, `symbol_name`. A `KeyError` would surface to the user.
- **Impact:** CLI cosmetic — the underlying API works; the JSON contract is what the tests validate.
- **Action:** update the CLI to read the correct fields (and add a unit test that calls into it).
- **Priority:** low.

#### F5 — Path C 503 message is slightly misleading when Ollama IS up

- **Symptom:** asking a general non-code question with `project_path=/tmp` returned 503 with `"Local LLM is unavailable and no external API key is configured."` — but Ollama was running fine. The dispatcher decided Path B was not viable (no RAG context) and Path C had no key, so it 503'd. The message reads as if Ollama itself was down.
- **Impact:** docs-level / UX confusion only — the gating is correct.
- **Action:** tweak the 503 detail to distinguish "Ollama unreachable" from "no local context found and no external key", or include the underlying reason in `metadata` on a 2xx response that says so.
- **Priority:** low.

---

### Not-findings (sanity-checked and behaving as designed)

- Hash-cache skip on re-index works (`indexed=0, skipped=4`).
- Structured logging emits single-line key=value entries with ISO timestamps from the lifespan.
- Ollama startup probe runs but does **not** crash the server when Ollama is down (verified separately during PR #1 development).
- Graceful shutdown closes embedder + chat client cleanly on Ctrl-C.

---

### 中文说明（F1 – F4）

#### F1 — tree-sitter 语法版本不匹配（中等优先级）

- **问题：** 服务启动后第一次索引时日志里出现 `Failed to load tree-sitter grammar for python: Incompatible Language version 15. Must be between 13 and 14`。`chunker.py` 捕获异常后静默退化为「整文件一个 chunk」，所以索引能继续，但 `SearchResult.symbol_name` 变成文件名（例如 `inventory.py`）而不是函数名（例如 `get_page`），搜索粒度变粗，`docs/design.md` 里宣传的「函数级 chunk」实际上没有生效。
- **根因：** `pyproject.toml` 把 `tree-sitter` 限制在 `>=0.22,<0.25`，但 `tree-sitter-python` 已经升级到 0.25，编译出来的语法 ABI=15，老的 runtime 只接受 13–14。
- **建议修复：** 把依赖矩阵升到一组兼容版本（`tree-sitter>=0.25` + 各语言包匹配），并在加载失败时输出更明显的告警（带 package 名 + 异常类型），而不是只打一行 warning 后悄悄降级。

#### F2 — 两套打分口径不一致（中等优先级）

- **问题：** 同一对 `(query, bug)`，`GET /v1/bugs/search` 给出 0.628，但 `POST /v1/query` 走 Path A 时同一条 bug 被算成 0.882，越过 0.85 阈值。两边读起来不一致，用户如果按 `/v1/bugs/search` 的分数去调阈值就会调错。
- **根因：** `bugs/store.py::BugStore.search` 与 `db/chroma.py::query_collection` 各自写了一遍 `score = max(0, 1 - distance)`，分散在两个文件里，未来很容易被一边改了另一边没改而再次发散。
- **建议修复：** 把转换函数抽到 `db/chroma.py::distance_to_score`，两个调用方都用它；在 `design.md` 和 `config.toml.example` 里写清楚「score = 1 - cosine_distance（取 0 下限）」这个口径。

#### F3 — Chroma 遥测报错刷屏（低优先级）

- **问题：** 每次 collection 操作日志里都会出现 `Failed to send telemetry event ... capture() takes 1 positional argument but 3 were given`，淹没真正有用的日志。
- **根因：** Chroma 1.x 默认开启匿名遥测，client 端打 hook 的代码和当前 posthog 版本签名对不上。
- **建议修复：** 构造 `PersistentClient` / `EphemeralClient` 时传入 `Settings(anonymized_telemetry=False)`，本地工具不需要任何遥测。

#### F4 — CLI `codepal search` 字段名过时（低优先级）

- **问题：** `cli/main.py::search` 还在按旧字段名读响应（`r['file']`、`r['lines']`、`r['symbol']`、`r['snippet']`），但 `/v1/search` 返回的实际字段是 `file_path` / `start_line` / `end_line` / `symbol_name` / `text`。一旦真有用户用 CLI 跑 search 就会直接 `KeyError`。
- **根因：** API schema 演进后 CLI 没有跟着改，单元测试也没覆盖到 CLI 的输出格式化路径。
- **建议修复：** 把 CLI 里的字段名同步成 `SearchResult` 的真实字段；后续给 `cli/main.py` 加一个 typer `CliRunner` 的最小用例。

