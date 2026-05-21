"""Query dispatcher: bug DB → local LLM → external LLM fallback."""
from __future__ import annotations
import httpx
from chromadb import AsyncClientAPI
from codepal.config import AppConfig
from codepal.db.chroma import get_code_collection
from codepal.embeddings.ollama import OllamaEmbedder
from codepal.bugs.store import BugStore


class QueryDispatcher:
    def __init__(
        self,
        chroma: AsyncClientAPI,
        embedder: OllamaEmbedder,
        bug_store: BugStore,
        cfg: AppConfig,
    ) -> None:
        self._chroma = chroma
        self._embedder = embedder
        self._bug_store = bug_store
        self._cfg = cfg

    async def dispatch(self, query: str, project_path: str) -> dict:
        # Path A: bug DB
        bug_hits = await self._bug_store.search(query, limit=1)
        if bug_hits and bug_hits[0]["score"] >= self._cfg.dispatcher.bug_score_threshold:
            hit = bug_hits[0]
            return {
                "answer": hit["solution"],
                "source": "bug_db",
                "context_chunks": [],
                "metadata": {"bug_id": hit["id"], "score": hit["score"]},
            }

        # Retrieve code context
        chunks = await self._semantic_search(query, project_path, limit=5)

        # Path B: local LLM via Ollama
        if chunks and chunks[0]["score"] >= self._cfg.dispatcher.local_llm_score_threshold:
            try:
                answer = await self._ollama_chat(query, chunks)
                return {"answer": answer, "source": "local_llm", "context_chunks": chunks, "metadata": {}}
            except Exception:
                pass

        # Path C: external LLM
        if not self._cfg.external_llm.api_key:
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail="External LLM API key not configured and local LLM unavailable.")

        answer = await self._external_chat(query, chunks)
        return {"answer": answer, "source": "external_llm", "context_chunks": chunks, "metadata": {}}

    async def _semantic_search(self, query: str, project_path: str, limit: int = 5) -> list[dict]:
        try:
            collection = await get_code_collection(self._chroma, project_path)
            embedding = await self._embedder.embed(query)
            results = await collection.query(query_embeddings=[embedding], n_results=limit, include=["metadatas", "distances", "documents"])
            out = []
            for i, meta in enumerate(results["metadatas"][0]):
                distance = results["distances"][0][i]
                out.append({
                    "file": meta.get("file_path", ""),
                    "symbol": meta.get("node_name", ""),
                    "lines": [meta.get("start_line", 0), meta.get("end_line", 0)],
                    "score": 1.0 - distance,
                    "snippet": (results["documents"][0][i] or "")[:400],
                })
            return out
        except Exception:
            return []

    async def _ollama_chat(self, query: str, chunks: list[dict]) -> str:
        context = "\n\n".join(f"File: {c['file']} ({c['symbol']})\n{c['snippet']}" for c in chunks[:3])
        prompt = f"You are a coding assistant. Use the following code context to answer the question.\n\nCONTEXT:\n{context}\n\nQUESTION: {query}\n\nANSWER:"
        cfg = self._cfg.ollama
        async with httpx.AsyncClient(base_url=cfg.base_url, timeout=cfg.chat_timeout) as client:
            r = await client.post("/api/chat", json={
                "model": cfg.chat_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            })
            r.raise_for_status()
            return r.json()["message"]["content"]

    async def _external_chat(self, query: str, chunks: list[dict]) -> str:
        context = "\n\n".join(f"File: {c['file']} ({c['symbol']})\n{c['snippet']}" for c in chunks[:3])
        prompt = f"You are a coding assistant. Use the following code context to answer the question.\n\nCONTEXT:\n{context}\n\nQUESTION: {query}"
        cfg = self._cfg.external_llm
        async with httpx.AsyncClient(base_url=cfg.base_url, timeout=120) as client:
            r = await client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {cfg.api_key}"},
                json={"model": cfg.model, "messages": [{"role": "user", "content": prompt}]},
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
