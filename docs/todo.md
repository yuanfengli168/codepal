# TODO — Design ↔ Implementation Drift

Captured 2026-05-22 while reviewing [design.md](design.md) against the current
implementation (post PR #1 + PR #2 + PR #3). Each row says what `design.md`
specifies vs what the code actually does and what we should do about it.

Legend
- ✅  matches design
- ⚠️  partially matches (works but with caveats)
- ❌  drifted; either code or design needs to change

---

## High-level architecture

| Area | design.md says | Implementation | Status | Action |
|---|---|---|---|---|
| 3-scenario dispatcher (bug DB → local LLM → external LLM) | §2 Scenarios A/B/C with fallback chain | `llm/dispatcher.py::QueryDispatcher.dispatch` implements all three paths in order | ✅ | — |
| Single FastAPI process exposing REST + MCP | §3 "FastAPI + fastmcp mounted at `/mcp`" | `api/app.py` mounts `mcp_server.py` at `/mcp` | ✅ | — |
| Ollama (`nomic-embed-text` + `qwen3:14b`) | §4 Tech stack | `embeddings/ollama.py`, `llm/ollama.py`, defaults in `config.py` | ✅ | — |
| ChromaDB local persistence | §4 Tech stack | `db/chroma.py` uses `PersistentClient` | ✅ | — |
| `/v1/status` health endpoint | §5 API surface | `api/routes/status.py` | ✅ | — |
| MCP tools (6 listed) | §6 MCP tool catalog | `mcp_server.py` exposes all 6 | ✅ | — |
| git `post-commit` hook auto-reindex | §7 Hooks | `hooks/installer.py` + `scripts/post-commit.sh` exist; not yet exercised in manual e2e | ⚠️ | Add a manual-testing.md §10 to install hook in `examples/buggy_repo` and verify reindex on commit |
| CLI (`serve / index / search / hooks install`) | §8 CLI | All commands present; `search` had broken field names (now fixed under F4) | ⚠️ | Add a typer `CliRunner` unit test for `search` so this never regresses |
| Function-level chunking via tree-sitter | §3 Indexing pipeline | Silent ABI mismatch → whole-file fallback (F1) | ❌ | Pin compatible tree-sitter matrix; surface load failures (in progress on this branch) |

## REST API contract drift (design.md §5 vs `api/models.py`)

All rows below were resolved on branch `docs-align-design-with-impl` by
rewriting design.md §5 to match the actual Pydantic models. The
`stack_trace` / `language` / `tags` / `error_pattern` / `duration_ms`
fields are now listed under §5 "Roadmap fields (not yet implemented)" so
future contributors don't reintroduce them silently.

| Endpoint | Field design.md uses | Field code uses | Status | Action |
|---|---|---|---|---|
| `POST /v1/query` request | `stack_trace`, `language` | not present | ✅ | Removed from design.md; logged under §5 Roadmap |
| `POST /v1/query` response | `context_chunks_used`, `external_tokens_used` | `context_chunks`, `metadata` | ✅ | design.md §5 now documents `context_chunks` + `metadata` |
| `POST /v1/index` request | `project_path`, `changed_files` | `path`, `files` | ✅ | design.md §5 now uses `path` / `files` / `project_slug` |
| `POST /v1/index` response | `chunks_added`, `duration_ms` | `indexed`, `skipped`, `errors` | ✅ | design.md §5 now documents `indexed` / `skipped` / `errors`; `duration_ms` parked in Roadmap |
| `GET /v1/search` result rows | `file`, `function`, `snippet` | `file_path`, `symbol_name`, `text`, `start_line`, `end_line` | ✅ | design.md §5 now matches `SearchResult` exactly |
| `POST /v1/bugs` request | `error_pattern`, `tags` | `error`, `solution`, `context` | ✅ | design.md §5 now uses `error` / `context` / `solution`; `tags` parked in Roadmap |
| Score convention | not specified | `score = max(0, 1 - cosine_distance)` | ✅ | Documented at the top of design.md §5 with a pointer to `distance_to_score` |

## Operational / quality issues

| Item | Status | Action |
|---|---|---|
| F1 — tree-sitter ABI mismatch silently degrades chunking | ❌ | Fix on `fix-findings-f1-f4` branch (pin `tree-sitter>=0.25`, language packs to matching ABI, louder warning) |
| F2 — two duplicate `1 - distance` snippets risk future drift | ⚠️ | Extract `distance_to_score` helper, route both `bugs/store.py` and `query_collection` through it (this branch) |
| F3 — Chroma telemetry log spam | ❌ | Pass `Settings(anonymized_telemetry=False)` to both `PersistentClient` and `EphemeralClient` (this branch) |
| F4 — CLI `search` reads stale field names | ❌ | Update to `file_path` / `start_line` / `end_line` / `symbol_name` / `text` (this branch) |
| F5 — Path C 503 message is misleading when Ollama is up | ⚠️ | Refine 503 detail to say "no local RAG context AND no external API key" instead of implying Ollama is down |
| Pydantic v2 deprecation warning from chromadb 2.11 | ⚠️ | Track upstream; suppress in test config if it gets noisy |

## Recommended next steps

1. ✅ Land the F1–F4 fix branch (`fix-findings-f1-f4`) so the chunker, scoring helper, telemetry, and CLI are all aligned with `design.md`'s spirit.
2. ✅ Open a separate documentation-only PR rewriting `design.md` §5 (REST schemas) to match what the code actually returns — done on branch `docs-align-design-with-impl`.
3. After (1) and (2) land on `main`, re-run `docs/manual-testing.md` end-to-end and verify `SearchResult.symbol_name` finally returns function names (the F1 acceptance criterion).
4. ✅ Add a typer `CliRunner` unit test for `codepal search` so the F4 regression cannot recur — covered by `tests/unit/test_findings_regressions.py::test_f4_cli_search_reads_current_field_names`.
5. ✅ Decide the fate of `stack_trace` / `language` / `tags` / `error_pattern`: removed from the active contract; tracked under design.md §5 "Roadmap fields (not yet implemented)".
6. Add §10 to `docs/manual-testing.md` to install the git `post-commit` hook in `examples/buggy_repo` and verify auto-reindex on commit (still ⚠️ in the architecture table above).
7. Refine F5 — make the Path C 503 message say "no local RAG context AND no external API key" instead of implying Ollama is down.
