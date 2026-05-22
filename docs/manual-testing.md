# Manual testing guide

End-to-end procedure for exercising CodePal by hand. Use this when you want
to sanity-check a fresh checkout, a new release, or your local Ollama setup.

The companion fixture [examples/buggy_repo](../examples/buggy_repo/) ships
four intentional bugs that drive the bug-DB and query flows below.

> Throughout this doc the API is assumed to be at `http://127.0.0.1:8742`.
> Adjust if you set `server.port` in your config.

---

## 0. Prerequisites

| Requirement | Why | Check |
|---|---|---|
| Python 3.11+ | Project runtime | `python3 --version` |
| `uv` or a `.venv` + `pip install -e .` | Installs `codepal` console script | `which codepal` |
| Ollama running locally | Embeddings + local LLM path | `curl -s http://localhost:11434/api/tags` |
| `nomic-embed-text` model pulled | Embeddings | `ollama list` |
| `qwen3:14b` (or your `chat_model`) pulled | Path B answers | `ollama list` |

Optional: an `external_llm.api_key` in your config to exercise Path C.

---

## 1. Start the service

```bash
# from the repo root, with your venv active
codepal serve --host 127.0.0.1 --port 8742 --log-level info
```

You should see structured log lines (single-line key=value pairs, ISO
timestamps). A warning is logged — **not** a crash — if Ollama is
unreachable; that is by design.

### 1a. Health check

```bash
curl -s http://127.0.0.1:8742/v1/status | jq
```

Expected:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "ollama_available": true,
  "chroma_available": true
}
```

If `ollama_available` is `false`, start Ollama (`ollama serve`) before
continuing — Paths B and C in §5 will fall back/fail otherwise.

---

## 2. Index the buggy fixture repo

Use the absolute path to `examples/buggy_repo` in *this* checkout.

```bash
# replace REPO_ROOT with your absolute path to the codepal checkout
REPO_ROOT=$(pwd)

curl -s -X POST http://127.0.0.1:8742/v1/index \
  -H 'content-type: application/json' \
  -d "{\"path\": \"${REPO_ROOT}/examples/buggy_repo\", \"project_slug\": \"buggy_repo\"}" | jq
```

Expected: `indexed >= 4`, `errors == []`. A second call should report
`skipped > 0` thanks to the SQLite hash-state cache.

### 2a. Search the indexed code

```bash
curl -s "http://127.0.0.1:8742/v1/search?q=paginate%20items&project_slug=buggy_repo&limit=5" | jq
```

You should see `inventory.py::get_page` near the top of `results`.

Try a few more queries to feel out semantic search:

- `q=mean of a list` → `stats.py::average`
- `q=default argument` → `users.py::add_user`
- `q=extract tag list from payload` → `parser.py::extract_tags`

---

## 3. Seed the bug-solution DB

Each entry corresponds to one of the four bugs in
[examples/buggy_repo/KNOWN_BUGS.md](../examples/buggy_repo/KNOWN_BUGS.md).
Copy-paste the four commands below.

```bash
# Bug #1 — off-by-one
curl -s -X POST http://127.0.0.1:8742/v1/bugs \
  -H 'content-type: application/json' \
  -d '{
    "error": "IndexError: list index out of range when paginating items",
    "context": "return items[start:end + 1]",
    "solution": "Use a half-open slice items[start:end]; end is already exclusive in our pagination contract."
  }' | jq

# Bug #2 — div by zero
curl -s -X POST http://127.0.0.1:8742/v1/bugs \
  -H 'content-type: application/json' \
  -d '{
    "error": "ZeroDivisionError: division by zero in average() when input list is empty",
    "context": "return sum(values) / len(values)",
    "solution": "Guard with: if not values: return 0.0 — or raise a domain-specific EmptyDatasetError."
  }' | jq

# Bug #3 — mutable default
curl -s -X POST http://127.0.0.1:8742/v1/bugs \
  -H 'content-type: application/json' \
  -d '{
    "error": "Stale shared state across calls: roles from a previous call appear in a fresh user",
    "context": "def add_user(name, roles=[]): roles.append(\"guest\")",
    "solution": "Replace mutable default with roles=None, then roles = list(roles) if roles else [] inside the body."
  }' | jq

# Bug #4 — NoneType not iterable
curl -s -X POST http://127.0.0.1:8742/v1/bugs \
  -H 'content-type: application/json' \
  -d '{
    "error": "TypeError: '\''NoneType'\'' object is not iterable when extracting tags",
    "context": "for tag in payload.get(\"tags\"): ...",
    "solution": "payload.get(\"tags\") returns None when missing. Use payload.get(\"tags\") or [] before iterating."
  }' | jq
