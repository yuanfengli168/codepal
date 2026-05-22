"""Query dispatcher: bug DB → local LLM (Ollama) → external LLM fallback."""

from __future__ import annotations

import logging

import httpx

from codepal.bugs.store import BugStore
from codepal.config import AppConfig
from codepal.db.chroma import ChromaClientWrapper, get_code_collection, query_collection
from codepal.embeddings.ollama import OllamaEmbedder
from codepal.llm.ollama import OllamaChatClient

logger = logging.getLogger(__name__)

def _to_response_chunk(c: dict) -> dict:
    """Map an internal semantic-search chunk to the QueryResponse.CodeChunk shape."""
    return {
        "file": c.get("file_path", c.get("file", "")),
        "symbol": c.get("symbol_name", c.get("node_name", c.get("symbol", ""))),
        "lines": [c.get("start_line", 0), c.get("end_line", 0)],
        "score": float(c.get("score", 0.0)),
        "snippet": c.get("document", c.get("text", c.get("snippet", "")))[:500],
    }


_RAG_PROMPT = """\
You are a senior software engineer acting as a coding assistant.
Use ONLY the code context below to answer the question.
If the context is insufficient, say so clearly.

CONTEXT:
{context}

QUESTION: {question}

ANSWER:"""


class QueryDispatcher:
    def __init__(
        self,
        chroma: ChromaClientWrapper,
        embedder: OllamaEmbedder,
        ollama_client: OllamaChatClient,
        bug_store: BugStore,
        cfg: AppConfig,
    ) -> None:
        self._chroma = chroma
        self._embedder = embedder
        self._ollama = ollama_client
        self._bug_store = bug_store
        self._cfg = cfg

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def dispatch(self, query: str, project_path: str) -> dict:
        """Route a query through the three-path dispatcher.

        Path A — bug DB hit above threshold  → instant cached solution
        Path B — local Ollama available      → RAG via qwen3:14b
        Path C — fallback external LLM API   → proxy with top-3 chunks
        """
        # ── Path A: bug DB ────────────────────────────────────────────
        bug_hits = await self._bug_store.search(query, limit=1)
        if bug_hits and bug_hits[0].score >= self._cfg.dispatcher.bug_score_threshold:
            hit = bug_hits[0]
            return {
                "answer": hit.solution,
                "source": "bug_db",
                "context_chunks": [],
                "metadata": {"bug_id": hit.id, "score": hit.score},
            }

        # Retrieve semantic context for both Path B and C
        chunks = await self._semantic_search(query, project_path, limit=5)
        response_chunks = [_to_response_chunk(c) for c in chunks]

        # ── Path B: local Ollama ──────────────────────────────────────
        if chunks and chunks[0]["score"] >= self._cfg.dispatcher.local_llm_score_threshold:
            try:
                answer = await self._ollama_rag(query, chunks)
                return {
                    "answer": answer,
                    "source": "local_llm",
                    "context_chunks": response_chunks,
                    "metadata": {},
                }
            except httpx.ConnectError:
                logger.warning("Ollama unavailable — falling back to external LLM")
            except Exception as exc:
                logger.warning("Ollama chat failed (%s) — falling back to external LLM", exc)

        # ── Path C: external LLM ──────────────────────────────────────
        if not self._cfg.external_llm.api_key:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=503,
                detail=(
                    "Local LLM is unavailable and no external API key is configured. "
                    "Set CODEPAL_EXTERNAL_LLM__API_KEY or start Ollama."
                ),
            )

        answer = await self._external_chat(query, chunks)
        return {
            "answer": answer,
            "source": "external_llm",
            "context_chunks": response_chunks,
            "metadata": {},
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _semantic_search(
        self, query: str, project_path: str, limit: int = 5
    ) -> list[dict]:
        try:
            collection = await get_code_collection(self._chroma, project_path)
            embedding = await self._embedder.embed(query)
            return await query_collection(
                collection, query_embedding=embedding, n_results=limit
            )
        except Exception as exc:
            logger.debug("Semantic search failed: %s", exc)
            return []

    def _build_context(self, chunks: list[dict]) -> str:
        parts = []
        for c in chunks[:3]:
            file_ = c.get("file_path", c.get("file", ""))
            symbol = c.get("node_name", c.get("symbol", ""))
            snippet = (c.get("document", c.get("snippet", "")))[:500]
            parts.append(f"# {file_} — {symbol}\n{snippet}")
        return "\n\n".join(parts)

    async def _ollama_rag(self, query: str, chunks: list[dict]) -> str:
        context = self._build_context(chunks)
        prompt = _RAG_PROMPT.format(context=context, question=query)
        return await self._ollama.complete(prompt)

    async def _external_chat(self, query: str, chunks: list[dict]) -> str:
        context = self._build_context(chunks)
        prompt = _RAG_PROMPT.format(context=context, question=query)
        cfg = self._cfg.external_llm
        async with httpx.AsyncClient(base_url=cfg.base_url, timeout=120) as client:
            r = await client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {cfg.api_key}"},
                json={
                    "model": cfg.model,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
