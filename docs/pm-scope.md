# CodePal — PM Scope & Acceptance Criteria

> **Version:** 0.1 (MVP)
> **Status:** Draft
> **Last Updated:** 2026-05-21
> **Author:** PM Agent

---

## 1. MVP Scope Table

| Feature / Capability | IN MVP | OUT of MVP | Notes |
|---|:---:|:---:|---|
| `POST /v1/query` REST endpoint | ✅ | | Core routing entry point |
| `POST /v1/index` REST endpoint | ✅ | | Trigger indexing manually or via hook |
| `GET /v1/search` REST endpoint | ✅ | | Semantic code search / debugging |
| `POST /v1/bugs` REST endpoint | ✅ | | Save bug solutions to local repo |
| `GET /v1/bugs/search` REST endpoint | ✅ | | Look up saved bug solutions |
| `GET /v1/status` REST endpoint | ✅ | | Health check + stats |
| MCP server (`/mcp`) with 6 mirrored tools | ✅ | | Copilot Agent integration via `.vscode/mcp.json` |
| Scenario 1 — Bug DB hit (zero LLM cost) | ✅ | | Exact/fuzzy match from ChromaDB bug repo |
| Scenario 2 — Local LLM answers (Ollama qwen3:14b) | ✅ | | Retrieval-augmented local inference |
| Scenario 3 — Smart proxy to external API | ✅ | | Minimal context payload forwarded to Copilot/Claude |
| tree-sitter function-level code chunking | ✅ | | Python as initial target language minimum |
| nomic-embed-text embeddings via Ollama | ✅ | | Chunk embedding for vector search |
| ChromaDB local vector store | ✅ | | Embedded, no separate server required |
| Git post-commit hook auto-indexing | ✅ | | `codepal hooks install` setup command |
| Initial full-project index (REST trigger) | ✅ | | `POST /v1/index` with `project_path` |
| Response `source` field in query response | ✅ | | Reports `local_bug_db`, `local_llm`, or `external_api` |
| `external_tokens_used` metric in response | ✅ | | Exposes token cost visibility per query |
| OpenClaw SKILL.md integration | ✅ | | Documents REST usage for OpenClaw agent |
| CLI indexing trigger (`codepal index`) | | ✅ | Post-MVP convenience tool |
| File watcher auto-indexing | | ✅ | Filesystem save triggers; post-MVP |
| Multi-project ChromaDB collections | | ✅ | MVP targets single active project |
| Explicit confidence scoring for escalation | | ✅ | Dynamic threshold; post-MVP |
| Per-query external API routing (Copilot vs Claude) | | ✅ | Escalation routing; post-MVP |
| Hosted / cloud-deployed version | | ✅ | Local macOS only for MVP |
| Open-source packaging & release | | ✅ | Post-MVP |
| Web UI / Dashboard | | ✅ | Post-MVP |
| REST/MCP authentication (API keys) | | ✅ | Post-MVP |
| Language pack expansion beyond initial targets | | ✅ | Additional tree-sitter parsers; post-MVP |

---

## 2. Acceptance Criteria

All 30 criteria are numbered and testable. Criteria are grouped by routing scenario then by supporting system areas.

### Scenario 1 — Bug DB Hit (Full Local, Zero API Cost)

**AC-01** — Given a query whose error message exactly matches a stored bug solution (cosine similarity ≥ 0.95), when `POST /v1/query` is called, then the response `source` field equals `"local_bug_db"` and `external_tokens_used` equals `0`.

**AC-02** — Given a query whose error message fuzzy-matches a stored bug solution (cosine similarity between 0.80 and 0.94), when `POST /v1/query` is called, then the response `source` field equals `"local_bug_db"` and the `answer` field contains the stored solution text.

**AC-03** — Given a Bug DB hit, when `POST /v1/query` responds, then response latency is under 500 ms (no LLM involved).

**AC-04** — Given a bug solution saved via `POST /v1/bugs` with `error_pattern`, `solution`, and `tags`, when `GET /v1/bugs/search?q=<same error>` is called, then the saved solution appears in the top-3 results with score ≥ 0.80.

**AC-05** — Given a query with no match in the Bug DB, when `POST /v1/query` is called, then `source` does **not** equal `"local_bug_db"` and routing proceeds to Scenario 2 or 3.

**AC-06** — Given a bug solution is saved with tags, when `GET /v1/bugs/search?q=<tag keyword>` is called, then results include solutions matching that tag.

**AC-07** — Given the Bug DB is empty, when `POST /v1/query` is called with a novel error, then the system does not return an error 500 and gracefully falls through to the next routing stage.

---

### Scenario 2 — Local LLM Handles It (Zero External API Cost)

**AC-08** — Given a project has been indexed and a query relates to code within that project, when the local LLM produces an answer with sufficient confidence, then `source` equals `"local_llm"` and `external_tokens_used` equals `0`.

**AC-09** — Given a query routed to local LLM, when `POST /v1/query` responds, then `context_chunks_used` is between 1 and 10 (relevant snippets were retrieved, not full codebase).

**AC-10** — Given a query routed to local LLM, when the response is returned, then the `answer` field is non-empty and contains at least one sentence of substantive content.

**AC-11** — Given Ollama is running with `qwen3:14b` loaded, when `GET /v1/status` is called, then the response includes an `ollama_connected: true` field (or equivalent).

