# CodePal — Tech Lead Implementation Plan

> **Version:** 0.1  
> **Author:** Tech Lead  
> **Date:** 2026-05-21  
> **References:** design.md, docs/pm-scope.md

---

## 1. Phased Implementation Order

### Phase 0 — Project Scaffold (Day 1)
Get the skeleton in place so every other phase has a home.

- `pyproject.toml` with all pinned dependencies
- `src/codepal/` package structure (empty modules, `__init__.py` files)
- `config.py` — TOML-backed config with env override
- `main.py` — FastAPI app factory + Uvicorn entrypoint
- `GET /v1/status` returning `{"status": "ok"}` (hardcoded for now)
- CI-friendly: `uv sync`, `uvicorn codepal.main:app --reload` works

**Exit criterion:** Service starts, `/v1/status` returns 200.

---

### Phase 1 — ChromaDB + Ollama Plumbing (Days 2–3)
Wire up the two core infrastructure dependencies before building on top of them.

- `db/chroma.py` — ChromaDB client factory, collection helpers
- `llm/ollama.py` — thin async HTTP wrapper for Ollama `/api/embed` and `/api/chat`
- Unit tests: embed a string, get a vector back; upsert/query ChromaDB
- `GET /v1/status` now reports real Ollama + ChromaDB availability

**Exit criterion:** Can embed text via Ollama, store it in ChromaDB, and retrieve it.

---

### Phase 2 — Code Indexer (Days 4–6)
The foundation of semantic search. Everything else depends on indexed chunks.

- `indexer/parser.py` — tree-sitter AST parser, extracts functions/classes
- `indexer/chunker.py` — wraps parsed symbols into `CodeChunk` dataclass with metadata
- `indexer/embedder.py` — batches chunks through `llm/ollama.py` for embeddings
- `indexer/store.py` — upserts chunks into ChromaDB (file-path-keyed, dedup logic)
- `indexer/pipeline.py` — orchestrates parser → chunker → embedder → store
- Wire `POST /v1/index` to the pipeline
- Wire `GET /v1/search` to ChromaDB vector query

**Exit criterion:** Index a real Python project, query it, get ranked results. Re-index same file = no duplicates.

---

### Phase 3 — Bug Solution Repository (Day 7)
Simpler than code indexing; same ChromaDB primitives, separate collection.

- `bugs/repository.py` — save/search bug solutions (separate ChromaDB collection)
- Wire `POST /v1/bugs` and `GET /v1/bugs/search`

**Exit criterion:** Save a bug solution, retrieve it by fuzzy error text across restarts.

---

### Phase 4 — Query Dispatcher (Days 8–10)
The intelligence layer. Depends on Phases 1–3 being complete.

- `dispatcher/router.py` — orchestrates the three routing paths (A/B/C)
- `dispatcher/local_llm.py` — build prompt from query + top-k chunks, call qwen3:14b
- `dispatcher/proxy.py` — build minimal context payload, call external LLM API
- Wire `POST /v1/query`
- Configurable score thresholds (`bug_db_threshold`, `local_llm_threshold`)
- Path C returns HTTP 503 when no external API key is configured

**Exit criterion:** All three routing paths exercised in a manual end-to-end test.

---

### Phase 5 — MCP Server (Days 11–12)
Layer on top of existing business logic; no new core functionality.

- `mcp/server.py` — `fastmcp` server, 6 tools wired to service layer functions
- Mount MCP transport on the same FastAPI app at `/mcp`
- No duplication of business logic — MCP tools call the same functions as REST handlers

**Exit criterion:** Claude Desktop (or any MCP client) can call all 6 tools.

---

### Phase 6 — Git Hook + CLI (Day 13)
Thin operational layer; depends on `POST /v1/index` working.

- `cli/hooks.py` — `codepal hooks install --project <path>` subcommand (uses `typer`)
- `scripts/post-commit.sh` — hook template embedded in the package
- Hook fails silently if service not running (curl exit code ignored, `|| true`)
- Idempotency: check if hook exists before writing

**Exit criterion:** Install hook, make a commit, watch indexing happen automatically.

---

### Phase 7 — Polish & Hardening (Days 14–15)
Make it production-ready for the MVP bar.

- Full test suite (`pytest`) covering all acceptance criteria
- Structured logging (`structlog`)
- Graceful shutdown, startup health wait for Ollama
- README: install, start, hook setup, external API key config
- Performance check: index 10k-line repo in < 60s

---

## 2. Folder / File Layout

