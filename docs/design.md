# CodePal — Software Design Document

> **Version:** 0.1 (MVP)
> **Status:** Draft
> **Last Updated:** 2026-05-21

---

## 1. Project Overview & Value Proposition

**CodePal** is a local API service for macOS that acts as a smart context optimizer sitting in front of expensive LLM APIs (GitHub Copilot, Claude, etc.).

Most AI coding assistants blindly send entire files or projects to paid external APIs — wasting tokens and money on context that's irrelevant to the problem at hand. CodePal intercepts those requests and takes a smarter path:

1. **Already know the answer?** Return it from the local bug solution repository instantly.
2. **Can figure it out locally?** Route to a local LLM (Ollama + qwen3:14b) with semantically relevant code snippets retrieved via vector search.
3. **Must escalate externally?** Build a minimal, laser-focused context payload — only the 2–3 functions/classes actually relevant to the error or question — and forward that to the external API.

> **Core value proposition:**
> *"Before you spend a token on Claude or Copilot, ask CodePal. It either knows the answer already, figures it out locally, or tells the external API exactly where to look."*

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Callers / Clients                    │
│                                                         │
│  OpenClaw Agent          GitHub Copilot Agent           │
│  (SKILL.md + web_fetch)  (.vscode/mcp.json)             │
└────────────┬─────────────────────────┬──────────────────┘
             │ REST                    │ MCP
             ▼                         ▼
┌─────────────────────────────────────────────────────────┐
│               CodePal API Service (FastAPI)             │
│                    localhost:8742                       │
│                                                         │
│  ┌──────────────┐   ┌──────────────┐  ┌─────────────┐  │
│  │  REST Router │   │  MCP Server  │  │  Query      │  │
│  │  /v1/query   │   │  tools:      │  │  Dispatcher │  │
│  │  /v1/index   │   │  query_code  │  │             │  │
│  │  /v1/bugs    │   │  index_path  │  │             │  │
│  └──────┬───────┘   └──────┬───────┘  └──────┬──────┘  │
│         └──────────────────┴─────────────────┘         │
│                            │                            │
│         ┌──────────────────▼──────────────────┐        │
│         │          Core Engine                │        │
│         │                                     │        │
│         │  ┌─────────────┐  ┌──────────────┐  │        │
│         │  │ Bug Solution│  │ Vector Search │  │        │
│         │  │ Repository  │  │ (ChromaDB)    │  │        │
│         │  └─────────────┘  └──────┬───────┘  │        │
│         │                          │           │        │
│         │  ┌───────────────────────▼─────────┐ │        │
│         │  │   Context Builder               │ │        │
│         │  │   (assembles minimal payload)   │ │        │
│         │  └───────────────────────┬─────────┘ │        │
│         └──────────────────────────┼───────────┘        │
└────────────────────────────────────┼───────────────────┘
                                     │
          ┌──────────────────────────▼────────────────────┐
          │               Local LLM Layer                 │
          │         Ollama (localhost:11434)               │
          │    qwen3:14b (inference)                       │
          │    nomic-embed-text (embeddings)               │
          └──────────────────────────┬────────────────────┘
                                     │ (only on escalation)
                                     ▼
                        ┌────────────────────────┐
                        │  External LLM APIs     │
                        │  GitHub Copilot / Claude│
                        └────────────────────────┘
```

---

## 3. The Three Core Scenarios

### Scenario 1 — Full Local Hit (Zero External API Cost)

```
Client Query (error/question)
        │
        ▼
┌───────────────────┐
│  Bug Solution DB  │  ← exact or fuzzy match found
│  (ChromaDB)       │
└────────┬──────────┘
         │  match found
         ▼
   Return stored solution
   (zero Ollama, zero external API)
```

**When it applies:** The error message, stack trace, or question closely matches a previously saved bug solution.

**Result:** Instant answer from local storage. No LLM involved.

---

### Scenario 2 — Local LLM Handles It (Zero External API Cost)

```
Client Query
        │
        ▼
  [Bug DB miss]
        │
        ▼
┌────────────────────┐
│  Vector Search     │  ← semantic lookup of codebase
│  (ChromaDB)        │    returns top-k relevant snippets
└────────┬───────────┘
         │  relevant code snippets
         ▼
┌────────────────────┐
│  Ollama qwen3:14b  │  ← query + snippets as context
│  Local Inference   │
└────────┬───────────┘
         │  confident answer
         ▼
   Return answer to client
   (no external API call)
