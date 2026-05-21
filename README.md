# CodePal

**Local AI coding assistant with semantic search, bug DB, and MCP server.**

CodePal runs entirely on your machine. It indexes your codebase, stores bug solutions, and answers coding questions using a local LLM (Ollama) — with an optional smart fallback to external APIs.

---

## Features

- 🔍 **Semantic code search** — find code by meaning, not just text
- 🐛 **Bug solution DB** — save and retrieve past solutions with vector similarity
- 🤖 **Query routing** — bug DB → local Ollama LLM → external LLM fallback
- 🔌 **MCP server** — all tools available to Claude Desktop and other MCP clients
- 🪝 **Git hook** — auto-index changed files on every commit

---

## Requirements

- Python 3.11+
- [Ollama](https://ollama.ai) running with models pulled:
  ```bash
  ollama pull nomic-embed-text
  ollama pull qwen3:14b
  ```

---

## Install

```bash
# Clone and install
git clone https://github.com/yuanfengli168/codepal.git
cd codepal
pip install -e ".[dev]"
```

---

## Start the Service

```bash
codepal serve
# or directly:
uvicorn codepal.main:app --host 127.0.0.1 --port 8742
```

The service starts at `http://127.0.0.1:8742`.

---

## Index Your Project

```bash
codepal index /path/to/your/project
# or via API:
curl -X POST http://127.0.0.1:8742/v1/index \
  -H 'Content-Type: application/json' \
  -d '{"path": "/path/to/your/project"}'
```

---

## Query Your Codebase

```bash
curl -X POST http://127.0.0.1:8742/v1/query \
  -H 'Content-Type: application/json' \
  -d '{"query": "How does authentication work?", "project_path": "/path/to/project"}'
```

---

## Install Git Hook

```bash
codepal hooks install --project /path/to/your/project
```

After each commit, changed files are automatically re-indexed.

---

## Configure External LLM (Optional)

Copy `config.toml.example` to `~/.codepal/codepal.toml` and set:

```toml
[external_llm]
api_key = "sk-..."
base_url = "https://api.openai.com/v1"
model = "gpt-4o"
```

---

## MCP Setup (Claude Desktop)

Add to your Claude Desktop MCP config:

```json
{
  "mcpServers": {
    "codepal": {
      "url": "http://127.0.0.1:8742/mcp"
    }
  }
}
```

Available MCP tools: `query_code`, `index_path`, `search_code`, `save_bug_solution`, `search_bug_solutions`, `get_status`

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/v1/status` | Health check |
| `POST` | `/v1/index` | Index a directory or files |
| `GET` | `/v1/search?q=...` | Semantic code search |
| `POST` | `/v1/query` | Ask a question about the codebase |
| `POST` | `/v1/bugs` | Save a bug solution |
| `GET` | `/v1/bugs/search?q=...` | Search bug solutions |

---

## Development

```bash
# Run tests
pytest

# Lint
ruff check src/

# Type check
mypy src/codepal/
```

---

*Runs on macOS (arm64 + x86_64). MVP v0.1*