```
codepal/
├── pyproject.toml
├── README.md
├── docs/
│   ├── design.md
│   ├── pm-scope.md
│   └── tech-lead-plan.md       ← this file
├── scripts/
│   └── post-commit.sh          ← git hook template (embedded in package)
├── tests/
│   ├── conftest.py
│   ├── test_indexer.py
│   ├── test_bugs.py
│   ├── test_dispatcher.py
│   ├── test_search.py
│   └── test_mcp.py
└── src/
    └── codepal/
        ├── __init__.py
        ├── main.py             ← FastAPI app factory + Uvicorn entry
        ├── config.py           ← TOML config + env override (pydantic-settings)
        │
        ├── api/
        │   ├── __init__.py
        │   ├── routes.py       ← APIRouter: all /v1/* endpoints
        │   └── models.py       ← Pydantic request/response schemas
        │
        ├── db/
        │   ├── __init__.py
        │   └── chroma.py       ← ChromaDB client factory + collection helpers
        │
        ├── llm/
        │   ├── __init__.py
        │   └── ollama.py       ← async HTTP wrapper: /api/embed + /api/chat
        │
        ├── indexer/
        │   ├── __init__.py
        │   ├── parser.py       ← tree-sitter AST parser → symbol extraction
        │   ├── chunker.py      ← CodeChunk dataclass + chunking logic
        │   ├── embedder.py     ← batch embed chunks via ollama.py
        │   ├── store.py        ← upsert/dedup chunks in ChromaDB
        │   └── pipeline.py     ← orchestrates parser→chunker→embedder→store
        │
        ├── bugs/
        │   ├── __init__.py
        │   └── repository.py   ← save/search bug solutions (ChromaDB)
        │
        ├── dispatcher/
        │   ├── __init__.py
        │   ├── router.py       ← routing logic: path A/B/C decision
        │   ├── local_llm.py    ← prompt builder + qwen3:14b call
        │   └── proxy.py        ← minimal context builder + external API call
        │
        ├── mcp/
        │   ├── __init__.py
        │   └── server.py       ← fastmcp server, 6 tools, mounted at /mcp
        │
        └── cli/
            ├── __init__.py
            └── hooks.py        ← typer CLI: `codepal hooks install --project`
```

---

## 3. Key Technical Decisions

### 3.1 MCP + FastAPI in the Same Process

**Decision:** Run `fastmcp` as a mounted ASGI sub-application inside the FastAPI process. Mount point: `/mcp`.

**Why:** A single process means:
- One port to manage (`8742`)
- MCP tools share the same in-process service objects (no IPC overhead, no serialisation of ChromaDB handles)
- Single `uvicorn` invocation for users and the git hook

**How:**
```python
# main.py
from fastapi import FastAPI
from fastmcp import FastMCP

app = FastAPI()
mcp = FastMCP("codepal")

# register tools on `mcp`, then:
app.mount("/mcp", mcp.get_asgi_app())
```

MCP tools call the same service-layer functions as REST handlers — zero business logic duplication.

**Risk:** `fastmcp` ASGI mount is relatively new. Pin to a tested version and verify SSE transport works under Uvicorn before Phase 5.

---

### 3.2 ChromaDB Collection Naming

Two collections, named deterministically:

| Collection | Name | Contents |
|---|---|---|
| Code chunks | `codepal_code_<project_slug>` | Indexed function/class chunks with file/line metadata |
| Bug solutions | `codepal_bugs` | Saved error → solution pairs |

**`project_slug`** = URL-safe hash of the absolute project path:
```python
import hashlib, re
slug = re.sub(r"[^a-z0-9_]", "_", project_path.lower())[:40]
slug += "_" + hashlib.md5(project_path.encode()).hexdigest()[:8]
# e.g. "codepal_code__users_alice_myproject_a1b2c3d4"
```

**Why separate collections rather than metadata filtering:**
- ChromaDB performance is better with smaller collections
- Bug solutions need different distance thresholds than code chunks
- Keeps bug DB intact when a code collection is wiped and re-indexed

**Upsert keying:** Each chunk's ChromaDB `id` is `sha256(file_path + "::" + symbol_name + "::" + start_line)[:16]`. This makes re-indexing the same file idempotent — upsert replaces, no duplicates.

---

### 3.3 Incremental Indexing Strategy

**Full index** (first run or forced):
- Walk the project directory, filter by language extension
- Parse all files via tree-sitter pipeline
- Batch embed + upsert into ChromaDB (batch size: 32 chunks per Ollama call)

**Incremental index** (git hook / `changed_files` payload):
1. Receive list of changed file paths
2. Delete all existing ChromaDB entries whose metadata `file_path` matches each changed file
3. Re-parse and re-embed only those files
4. Upsert new chunks

**Why delete-then-reinsert rather than diff:**
- Simpler logic, no risk of stale chunks from renamed/moved symbols
- Ollama embedding is the bottleneck, not ChromaDB writes; deleting a few hundred chunks is instant

**Deleted files:** If a file is deleted, step 2 above removes its chunks automatically. The git hook passes files from `git diff-tree`, which includes deletions; the indexer handles a missing file gracefully (skip parse, delete old chunks, done).

**Batch sizing:** 32 chunks per `/api/embed` call. Ollama can handle larger batches but 32 balances latency and memory. Make this configurable (`indexer.embed_batch_size`).

---

### 3.4 Ollama HTTP Calls

**Client:** Plain `httpx.AsyncClient` — no Ollama Python SDK. The SDK adds abstraction without benefit for our narrow use case, and `httpx` is already in the dependency tree for FastAPI.

**Endpoints used:**