```

**When it applies:** The local LLM can produce a confident answer using retrieved context from the indexed codebase.

**Result:** Answer generated locally. No paid API usage.

---

### Scenario 3 — Smart Proxy to External API (Minimal Token Spend)

```
Client Query
        │
        ▼
  [Bug DB miss + local LLM uncertain]
        │
        ▼
┌────────────────────┐
│  Vector Search     │  ← find 2-3 most relevant
│  (ChromaDB)        │    functions/classes from codebase
└────────┬───────────┘
         │  minimal relevant snippets
         ▼
┌─────────────────────────┐
│  Context Builder        │
│  - error/question       │
│  - only relevant chunks │
│  (NOT whole codebase)   │
└────────┬────────────────┘
         │  compact payload
         ▼
┌─────────────────────────┐
│  External LLM API       │
│  (Copilot / Claude)     │
└────────┬────────────────┘
         │
         ▼
   Forward answer to client
```

**When it applies:** Local resources can't confidently answer. External API is needed, but only receives a surgical, minimal context payload.

**Result:** External API is called, but token usage is a small fraction of naive full-context approaches.

---

## 4. Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| Language | Python 3.11+ | Core implementation |
| API Framework | FastAPI | REST endpoints + async serving |
| MCP Server | `fastmcp` or `mcp` SDK | MCP protocol alongside REST |
| Local LLM | Ollama → `qwen3:14b` | Local inference for queries |
| Embeddings | Ollama → `nomic-embed-text` | Code chunk embeddings |
| Vector DB | ChromaDB | Store and search embedded code chunks |
| Code Parsing | `tree-sitter` | Extract functions/classes from source files |
| Git Integration | Git hooks (post-commit) | Trigger incremental re-indexing |
| Package Manager | `uv` or `pip` | Dependency management |
| Config | TOML / env vars | Runtime configuration |

---

## 5. API Surface

### Base URL
```
http://localhost:8742/v1
```

### REST Endpoints

#### `POST /v1/query`
Submit a query (error, question, code problem). Returns answer + source (local_bug_db / local_llm / external_api).

**Request:**
```json
{
  "query": "TypeError: cannot unpack non-iterable NoneType object",
  "stack_trace": "...",
  "language": "python",
  "project_path": "/Users/user/myproject"
}
```

**Response:**
```json
{
  "answer": "The function returns None when ...",
  "source": "local_llm",
  "context_chunks_used": 3,
  "external_tokens_used": 0
}
```

---

#### `POST /v1/index`
Trigger indexing of a project directory or list of changed files.

**Request:**
```json
{
  "project_path": "/Users/user/myproject",
  "changed_files": ["src/utils.py", "src/parser.py"]
}
```

**Response:**
```json
{
  "indexed_files": 2,
  "chunks_added": 47,
  "duration_ms": 1230
}
```

---

#### `GET /v1/search`
Semantic search of indexed code. Useful for debugging/exploration.

**Query params:** `q=<query>`, `top_k=5`, `project=<path>`

**Response:**
```json
{
  "results": [
    {
      "file": "src/parser.py",
      "function": "parse_response",
      "score": 0.94,
      "snippet": "def parse_response(data):\n    ..."
    }
  ]
}
```

---

#### `POST /v1/bugs`
Save a bug solution to the local repository.

**Request:**
```json
{
  "error_pattern": "TypeError: cannot unpack non-iterable NoneType",
  "solution": "Check for None return before unpacking. Add guard: `if result is None: return`",
  "tags": ["python", "unpacking", "none-check"]
}
```

---

#### `GET /v1/bugs/search`
Search the bug solution repository.

**Query params:** `q=<error_text>`, `top_k=3`

---

#### `GET /v1/status`
Health check + stats (indexed chunks, bug solutions stored, Ollama connectivity).

---

### MCP Tools (exposed via MCP server)

| Tool Name | Description |
|---|---|
| `query_code` | Main query tool — same as `POST /v1/query` |
| `index_path` | Index a project or list of files |
| `search_code` | Semantic code search |
| `save_bug_solution` | Add to local bug repo |
| `search_bug_solutions` | Look up saved solutions |
| `get_status` | Service health and stats |

---

## 6. MCP Integration

CodePal exposes an MCP server alongside the REST API, enabling native integration with MCP-aware clients.

### OpenClaw Integration (via SKILL.md)

A `SKILL.md` is placed in the CodePal skill directory. OpenClaw uses `web_fetch` to call the REST endpoints. The skill documents:
- Service URL and available endpoints
- How to trigger queries, indexing, and bug lookups
- How to interpret response `source` field to decide if external escalation happened

### GitHub Copilot Agent Integration (via `.vscode/mcp.json`)

```json
{
  "mcpServers": {
    "codepal": {
      "url": "http://localhost:8742/mcp",
      "transport": "http"
    }
  }
}
```

Copilot Agent can call CodePal MCP tools directly during agentic coding sessions, using `query_code` before making any external LLM calls.

### MCP Server Implementation Notes

- Run on the same process as FastAPI (using `asyncio` with `anyio` or separate thread)
- MCP endpoint: `http://localhost:8742/mcp` (SSE or streamable HTTP transport)
- Tools mirror REST functionality; no separate business logic

