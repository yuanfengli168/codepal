# How to Automatically use CodePal

> Captured 2026-05-22. Three options for getting external-LLM traffic
> through CodePal automatically, plus where each one lands in practice.

## Reality check up front

- **For GitHub Copilot itself: not possible transparently.** Copilot pins
  to `*.githubcopilot.com`, ignores `HTTPS_PROXY`, and the extension is
  closed source. The closest you can get is Copilot **Agent mode** + MCP
  + a strict system prompt (Option 3b below).
- **For every other tool that lets you set a base URL** (Cursor,
  Continue.dev, Aider, Cline, Roo Code, LangChain, custom scripts,
  OpenClaw, Hermes, etc.): yes, fully feasible.

---

## Option 1 — OpenAI-compatible shim ⭐ recommended

Add a new endpoint: `POST /v1/chat/completions` that mimics OpenAI's
schema. CodePal runs the tier A → B → C ladder internally and only
forwards to the real provider on cache miss.

**How it lands:**
- Any tool that accepts `OPENAI_BASE_URL` (or `ANTHROPIC_BASE_URL`)
  gets retrofitted by one env var:
  ```bash
  export OPENAI_BASE_URL=http://localhost:8742/v1
  export OPENAI_API_KEY=anything
  ```
- Works with **Cursor, Continue.dev, Aider, Cline, Roo Code,
  LangChain, LiteLLM, OpenAI/Anthropic SDKs, custom Python scripts**.
- CodePal intercepts the request, injects retrieved chunks, then
  either answers locally or forwards a slimmed-down payload to the
  real upstream using your real key.

**Pros:** clean, no TLS hackery, streaming works, OpenAI chat/completions
is the de-facto standard protocol.
**Cons:** doesn't help Copilot itself; for native Anthropic clients you'd
also need to speak `/v1/messages`.
**Effort:** ~1–2 days. Add `api/routes/openai_compat.py` + tests; reuse
`QueryDispatcher`.

---

## Option 2 — Local TLS-terminating proxy (mitmproxy / Caddy)

Run a local HTTPS proxy that terminates TLS for `api.openai.com`,
`api.anthropic.com`, `generativelanguage.googleapis.com`, runs the
request through CodePal, then forwards.

**How it lands:**
- Install a local CA cert into the system keychain.
- `export HTTPS_PROXY=http://localhost:8743`.
- mitmproxy hands the request to a CodePal addon → tier A returns
  immediately, tier B answers locally, tier C rewrites the body to
  drop irrelevant context, then forwards upstream.

**Pros:** truly transparent — any tool honoring system proxy gets
intercepted.
**Cons:** TLS trust setup is a footgun; streaming responses (SSE) need
careful handling; **Copilot still won't go through it** (pins certs,
ignores proxy); fragile across OS updates.
**Effort:** ~3–5 days. New `codepal/proxy/` module + mitmproxy addon +
setup docs + security warning.

---

## Option 3 — Drop-in SDK wrappers + "must-call-tool" MCP contract

Two complementary pieces for the cases Options 1 & 2 don't cover.

### 3a. SDK shims

Tiny wrappers:
```python
from codepal.openai import OpenAI    # behaves like openai.OpenAI
client = OpenAI()                    # routes through localhost:8742 first
client.chat.completions.create(...)  # tier A/B/C, then real OpenAI
```
Same for `anthropic`. For your own scripts/agents this is one-line
adoption.

### 3b. Strict MCP system prompt — covers OpenClaw / Hermes / Claude Desktop / Copilot Agent

For agent-mode clients, ship an `AGENTS.md` / `SKILL.md` / system prompt
that says:

> "Before answering any code question, you **must** call
> `codepal.search_code` and `codepal.search_bug_solutions` first. Only
> call external reasoning if both return `score < 0.6`."

The model still *chooses* to comply, but with a firm instruction
compliance is ~95%. Combined with telemetry (`metadata.tier_taken`),
you can see when it skipped.

**Note (2026-05-22):** Jacky plans to use this with **OpenClaw** —
ship a `SKILL.md` that points at `http://localhost:8742/mcp` and
enforces the "search CodePal first" contract. With OpenClaw this is
effectively the recommended path; Option 1 is for clients that don't
speak MCP.

**Pros:** zero infrastructure, works inside Copilot Agent mode (closest
you can get to automatic Copilot interception).
**Cons:** SDK wrapper only helps code you control; system-prompt
enforcement is soft.
**Effort:** ~half a day each.

---

## Recommended landing order

1. **Option 1 (OpenAI-compat shim)** — biggest reach, smallest effort,
   no TLS pain. Single highest-leverage feature to add next.
2. **Option 3b (strict MCP contract / OpenClaw SKILL.md)** — half a day;
   already on Jacky's path for OpenClaw + Hermes.
3. **Option 2 (TLS proxy)** — only if users refuse env vars and use
   tools that don't speak OpenAI's schema. High maintenance burden;
   defer until demanded.

For Copilot specifically, accept reality: use **Agent mode + MCP +
strict system prompt** (Option 3b). Full transparent interception isn't
possible without forking the extension. A separate browser-side
"side-car page" approach is being scoped in `docs/copilot-sidecar-page-design.md`.
