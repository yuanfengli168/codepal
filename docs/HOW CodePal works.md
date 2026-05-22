# How CodePal works

> Captured 2026-05-22 from a Q&A session. Two questions answered:
> 1. What can CodePal do (with real examples) — does it reduce premium-API context?
> 2. Does it intercept Copilot automatically, or do I have to call it?

---

## Part 1 — What CodePal does, in plain terms

CodePal is a **local-first code Q&A router**. It sits between your editor (or you on the CLI) and any premium LLM, and tries to answer your question without calling out — only paying for an external API call as a last resort.

### The 3-tier router (and where the savings come from)

When you ask a question, CodePal walks down this ladder. The first tier that succeeds wins:

| Tier | What it tries | Cost | When it fires |
|---|---|---|---|
| **A. Bug DB** | Semantic search over your saved `error → solution` notes (ChromaDB) | $0 | You've hit this bug before and saved the fix |
| **B. Local RAG + local LLM** | Embed your query, retrieve the top-K code chunks from your repo, stuff them into `qwen3:14b` via Ollama | $0 | The answer is "in your codebase" — most refactor / "where is X used" / "why does this function exist" questions |
| **C. External LLM (proxy)** | Forward to Claude/GPT/etc. — **but only with the local RAG chunks already attached**, not your whole repo | small | Genuinely new problem that the local model can't handle |

**Yes — it reduces tokens to premium APIs in two concrete ways:**

1. **Most queries never leave your machine.** Tiers A and B return locally; the external API is never called.
2. **When tier C does fire**, CodePal sends only `query + top-K relevant chunks` (e.g. 3–8 functions, ~2–4 KB), not your whole project. You're paying for ~3 KB of context instead of 300 KB.

### Concrete examples

#### Example 1 — Bug DB hit (saves a whole API call)
You once fixed a `TypeError: cannot unpack non-iterable NoneType` in a parser. You saved it:
```bash
curl -X POST localhost:8742/v1/bugs -d '{
  "error":"TypeError: cannot unpack non-iterable NoneType",
  "context":"name, value = lookup(key)",
  "solution":"Guard with: if result is None: return"
}'
```
Three weeks later you hit the same traceback. You ask:
```bash
codepal search "TypeError unpack NoneType"
```
→ tier A returns the saved solution with `score=0.91`. **Zero external tokens.**

#### Example 2 — "Where is `get_page` used and what does it return?" (local RAG)
```
POST /v1/query  {"query":"how does pagination work in this repo?","project_path":"."}
```
- Embeds your question → retrieves `inventory.py::get_page` and `inventory.py::page_count` from Chroma
- Feeds those two functions (~40 lines) into local `qwen3:14b`
- Returns the answer with `source: "local_llm"` and `context_chunks: [...]`

→ **Zero external tokens.** You'd otherwise have pasted the whole file (or worse, the whole folder) into Claude.

#### Example 3 — Genuinely hard problem (external, but slimmed down)
```
POST /v1/query  {"query":"design a sharded rate limiter for this Redis client","project_path":"."}
```
- Tier B's local model isn't confident
- Tier C fires, but the request to Claude includes **only** the 4 functions CodePal retrieved (`redis_client.py::__init__`, `acquire`, `release`, plus your existing `RateLimiter` class) — not the 200-file repo
- Response comes back tagged `source: "external_llm"`, `metadata.external_tokens` tells you what you spent

→ Maybe 2–3 K input tokens instead of the 50 K+ you'd send by pasting context manually.

#### Example 4 — Auto-reindex on every commit
Install the git hook once:
```bash
codepal hooks install
```
Now every `git commit` calls `POST /v1/index` with just the changed files. Your search index stays fresh without you thinking about it — and tier B keeps working on tomorrow's code.

#### Example 5 — MCP integration (Copilot / Claude desktop / OpenClaw)
CodePal exposes 6 MCP tools at `http://localhost:8742/mcp`:
`query_code`, `search_code`, `index_path`, `save_bug_solution`, `search_bug_solutions`, `get_status`.

In VS Code via `.vscode/mcp.json`, GitHub Copilot Agent can call `search_code` *before* it asks Anthropic anything — so even Copilot's premium calls get shorter context.

### What it explicitly is **not**
- Not a code editor or autocomplete (it complements Copilot; doesn't replace it).
- Not a hosted service — runs entirely on `localhost:8742`.
- Not a general chatbot — it's grounded in *your* repo + *your* saved bug notes.

### TL;DR cost-saving cheat sheet
- Save every non-trivial fix → tier A turns repeat bugs into $0 lookups.
- Ask repo questions through CodePal → tier B keeps them local.
- When you *do* need Claude/GPT, you send ~K tokens of relevant chunks, not megabytes of repo.

---

## Part 2 — Does it auto-intercept Copilot, or do I have to call it?

### Short answer: **you have to wire it up — it doesn't auto-intercept Copilot.**

CodePal is a **server** sitting at `localhost:8742`. Nothing on your machine routes Copilot's traffic through it by default. There are three ways it actually gets used today:

### 1. MCP integration (the closest thing to "automatic")
If you register CodePal as an MCP server in your client, the agent **can** call it on its own — but only because the LLM decides to, not because traffic is intercepted.

**VS Code Copilot Agent** — add to `.vscode/mcp.json`:
```json
{
  "servers": {
    "codepal": { "url": "http://localhost:8742/mcp" }
  }
}
```
Now Copilot Agent sees 6 tools (`query_code`, `search_code`, `save_bug_solution`, …). It will call them *when it judges them relevant* — typically when you ask repo-specific questions. You don't have to type anything special, but the model is the one deciding.

**Caveat:** vanilla Copilot inline completions (the grey ghost text) and Copilot Chat in "Ask" mode do **not** use MCP. Only **Agent mode** does. So:
- Copilot Agent mode ✅ — can auto-call CodePal
- Copilot Chat "Ask" / inline ❌ — bypasses CodePal entirely

### 2. Explicit calls (CLI / HTTP)
You invoke it yourself:
```bash
codepal search "how does pagination work"
curl localhost:8742/v1/query -d '{"query":"...","project_path":"."}'
```
100% under your control, 0% automatic.

### 3. Git hook (the only truly automatic piece)
`codepal hooks install` drops a `post-commit` hook that calls `POST /v1/index` on every commit. That keeps the index fresh — but it's indexing, not query routing.

### What CodePal is **not**
- ❌ Not a proxy that sits in front of `api.openai.com` / `api.anthropic.com`
- ❌ Not a VS Code extension that hooks into Copilot's request pipeline
- ❌ Not invoked by Copilot's inline completion or "Ask" mode

### So how do you actually save tokens in practice?

| Workflow | CodePal involved? | How |
|---|---|---|
| Copilot inline ghost text | No | — |
| Copilot Chat "Ask" mode | No | — |
| **Copilot Chat Agent mode** with `mcp.json` configured | **Yes, automatically** | Agent decides to call `search_code` / `query_code` |
| Claude Desktop / OpenClaw with MCP configured | **Yes, automatically** | Same as above |
| `codepal query` on CLI | Yes | Explicit |
| `curl /v1/query` from your own script | Yes | Explicit |

### Practical recommendation
1. Use Copilot in **Agent mode** with `.vscode/mcp.json` pointing at `http://localhost:8742/mcp`.
2. Phrase questions so the agent recognises they're repo-specific ("in this repo, …", "how does our `X` work?") — that nudges it to call `search_code` before it spends tokens.
3. For inline completions, you're still paying Copilot's normal cost; CodePal can't help there.