---

## 7. Git Hook Indexing

### Design

CodePal uses a `post-commit` git hook to trigger incremental re-indexing whenever code changes are committed. Only changed files are re-indexed — not the full codebase.

### Hook Script

Installed at `.git/hooks/post-commit`:

```bash
#!/bin/bash
# CodePal post-commit indexing hook

CHANGED_FILES=$(git diff-tree --no-commit-id -r --name-only HEAD)
PROJECT_ROOT=$(git rev-parse --show-toplevel)

if [ -n "$CHANGED_FILES" ]; then
  curl -s -X POST http://localhost:8742/v1/index \
    -H "Content-Type: application/json" \
    -d "{
      \"project_path\": \"$PROJECT_ROOT\",
      \"changed_files\": $(echo "$CHANGED_FILES" | jq -R -s 'split("\n") | map(select(length > 0))')
    }" &
fi
```

### Indexing Pipeline (per file)

```
Source File
     │
     ▼
┌────────────────┐
│  tree-sitter   │  ← parse into AST
│  Code Parser   │
└────────┬───────┘
         │  functions, classes, docstrings
         ▼
┌────────────────┐
│  Chunk Builder │  ← split into indexable units
│                │    (function-level granularity)
└────────┬───────┘
         │  text chunks with metadata
         ▼
┌────────────────┐
│  nomic-embed   │  ← embed each chunk via Ollama
│  -text (Ollama)│
└────────┬───────┘
         │  embedding vectors
         ▼
┌────────────────┐
│  ChromaDB      │  ← upsert (replace old chunks
│  (local store) │    for same file paths)
└────────────────┘
```

### Hook Installation

CodePal provides a setup command to install the hook:

```bash
codepal hooks install --project /path/to/project
```

This copies the hook script into `.git/hooks/post-commit` and sets executable permissions.

### Initial Full Index

On first use, a full index must be triggered manually:

```bash
codepal index /path/to/project
# or via REST:
POST /v1/index { "project_path": "/path/to/project" }
```

---

## 8. Future Work / Out of Scope for MVP

These items are logged for future development but are **not part of the MVP**:

| Feature | Notes |
|---|---|
| CLI indexing trigger | `codepal index ./my-project` — manual full or partial index from terminal |
| File watcher auto-indexing | Watch filesystem for saves, re-index automatically without git commits |
| Multi-project support | Maintain separate ChromaDB collections per project; currently single-project focus |
| Confidence scoring | Explicit scoring on local LLM answers to decide escalation threshold dynamically |
| Escalation routing | Route different query types to different external APIs (Copilot vs Claude) |
| Hosted / online version | Cloud-hosted CodePal service; currently local macOS only |
| Open-source public release | Package, document, and publish for community use |
| Web UI / Dashboard | Usage stats, indexed files browser, bug solution manager |
| Authentication | API keys for REST/MCP in multi-user or network-exposed configurations |
| Language pack expansion | tree-sitter parsers beyond initial target languages |

---

## 9. Key Design Decisions & Rationale

| Decision | Rationale |
|---|---|
| Python + FastAPI | Fast iteration, strong LLM/ML ecosystem, async-native |
| Ollama for both LLM + embeddings | Single local runtime, no API keys, easy model swaps |
| qwen3:14b as local LLM | Strong code reasoning at a size that runs well on macOS (Apple Silicon) |
| nomic-embed-text | High-quality code embeddings, runs efficiently via Ollama |
| ChromaDB | Embedded (no separate server), Python-native, straightforward for local use |
| Function-level chunking | More semantically meaningful than line-based splits; better retrieval precision |
| MCP + REST dual surface | REST for OpenClaw/generic HTTP callers; MCP for native Copilot Agent integration |
| Git hook (post-commit) | Lightweight, zero-dependency trigger; indexing stays in sync with committed code |

---

*End of design document. Questions or revisions? Update this file and increment the version.*