| Purpose | Endpoint | Notes |
|---|---|---|
| Embeddings | `POST /api/embed` | `{"model": "nomic-embed-text", "input": [...]}` — batch form |
| Inference | `POST /api/chat` | `{"model": "qwen3:14b", "messages": [...], "stream": false}` |

**Connection management:**
```python
# llm/ollama.py
class OllamaClient:
    def __init__(self, base_url: str, timeout: float = 120.0):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout),
            limits=httpx.Limits(max_connections=4),
        )
```

Single shared `OllamaClient` instance, injected via FastAPI dependency. 4 concurrent connections is enough; Ollama is single-threaded per model anyway.

**Timeout strategy:**
- Embedding: 30s timeout (batch of 32 small chunks)
- Inference (`qwen3:14b`): 120s timeout (can be slow on first tokens)
- Both configurable in `config.toml`

**Error handling:**
- `httpx.ConnectError` → surface as 503, include `"ollama_available": false` in `/v1/status`
- Non-200 from Ollama → log full response body, raise `OllamaError` (caught at dispatcher level)
- Dispatcher path C triggers automatically if Ollama is unreachable

---

## 4. Full `pyproject.toml`

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "codepal"
version = "0.1.0"
description = "Local smart context optimizer for LLM coding assistants"
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }

dependencies = [
    # API framework
    "fastapi==0.115.5",
    "uvicorn[standard]==0.32.1",
    "pydantic==2.10.3",
    "pydantic-settings==2.7.0",

    # MCP server
    "fastmcp==2.3.3",

    # HTTP client (Ollama calls + external LLM proxy)
    "httpx==0.28.1",

    # Vector store
    "chromadb==0.6.3",

    # Code parsing
    "tree-sitter==0.23.2",
    "tree-sitter-python==0.23.6",
    "tree-sitter-javascript==0.23.1",
    "tree-sitter-typescript==0.23.2",
    "tree-sitter-go==0.23.4",
    "tree-sitter-rust==0.23.2",

    # Config
    "tomli==2.2.1",           # TOML parser (stdlib tomllib for 3.11+, this for compat)

    # CLI
    "typer==0.15.1",

    # Logging
    "structlog==24.4.0",

    # Utilities
    "anyio==4.7.0",           # async primitives (shared with fastapi/uvicorn)
]

[project.scripts]
codepal = "codepal.cli.hooks:app"

[project.optional-dependencies]
dev = [
    "pytest==8.3.4",
    "pytest-asyncio==0.24.0",
    "pytest-cov==6.0.0",
    "httpx==0.28.1",          # also used in tests for TestClient
    "ruff==0.8.4",
    "mypy==1.13.0",
]

[tool.hatch.build.targets.wheel]
packages = ["src/codepal"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]

[tool.mypy]
python_version = "3.11"
strict = true
ignore_missing_imports = true
```

### Dependency Rationale

| Package | Why this version |
|---|---|
| `fastapi 0.115.5` | Latest stable at time of writing; Pydantic v2 native |
| `fastmcp 2.3.3` | Stable ASGI mount support; avoid 0.x era which lacked it |
| `chromadb 0.6.3` | Embedded mode stable; 0.6.x has improved upsert perf |
| `tree-sitter 0.23.x` | 0.23 introduced the new Python bindings API (no C extension manual compile) |
| `httpx 0.28.1` | Required by FastAPI TestClient and our Ollama client |
| `typer 0.15.1` | Stable; compatible with Pydantic v2 |
| `structlog 24.4.0` | Async-safe structured logging, integrates with uvicorn |

---

## 5. Configuration Schema (`config.toml`)

Place at `~/.config/codepal/config.toml` (or `CODEPAL_CONFIG` env var override):

```toml
[service]
host = "127.0.0.1"
port = 8742

[ollama]
base_url = "http://localhost:11434"
embed_model = "nomic-embed-text"
chat_model = "qwen3:14b"
embed_timeout_s = 30
chat_timeout_s = 120

[indexer]
embed_batch_size = 32
supported_extensions = [".py", ".js", ".ts", ".go", ".rs"]

[dispatcher]
bug_db_score_threshold = 0.85
local_llm_score_threshold = 0.70
top_k_chunks = 5
proxy_max_chunks = 3

[external_llm]
# Leave empty to disable Path C
api_key = ""
base_url = "https://api.githubcopilot.com"
model = "gpt-4o"
```

---

## 6. Critical Path & Risk Items

| Risk | Mitigation |
|---|---|
| `fastmcp` ASGI mount stability | Spike in Phase 0; validate SSE transport works before committing to it |
| `tree-sitter` 0.23 binding API differences across languages | Test all 5 language parsers in Phase 2 before wiring to API |
| `qwen3:14b` inference latency > 120s on cold start | Increase timeout; add startup warm-up ping to Ollama |
| ChromaDB collection size limits | Not an issue for MVP (single project); document for future multi-project work |
| Ollama `nomic-embed-text` batch API shape | Verify `/api/embed` accepts `input: [str]` array form (it does as of Ollama 0.3+) |

---

*End of tech lead plan. Update this file as implementation decisions evolve.*
