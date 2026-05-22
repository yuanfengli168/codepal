"""FastMCP server — 6 tools that call the same service layer as REST handlers.

All tools call into ``app.state`` singletons created by the FastAPI lifespan
(see ``codepal.api.app.create_app``). This guarantees zero business-logic
duplication between REST and MCP transports.
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastmcp import FastMCP

logger = logging.getLogger(__name__)


def create_mcp_app(app: FastAPI):
    """Create and configure the MCP ASGI app bound to the FastAPI ``app.state``."""

    mcp = FastMCP("codepal")

    def _state():
        return app.state

    @mcp.tool()
    async def get_status() -> dict:
        """Check CodePal service health (Ollama + ChromaDB)."""
        state = _state()
        cfg = state.config
        ollama_ok = False
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{cfg.ollama.base_url}/api/tags")
                ollama_ok = r.status_code == 200
        except Exception:
            pass
        chroma_ok = False
        try:
            await state.chroma.list_collections()
            chroma_ok = True
        except Exception:
            pass
        return {
            "status": "ok",
            "ollama_available": ollama_ok,
            "chroma_available": chroma_ok,
        }

    @mcp.tool()
    async def index_path(path: str, project_slug: str | None = None) -> dict:
        """Index a directory in-place using the shared pipeline."""
        pipeline = _state().pipeline
        slug = project_slug or Path(path).name.lower().replace(" ", "_") or "project"
        result = await pipeline.index_path(Path(path), slug)
        return {
            "indexed": result["indexed"],
            "skipped": result.get("skipped", 0),
            "errors": result.get("errors", []),
        }

    @mcp.tool()
    async def search_code(q: str, limit: int = 5, project_slug: str = "project") -> dict:
        """Semantic search over indexed code via the shared pipeline."""
        pipeline = _state().pipeline
        results = await pipeline.search(query=q, project_slug=project_slug, limit=limit)
        return {"results": results}

    @mcp.tool()
    async def query_code(query: str, project_path: str) -> dict:
        """Route a query through bug DB → local LLM → external LLM."""
        return await _state().dispatcher.dispatch(query=query, project_path=project_path)

    @mcp.tool()
    async def save_bug_solution(error: str, solution: str, context: str = "") -> dict:
        """Save a bug + solution pair to the local ChromaDB collection."""
        bug_id = await _state().bug_store.save(
            error=error, solution=solution, context=context or None
        )
        return {"id": bug_id}

    @mcp.tool()
    async def search_bug_solutions(q: str, limit: int = 5) -> dict:
        """Search the bug solution database."""
        results = await _state().bug_store.search(query=q, limit=limit)
        return {
            "results": [
                {
                    "id": r.id,
                    "score": r.score,
                    "error": r.error,
                    "solution": r.solution,
                    "context": r.context,
                }
                for r in results
            ]
        }

    return mcp.http_app()