```

Each call should return `{"id": "<uuid>"}` with HTTP **201**.

### 3a. Search the bug DB

```bash
curl -s "http://127.0.0.1:8742/v1/bugs/search?q=NoneType%20not%20iterable&limit=3" | jq
```

The top result's `error` should match Bug #4 and its `score` should be high
(`>= 0.85` typically — that is the default `dispatcher.bug_score_threshold`).

---

## 4. Exercise the dispatcher (Path A — bug DB hit)

This is the main "debug" feature: ask CodePal an error question and let the
dispatcher route to the bug DB.

```bash
curl -s -X POST http://127.0.0.1:8742/v1/query \
  -H 'content-type: application/json' \
  -d "{
    \"query\": \"I am getting TypeError: NoneType object is not iterable when reading tags from a payload\",
    \"project_path\": \"${REPO_ROOT}/examples/buggy_repo\"
  }" | jq
```

Expected:

```json
{
  "answer": "...the bug DB solution text for Bug #4...",
  "source": "bug_db",
  "context_chunks": [],
  "metadata": { "bug_id": "...", "score": 0.9... }
}
```

The key signal is `"source": "bug_db"`. If you instead see `local_llm` or
`external_llm`, either the score threshold is too high for your embedder, or
the bug was not saved — re-run §3 and verify with §3a.

Try the other three error messages from
[KNOWN_BUGS.md](../examples/buggy_repo/KNOWN_BUGS.md) and confirm each
routes to `bug_db`.

---

## 5. Exercise Paths B and C

### 5a. Path B — local Ollama LLM with code RAG

Ask a question that the bug DB cannot answer but the indexed code can:

```bash
curl -s -X POST http://127.0.0.1:8742/v1/query \
  -H 'content-type: application/json' \
  -d "{
    \"query\": \"How does pagination work in this codebase?\",
    \"project_path\": \"${REPO_ROOT}/examples/buggy_repo\"
  }" | jq
```

Expected: `"source": "local_llm"`, `context_chunks` populated with
`inventory.py` entries, `answer` is the Ollama chat model's response.

### 5b. Path C — external LLM fallback

Requires `[external_llm].api_key` in your config. With no API key set you
should instead get **HTTP 503** ("no answer source available") — that is the
correct behaviour.

```bash
curl -s -X POST http://127.0.0.1:8742/v1/query \
  -H 'content-type: application/json' \
  -d '{
    "query": "explain quantum entanglement in three sentences",
    "project_path": "/tmp"
  }' | jq
```

Expected: `"source": "external_llm"` when a key is configured; `503`
otherwise.

---

## 6. MCP server

The MCP transport is mounted at `/mcp` on the same port and exposes six
tools: `get_status`, `index_path`, `search_code`, `query_code`,
`save_bug_solution`, `search_bug_solutions`.

Quickest sanity check is the included pytest smoke suite:

```bash
.venv/bin/python -m pytest tests/unit/test_mcp.py -q
```

For interactive testing, point any MCP client (e.g. `mcp inspector`) at
`http://127.0.0.1:8742/mcp` and invoke `search_bug_solutions` with one of the
error strings from §3.

---

## 7. CLI smoke test

```bash
codepal --help
codepal index "${REPO_ROOT}/examples/buggy_repo"
codepal search "average of a list"
```

> Note: the CLI `search` command currently formats results using older
> response field names; the underlying API works regardless. The JSON
> contract is what is validated by the test suite.

---

## 8. Git-hook (post-commit) smoke test

```bash
codepal hooks install --project "${REPO_ROOT}/examples/buggy_repo"
cd "${REPO_ROOT}/examples/buggy_repo"
git init -q && git add -A && git commit -m "seed" -q
# touch a file and commit again — the hook should POST to /v1/index with the
# changed file list
echo "# touched" >> src/stats.py
git add -A && git commit -m "touch stats" -q
```

Check the server log for an incoming `POST /v1/index` with `files=[...]`.

---

## 9. Automated regression

After any of the above, the full suite should still pass:

```bash
.venv/bin/python -m pytest tests/unit -q
.venv/bin/python -m pytest tests/integration -q -m "not slow"
# optionally:
.venv/bin/python -m pytest tests/integration -q -m slow
```

Expected: **99 unit + 3 integration** green. The slow indexing benchmark
should complete in well under 60 seconds.

---

## 10. Teardown

```bash
# stop the server with Ctrl-C, then optionally wipe state:
rm -rf ~/.codepal/chroma ~/.codepal/index_state.db
```

Re-running §1 from scratch should rebuild everything cleanly.