**AC-12** — Given a project with at least 10 indexed Python files, when `GET /v1/search?q=<function name>` is called, then results include at least one chunk with score ≥ 0.75 referencing the expected file.

**AC-13** — Given a query routed to Scenario 2, when the local LLM is unable to produce a confident answer (e.g., returns low-confidence or refuses), then the system escalates to Scenario 3 rather than returning an empty or malformed answer.

**AC-14** — Given Ollama is **not** reachable, when `POST /v1/query` is called, then the service returns an appropriate error (HTTP 503 or equivalent) with a human-readable message — it does not silently call the external API without configuration.

---

### Scenario 3 — Smart Proxy to External API (Minimal Token Spend)

**AC-15** — Given a Bug DB miss and local LLM escalation, when `POST /v1/query` is called, then `source` equals `"external_api"` and `external_tokens_used` is greater than `0`.

**AC-16** — Given an external API call is made, when comparing token count to a naive full-codebase payload for the same query, then `external_tokens_used` is no greater than 20% of the naive full-codebase token count (surgical context only).

**AC-17** — Given an external API call, when the context payload is assembled, then only 2–3 most relevant functions/classes from the codebase are included — not whole files or the full project.

**AC-18** — Given an external API call, when `POST /v1/query` responds, then the `answer` field contains the external LLM's response and is non-empty.

**AC-19** — Given an external API call results in an HTTP error (e.g., 429, 500 from Copilot/Claude), when `POST /v1/query` is called, then the service returns an appropriate error response to the caller without crashing (no HTTP 500 from CodePal itself).

**AC-20** — Given a query that requires external escalation, when `context_chunks_used` is reported, then its value reflects only the chunks sent to the external API (not all indexed chunks).

---

### Indexing & Vector Search

**AC-21** — Given `POST /v1/index` is called with a valid `project_path`, when indexing completes, then the response includes `indexed_files` (integer ≥ 1), `chunks_added` (integer ≥ 1), and `duration_ms` (integer > 0).

**AC-22** — Given a Python source file with 5 functions, when it is indexed via `POST /v1/index`, then `chunks_added` is at least 5 (one chunk per function minimum).

**AC-23** — Given the git post-commit hook is installed via `codepal hooks install --project <path>`, when a commit is made to that project, then `POST /v1/index` is automatically called with only the changed files listed in `changed_files`.

**AC-24** — Given a file is re-indexed after modification, when `GET /v1/search` is queried for content from that file, then results reflect the updated content — stale chunks for the same file path are replaced (upsert behavior).

**AC-25** — Given a project path that does not exist, when `POST /v1/index` is called with that path, then the service returns HTTP 400 or 422 with a descriptive error message.

---

### API Contract & Service Quality

**AC-26** — Given the service is running, when `GET /v1/status` is called, then the response includes: Ollama connectivity status, total indexed chunks count, total bug solutions stored count, and an HTTP 200 status code.

**AC-27** — Given the MCP server is running at `http://localhost:8742/mcp`, when a `.vscode/mcp.json` client connects, then all 6 MCP tools (`query_code`, `index_path`, `search_code`, `save_bug_solution`, `search_bug_solutions`, `get_status`) are discoverable and callable.

**AC-28** — Given `POST /v1/query` is called with a missing required field (`query`), when the request is received, then the service returns HTTP 422 with a validation error body — not HTTP 500.

**AC-29** — Given the service is running, when `POST /v1/query` receives a request with `language` and `project_path` fields, then routing logic uses `project_path` to scope vector search to that project's indexed chunks only (no cross-project contamination).

**AC-30** — Given any REST or MCP call that triggers an external API request, when the response is returned to the caller, then the `source` field correctly identifies `"external_api"` — callers can always determine whether external tokens were spent.

---

## 3. Definition of Done

A feature or story is **Done** when **all 8** of the following conditions are met:

- [ ] **1. AC coverage** — All acceptance criteria relevant to the feature pass (manual or automated test); each AC is traceable to a test case or documented test run.
- [ ] **2. Routing correctness** — All three routing scenarios (Bug DB hit, Local LLM, External API) are exercised end-to-end with a real Ollama instance and produce the correct `source` values in responses.
- [ ] **3. API contract verified** — REST endpoints and MCP tools return the exact response shapes specified in the design doc (fields, types, HTTP status codes); no undocumented fields or missing required fields.
- [ ] **4. Token guard confirmed** — At least one external-escalation test verifies that `external_tokens_used` is ≤ 20% of the naive full-codebase baseline for the same query (AC-16 verified with real data).
- [ ] **5. Error handling tested** — Unhappy paths are tested: Ollama unreachable (AC-14), invalid index path (AC-25), malformed query (AC-28), external API error (AC-19) — all return structured errors, no unhandled 500s.
- [ ] **6. Git hook works** — Post-commit hook installs cleanly via `codepal hooks install`, fires on commit, and triggers incremental index of only changed files (AC-23 verified in a real git repo).
- [ ] **7. No regressions** — Existing passing tests remain green after the change; CI or equivalent check passes.
- [ ] **8. Documentation updated** — `design.md` version is incremented if the feature changes any API shape, behavior, or architecture; `SKILL.md` for OpenClaw reflects any endpoint changes; inline code docstrings are present for all public functions.

---

*Questions or scope changes? Update this file and bump the version. Do not ship scope changes without revising the AC table above.*
