# CodePal — Architecture Document

> **Version:** 0.1  
> **Status:** Draft  
> **Date:** 2026-05-21  
> **Inputs:** `design.md` v0.1, `pm-scope.md` v0.1  
> **Author:** Tech Lead

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Component Breakdown & Responsibilities](#2-component-breakdown--responsibilities)
3. [Data Flow — All Three Routing Scenarios](#3-data-flow--all-three-routing-scenarios)
4. [Technology Choices with Rationale](#4-technology-choices-with-rationale)
5. [Implementation Order with Dependencies](#5-implementation-order-with-dependencies)
6. [Risks & Open Technical Questions](#6-risks--open-technical-questions)

---

## 1. System Overview

CodePal is a local API service (macOS, `localhost:8742`) that acts as a smart context filter in front of external LLM APIs. It intercepts coding queries and attempts to resolve them cheaply — first from a bug solution cache, then from a local LLM with relevant code context, and only escalates to external paid APIs when necessary and with a surgically minimal payload.

### High-Level Topology

```
┌──────────────────────────────────────────────────────────────┐
│                         Callers                              │
│                                                              │
│   OpenClaw Agent            GitHub Copilot Agent            │
│   (web_fetch → REST)        (.vscode/mcp.json → MCP)        │
└─────────────┬───────────────────────────┬────────────────────┘
              │ HTTP REST                 │ MCP (SSE/HTTP)
              ▼                           ▼
┌──────────────────────────────────────────────────────────────┐
│                 CodePal Service  :8742                       │
│                                                              │
│  ┌──────────────────┐    ┌──────────────────────────────┐   │
│  │  FastAPI REST    │    │  fastmcp MCP Server          │   │
│  │  /v1/*           │    │  /mcp  (6 tools)             │   │
│  └────────┬─────────┘    └──────────────┬───────────────┘   │
│           └──────────────────┬──────────┘                   │
│                              ▼                               │
│              ┌───────────────────────────┐                  │
│              │     Query Dispatcher      │                  │
│              │  (routes A → B → C)       │                  │
│              └──────┬──────────┬─────────┘                  │
│                     │          │                             │
│          ┌──────────▼──┐  ┌────▼──────────────────────┐    │
│          │  Bug Repo   │  │     Code Index             │    │
│          │  (ChromaDB) │  │  (ChromaDB + tree-sitter)  │    │
│          └─────────────┘  └────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
              │                          │
              ▼                          ▼
  ┌───────────────────────┐   ┌─────────────────────────────┐
  │  Ollama  :11434       │   │  External LLM API           │
  │  qwen3:14b (chat)     │   │  GitHub Copilot / Claude    │
  │  nomic-embed-text     │   │  (only on Path C)           │
  └───────────────────────┘   └─────────────────────────────┘
```

---

## 2. Component Breakdown & Responsibilities

### 2.1 Transport Layer

#### FastAPI REST Server
- **Owns:** `/v1/query`, `/v1/index`, `/v1/search`, `/v1/bugs`, `/v1/bugs/search`, `/v1/status`
- **Responsibilities:**
  - Request validation via Pydantic models
  - HTTP status codes and error response shaping
  - Delegates all business logic to the service layer — no logic lives in route handlers
- **Does NOT own:** Business logic, LLM calls, ChromaDB access

#### fastmcp MCP Server
- **Owns:** `/mcp` endpoint, 6 MCP tools: `query_code`, `index_path`, `search_code`, `save_bug_solution`, `search_bug_solutions`, `get_status`
- **Responsibilities:**
  - MCP protocol compliance (SSE or streamable HTTP transport)
  - Tool discovery and schema exposure to MCP clients (e.g., Copilot Agent via `.vscode/mcp.json`)
  - Each tool is a thin adapter: unpack MCP arguments → call the same service function the REST route calls → return result
- **Does NOT own:** Any logic that isn't already in the service layer; no parallel implementations

**Key architectural constraint:** REST handlers and MCP tools MUST call identical service-layer functions. Zero business logic duplication between the two transport surfaces. If the routing logic needs to change, it changes once in the service layer and both surfaces benefit automatically.

---

### 2.2 Query Dispatcher

The central intelligence component. Receives a normalised query object and executes the three-path routing cascade.

**Responsibilities:**
- Orchestrate the A → B → C decision tree (see §3)
- Read configurable score thresholds (`bug_db_score_threshold`, `local_llm_score_threshold`)
- Populate the `source` field on every response: `"local_bug_db"`, `"local_llm"`, or `"external_api"`
- Track and return `context_chunks_used` and `external_tokens_used` on every response (AC-09, AC-16, AC-20)
- Handle fallthrough gracefully: if Ollama is unreachable, skip Path B and attempt Path C (AC-14)
- Return HTTP 503 if Path C is needed but no external API key is configured

**Does NOT own:** Vector search logic, embedding, LLM HTTP calls, context assembly — delegates to sub-components

---

### 2.3 Bug Solution Repository

Manages the fast-return cache of known error → solution pairs.

**Responsibilities:**
- Save bug solutions: `{error_pattern, solution, tags}` → embed with `nomic-embed-text` → upsert into ChromaDB `codepal_bugs` collection
- Search: embed query → cosine similarity search → return top-k results with scores
- Tag-based retrieval: tags stored as ChromaDB metadata, filterable alongside vector search
- Durability: ChromaDB persists to disk; solutions survive service restarts (AC-16)

**Collection schema:**
```
collection: codepal_bugs
document:   "{error_pattern} {solution}"   ← concatenated for richer embedding
metadata:   { tags: [...], solution: "...", created_at: "..." }
id:         sha256(error_pattern)[:16]     ← deduplication key
```

---

### 2.4 Code Indexer

Responsible for all aspects of turning source files into searchable vector chunks.

#### Sub-components:

**Parser** (`indexer/parser.py`)
- Uses `tree-sitter` to parse source files into ASTs
- Extracts top-level functions and classes with their: name, start/end line numbers, raw source text, docstring (if present)
- Supported languages for MVP: Python (required), JavaScript, TypeScript, Go, Rust
- Returns a list of `ParsedSymbol` objects; gracefully skips unparseable files (AC-25 partial)

**Chunker** (`indexer/chunker.py`)
- Wraps each `ParsedSymbol` into a `CodeChunk` dataclass:
  ```python
  @dataclass
  class CodeChunk:
      chunk_id: str      # sha256(file_path + "::" + symbol_name + "::" + start_line)[:16]
      file_path: str
      symbol_name: str
      start_line: int
      end_line: int
      language: str
      text: str          # raw source of the symbol
  ```
- `chunk_id` is the deduplication key used for upsert; same symbol = same ID across re-indexes

**Embedder** (`indexer/embedder.py`)
- Accepts a list of `CodeChunk` objects
- Batches them (default: 32 per request) and calls Ollama `/api/embed` with `nomic-embed-text`
- Returns `(chunk, vector)` pairs

**Store** (`indexer/store.py`)
- Upserts `(chunk, vector)` pairs into ChromaDB collection `codepal_code_{project_slug}`
- For incremental re-index: deletes all existing entries where `metadata.file_path` matches the changed file, then inserts fresh chunks — avoids stale chunks from renamed or deleted symbols (AC-24)

**Pipeline** (`indexer/pipeline.py`)
- Orchestrates: Parser → Chunker → Embedder → Store
- Accepts either `project_path` (full index: walk directory, filter by extension) or `changed_files` list (incremental: process only listed files)
- Returns `IndexResult(indexed_files, chunks_added, errors, duration_ms)` (AC-21)

---

### 2.5 Local LLM Interface

**Responsibilities:**
- Thin async HTTP wrapper around Ollama (`httpx.AsyncClient`)
- Two operations: `embed(texts: list[str]) → list[vector]` and `chat(messages: list[dict]) → str`
- Connectivity check used by `/v1/status` (AC-11, AC-26)
- Raises typed `OllamaUnavailableError` on `ConnectError` so the Dispatcher can handle it cleanly

**Does NOT own:** Prompt construction, routing decisions, result interpretation

---

### 2.6 Context Builder (inside Dispatcher)

Used only on Path B and Path C.

**Responsibilities:**
- Accepts a query + a list of `CodeChunk` results from vector search
- Path B: build a RAG prompt: system instructions + top-k chunks as context + user question
- Path C: build a minimal external payload: query + top 2–3 chunks only (AC-17); enforce the ≤20% token budget constraint (AC-16)
- Counts tokens for `external_tokens_used` reporting (approximate via `len(text.split()) * 1.3` is acceptable for MVP; no tokenizer dependency required)

---

### 2.7 External LLM Proxy (inside Dispatcher)

**Responsibilities:**
- Sends the minimal context payload to the configured external API (Copilot / Claude)
- Reads `external_llm.api_key` and `external_llm.base_url` from config
- Returns HTTP 503 with a clear message if `api_key` is not configured (AC-14 / AC-22 of pm-scope)
- Maps external API errors (429, 500, timeout) to structured CodePal error responses — no unhandled exceptions (AC-19)

---

### 2.8 Configuration

Single `config.toml` (location: `~/.config/codepal/config.toml` or `CODEPAL_CONFIG` env override). Loaded at startup via `pydantic-settings`; all fields have defaults so the service starts without a config file.

**Key config sections:**
```toml
[service]         # host, port
[ollama]          # base_url, models, timeouts
[indexer]         # embed_batch_size, supported_extensions
[dispatcher]      # bug_db_score_threshold, local_llm_score_threshold, top_k_chunks, proxy_max_chunks
[external_llm]    # api_key, base_url, model
```

---

### 2.9 Git Hook CLI

A minimal `typer` CLI providing `codepal hooks install --project <path>`.

**Responsibilities:**
- Copy the embedded `post-commit.sh` template into `<path>/.git/hooks/post-commit`
- Set executable permissions
- Idempotent: if hook already exists, update it (overwrite); do not append (AC-30 of pm-scope)
- Hook script itself: calls `POST /v1/index` with changed files via `curl`; exits 0 regardless of CodePal availability (AC-29 of pm-scope)

---

## 3. Data Flow — All Three Routing Scenarios

### Scenario A — Bug DB Hit (Full Local, Zero LLM Cost)

Triggered when the top bug solution's cosine similarity ≥ `bug_db_score_threshold` (default: 0.85; exact match ≥ 0.95 per AC-01, fuzzy match 0.80–0.94 per AC-02).

```
Client
  │
  │  POST /v1/query { query, project_path, ... }
  ▼
FastAPI / MCP Transport
  │  validates request shape (422 on missing fields — AC-28)
  ▼
Query Dispatcher
  │
  │  1. embed(query) via Ollama nomic-embed-text
  │
  ▼
Bug Repository — search(query_vector, top_k=1)
  │
  │  score ≥ threshold?  ──YES──▶  return solution
  │                                  source = "local_bug_db"
  │                                  external_tokens_used = 0
  │                                  latency target: < 500ms (AC-03)
  │  NO
  ▼
[fall through to Scenario B]
```

**Failure modes handled:**
- Empty Bug DB → graceful fallthrough, no 500 (AC-07)
- Ollama unavailable for embedding → skip Bug DB, attempt Path C (AC-14)

---

### Scenario B — Local LLM Handles It (Zero External API Cost)

Triggered when Bug DB misses and vector search finds relevant code chunks (top chunk score ≥ `local_llm_score_threshold`, default: 0.70).

```
[continuing from Scenario A miss]
  │
  ▼
Code Index — vector_search(query_vector, project_path, top_k=5)
  │
  │  returns ranked CodeChunks with scores
  │
  ▼
Context Builder (Path B)
  │  assembles RAG prompt:
  │    system: "You are a code assistant. Use the following code context..."
  │    context: top-k chunk texts (up to config.dispatcher.top_k_chunks)
  │    user: original query
  │
  ▼
Ollama — chat(qwen3:14b, prompt)
  │
  │  answer returned?
  │  confident (non-empty, substantive — AC-10)?  ──YES──▶  return answer
  │                                                           source = "local_llm"
  │                                                           context_chunks_used = k (AC-09)
  │                                                           external_tokens_used = 0
  │  NO / low confidence / Ollama error
  ▼
[fall through to Scenario C]
```

**Failure modes handled:**
- Ollama unreachable → typed `OllamaUnavailableError` → Dispatcher skips to Path C (AC-14)
- Empty answer from qwen3:14b → treated as low confidence → fall through (AC-13)
- No indexed chunks for project → `context_chunks_used = 0` → fall through

---

### Scenario C — Smart Proxy to External API (Minimal Token Spend)

Triggered when both Bug DB and local LLM fail to produce a confident answer, OR when Ollama is unavailable.

```
[continuing from Scenario B miss or Ollama unavailable]
  │
  ▼
Context Builder (Path C)
  │  selects top 2–3 chunks only (config.dispatcher.proxy_max_chunks — AC-17)
  │  assembles minimal payload:
  │    { "query": "...", "context": [chunk1, chunk2, chunk3] }
  │  calculates approximate token count for external_tokens_used
  │
  ▼
External LLM Proxy
  │
  │  api_key configured?  ──NO──▶  return HTTP 503 + human-readable error (AC-14/22)
  │
  │  YES
  │
  │  POST external_llm.base_url + headers(api_key) + minimal payload
  │
  ▼
External LLM API (Copilot / Claude)
  │
  │  2xx?  ──YES──▶  return answer
  │                    source = "external_api"
  │                    external_tokens_used = (reported or estimated)  (AC-15, AC-16)
  │                    context_chunks_used = chunks sent to API (AC-20)
  │
  │  4xx/5xx?  ──▶  structured error response from CodePal (no unhandled 500 — AC-19)
  │
  ▼
[response returned to caller]
```

**Token budget enforcement (AC-16):**
- `proxy_max_chunks` caps at 3 by default
- Each chunk is a single function/class, typically 5–50 lines
- For a 10k-line project, naive full-codebase = ~10,000 lines; 3 function chunks ≈ 150 lines = 1.5% — well within the ≤20% AC-16 threshold

---

### Indexing Data Flow

```
POST /v1/index { project_path } or { changed_files: [...] }
  │
  ▼
Indexer Pipeline
  │
  ├─ full index: walk project_path, filter by supported_extensions
  └─ incremental: use changed_files list directly
  │
  ▼
Parser (tree-sitter per file)
  │  extracts ParsedSymbol list (function/class name, lines, source)
  │  skips unparseable files, logs to errors[]
  │
  ▼
Chunker
  │  wraps each symbol into CodeChunk with deterministic chunk_id
  │
  ▼
Embedder (batch via Ollama nomic-embed-text)
  │  batch_size = 32 (configurable)
  │
  ▼
Store (ChromaDB upsert)
  │  incremental: delete old entries for file_path first, then upsert
  │  full: upsert all (chunk_id collision = replace)
  │
  ▼
Return IndexResult { indexed_files, chunks_added, errors, duration_ms }
```

---

## 4. Technology Choices with Rationale

### 4.1 Python 3.11+

**Chosen because:**
- First-class async support (`asyncio`, `anyio`) required by FastAPI + Ollama concurrent calls
- Richest LLM/ML ecosystem (ChromaDB, tree-sitter, httpx, pydantic all native)
- Fastest iteration speed for a v0.1 local tool

**Alternative considered:** Go — better performance, but ecosystem for ML/vector tooling is immature; wrong tradeoff for this workload.

---

### 4.2 FastAPI

**Chosen because:**
- Native async, native Pydantic v2 for request/response validation
- Automatic OpenAPI docs (useful for OpenClaw SKILL.md documentation)
- Uvicorn ASGI server included; same process can mount the MCP sub-app

**Alternative considered:** Flask/Starlette — FastAPI strictly better here; no reason to use raw Starlette.

---

### 4.3 fastmcp (MCP Server)

**Chosen because:**
- Purpose-built Python MCP implementation
- ASGI-compatible: mounts inside FastAPI as a sub-app at `/mcp`
- Single process = shared in-memory service objects with REST layer; no IPC complexity

**Critical constraint:** Both REST and MCP surfaces must call the same service functions. The MCP layer is purely a protocol adapter, not a second implementation.

**Risk:** ASGI mount with SSE transport is relatively new in `fastmcp`. Spike required in Phase 0 to confirm transport works under Uvicorn before the MCP phase (see §6).

---

### 4.4 ChromaDB (Embedded)

**Chosen because:**
- No separate server process — embedded library, persists to disk in a local directory
- Python-native API, strong upsert/delete primitives
- Purpose-built for the `(vector, metadata, document)` access pattern this system uses

**Two collection strategy:**

| Collection | Name Pattern | Reason for separation |
|---|---|---|
| Code chunks | `codepal_code_{project_slug}` | Isolated per project; can be wiped and re-indexed without touching bug solutions |
| Bug solutions | `codepal_bugs` | Global; survives project collection resets; different score thresholds apply |

**project_slug derivation:**
```python
slug = re.sub(r"[^a-z0-9_]", "_", project_path.lower())[:40]
slug += "_" + hashlib.md5(project_path.encode()).hexdigest()[:8]
# e.g. "codepal_code__users_alice_myproject_a1b2c3d4"
```

**Upsert key:** `sha256(file_path + "::" + symbol_name + "::" + start_line)[:16]`
- Same symbol in same file → same ID → upsert replaces, no duplicates
- Moved symbol (different line) → different ID → old entry deleted by file-path cleanup step

**Alternative considered:** pgvector (PostgreSQL) — requires a running server, eliminates the "no dependencies" property. Qdrant — same problem. ChromaDB embedded is the right tradeoff for a local macOS tool.

---

### 4.5 Ollama (Local LLM Runtime)

**Chosen because:**
- Single local runtime handles both inference (`qwen3:14b`) and embeddings (`nomic-embed-text`)
- No API keys, no network dependency for Paths A and B
- Model swaps require only config changes, not code changes

**Client approach:** Plain `httpx.AsyncClient` against Ollama's HTTP API. No Ollama Python SDK.

**Rationale for no SDK:**
- SDK adds abstraction without benefit for two narrow operations (`/api/embed`, `/api/chat`)
- `httpx` is already in the dependency tree (FastAPI TestClient)
- Direct HTTP = full control over timeout and connection pool settings

**Ollama API calls:**

| Operation | Endpoint | Payload |
|---|---|---|
| Batch embed | `POST /api/embed` | `{"model": "nomic-embed-text", "input": ["text1", "text2", ...]}` |
| Chat inference | `POST /api/chat` | `{"model": "qwen3:14b", "messages": [...], "stream": false}` |

**Connection limits:** `httpx.Limits(max_connections=4)` — Ollama is effectively single-threaded per model; more connections don't improve throughput and waste memory.

**Timeouts:**
- Embed: 30s (batch of 32 small chunks)
- Chat: 120s (qwen3:14b cold start can be slow)
- Both configurable in `config.toml`

---

### 4.6 tree-sitter (Code Parsing)

**Chosen because:**
- Incremental AST parser designed exactly for this use case
- Language-specific parsers available as Python packages (no compile step in 0.23+)
- Function-level extraction gives semantically coherent chunks — better retrieval precision than line-window splitting

**Function-level vs. line-window chunking:**

| Approach | Pros | Cons |
|---|---|---|
| Function-level (tree-sitter) | Semantic unit, no partial functions, includes docstring naturally | Requires language parser, setup per language |
| Fixed line windows (e.g., 50 lines) | Language-agnostic, simple | Splits functions mid-body, poor retrieval precision |
| Semantic chunking (LLM-based) | Best quality | Too slow and expensive for indexing |

Function-level is the right tradeoff for a code Q&A tool.

**MVP language targets:** Python (required, AC-22 references Python explicitly), plus JavaScript, TypeScript, Go, Rust via their `tree-sitter-*` packages. Unlisted languages → skip file, log to `errors[]`.

---

### 4.7 `uv` (Package Manager)

**Chosen because:**
- Dramatically faster than `pip` for dependency resolution and install
- `pyproject.toml`-native
- `uv sync` produces reproducible environments

**No impact on runtime.** `uv` is a dev/install tool only.

---

## 5. Implementation Order with Dependencies

Dependencies are listed as blockers — a phase cannot start until its blockers are done.

```
Phase 0 — Scaffold
  Blockers: none
  Delivers: pyproject.toml, package structure, config loading,
            FastAPI app factory, GET /v1/status (hardcoded "ok"),
            uvicorn starts cleanly
  Exit gate: Service starts; /v1/status returns 200

        │
        ▼

Phase 1 — Infrastructure Plumbing
  Blockers: Phase 0
  Delivers: ChromaDB client factory + collection helpers (db/chroma.py)
            Ollama async HTTP client (llm/ollama.py): embed + chat
            GET /v1/status now reports real Ollama + ChromaDB state
  Exit gate: Can embed a string via Ollama; upsert + query ChromaDB round-trip works
  Risk: Spike fastmcp ASGI mount here to de-risk Phase 5 early

        │
        ▼

Phase 2 — Code Indexer
  Blockers: Phase 1 (needs ChromaDB + Ollama client)
  Delivers: parser.py, chunker.py, embedder.py, store.py, pipeline.py
            POST /v1/index (full and incremental)
            GET /v1/search (vector query)
  Exit gate: Index a real Python project; GET /v1/search returns ranked results;
             re-index same file = no duplicate chunks

        │
        ▼

Phase 3 — Bug Solution Repository
  Blockers: Phase 1 (needs ChromaDB + Ollama client)
  NOTE: Phases 2 and 3 can run in parallel — they share infrastructure but don't depend on each other
  Delivers: bugs/repository.py
            POST /v1/bugs, GET /v1/bugs/search
  Exit gate: Save a bug, search for it, retrieve across restart

        │
        ▼ (both Phase 2 and 3 must be done)

Phase 4 — Query Dispatcher
  Blockers: Phases 2 AND 3 (needs both Code Index and Bug Repository)
  Delivers: dispatcher/router.py (A/B/C routing)
            dispatcher/local_llm.py (RAG prompt + qwen3:14b)
            dispatcher/proxy.py (context builder + external API call)
            POST /v1/query — full end-to-end
  Exit gate: All three routing paths exercised manually with correct `source` values

        │
        ▼

Phase 5 — MCP Server
  Blockers: Phase 4 (all service functions must exist before MCP adapts them)
  Delivers: mcp/server.py — 6 tools mounted at /mcp
  Exit gate: MCP client (Claude Desktop or test client) can discover and call all 6 tools

        │
        ▼

Phase 6 — Git Hook CLI
  Blockers: Phase 2 (POST /v1/index must work before hook is useful)
  NOTE: Can start in parallel with Phase 5 — no dependency between them
  Delivers: cli/hooks.py, scripts/post-commit.sh
            `codepal hooks install --project <path>`
  Exit gate: Install hook, commit to test repo, incremental index fires automatically

        │
        ▼

Phase 7 — Hardening & DoD
  Blockers: Phases 5 and 6
  Delivers: Full pytest suite covering all 30 ACs
            structlog structured logging
            graceful startup (Ollama wait with timeout)
            README
            Performance validation (10k-line index < 60s)
  Exit gate: All 8 Definition of Done items checked off
```

### Parallel Opportunities

| Parallel pair | Notes |
|---|---|
| Phase 2 + Phase 3 | Both depend only on Phase 1; different files, no shared state |
| Phase 5 + Phase 6 | Both depend on Phase 4; different files, no shared state |

With one developer: run sequentially. With two: Phases 2/3 and Phases 5/6 can overlap.

---

## 6. Risks & Open Technical Questions

### Risk 1 — fastmcp ASGI Mount Under Uvicorn (HIGH)
**Description:** `fastmcp`'s ASGI sub-app mount with SSE transport is relatively new. It may not behave correctly under Uvicorn's event loop, particularly for long-lived SSE connections that MCP clients expect.  
**Mitigation:** Spike this in Phase 1 (not Phase 5). Create a minimal FastAPI app, mount fastmcp, connect a test MCP client, verify SSE streams work. If broken, evaluate: (a) streamable-HTTP transport instead of SSE, (b) `mcp` SDK (official, lower-level) instead of `fastmcp`, (c) separate process with IPC.  
**Owner:** Tech Lead  
**Decision needed by:** End of Phase 1

---

### Risk 2 — Ollama `/api/embed` Batch Form (MEDIUM)
**Description:** The batch input form `{"input": ["text1", "text2"]}` for `/api/embed` was introduced in Ollama 0.3.x. Older Ollama installs only accept single `{"prompt": "text"}` form.  
**Mitigation:** Add a version check at startup; log a clear warning if Ollama < 0.3 is detected. Fall back to single-item requests if needed (slower but functional). Document minimum Ollama version in README.  
**Owner:** SDE implementing Phase 1  
**Decision needed by:** Phase 1

---

### Risk 3 — qwen3:14b Inference Latency (MEDIUM)
**Description:** On first cold invocation, `qwen3:14b` model loading may exceed 120s on older Apple Silicon hardware (M1 base, 8GB unified memory). Subsequent calls are faster.  
**Mitigation:** Add a startup warm-up: send a trivial chat request to Ollama during service startup to trigger model load. Increase configurable `chat_timeout_s` default to 180s. Document hardware requirements (M1 Pro / 16GB+ recommended for reliable latency).  
**Owner:** SDE implementing Phase 4  
**Decision needed by:** Phase 4

---

### Risk 4 — tree-sitter 0.23 Python Binding API Changes (MEDIUM)
**Description:** tree-sitter 0.23 introduced a new Python bindings API. The grammar packages (`tree-sitter-python`, `tree-sitter-javascript`, etc.) must match the 0.23 binding API, and not all language grammars have been updated to 0.23 at the same pace.  
**Mitigation:** Test all 5 language parsers (Python, JS, TS, Go, Rust) against pinned versions in Phase 2 before wiring to the API. Python is required; other languages can be deprioritised if their grammar packages lag behind.  
**Owner:** SDE implementing Phase 2  
**Decision needed by:** Phase 2 Day 1

---

### Risk 5 — ChromaDB Collection Name Length Limits (LOW)
**Description:** ChromaDB enforces a collection name length limit (63 characters). Long project paths could produce slugs that exceed this after the MD5 suffix is appended.  
**Mitigation:** Enforce `slug = slug[:54] + "_" + md5[:8]` = 63 chars maximum. Add a unit test for this. Already accounted for in the slug derivation formula in §2.2.  
**Owner:** SDE implementing Phase 1  
**Decision needed by:** Phase 1

---

### Risk 6 — Token Count Accuracy for AC-16 (LOW)
**Description:** AC-16 requires `external_tokens_used` to be ≤ 20% of naive full-codebase token count. MVP uses an approximate token count (`len(text.split()) * 1.3`). If the external API returns an actual token count in its response, prefer that over the approximation.  
**Mitigation:** Use actual token counts from API responses where available (OpenAI-compatible APIs return `usage.total_tokens`). For the naive baseline, count tokens across all indexed chunks for the project at query time — expensive once but only needed for the AC-16 verification test, not production queries.  
**Owner:** SDE implementing Phase 4  
**Decision needed by:** Phase 4

---

### Open Technical Question 1 — Local LLM Confidence Signal
**Question:** How does the Dispatcher decide Path B answer is "confident enough" to not escalate to Path C? qwen3:14b does not return an explicit confidence score.

**Options:**
- (a) **Heuristic on answer length/content:** If answer is non-empty and > N words → treat as confident. Simple, fragile.
- (b) **Secondary prompt:** Ask the model "How confident are you?" in a follow-up. Doubles latency.
- (c) **Threshold on retrieved chunk scores:** If top chunk score ≥ threshold AND model returns non-empty → treat as confident. Indirect but fast.
- (d) **Post-MVP:** Always use Path B if chunks found, escalate to Path C only on Ollama failure. Defer confidence scoring to a later version.

**Recommendation for MVP:** Option (d). The PM scope explicitly lists "Explicit confidence scoring" as OUT of scope. Implement: if Ollama is reachable and top chunk score ≥ `local_llm_score_threshold`, call Path B and return its answer. Only escalate if Ollama is unreachable or returns an empty/error response. Document this as a known limitation.

---

### Open Technical Question 2 — External LLM API Shape
**Question:** The design targets both GitHub Copilot and Claude. Their API shapes differ (Copilot uses OpenAI-compatible format; Claude uses Anthropic format).

**Recommendation for MVP:** Target OpenAI-compatible API only (Copilot). Use a simple `base_url` + `api_key` config; the proxy sends a standard `POST /chat/completions` payload. Anthropic support can be added post-MVP via an adapter. Document this constraint in the README.

---

*End of architecture document. Update version and date when implementation decisions change this baseline.*
