"""FastMCP server — 6 tools mirroring the REST API."""
from __future__ import annotations

from fastapi import FastAPI
from fastmcp import FastMCP

mcp = FastMCP("codepal")


def create_mcp_app(app: FastAPI):
    """Create and configure the MCP ASGI app, injecting FastAPI app state."""

    @mcp.tool()
    async def get_status() -> dict:
        """Check CodePal service health."""
        import httpx

        from codepal.config import get_config
        from codepal.db.chroma import get_chroma_client
        cfg = get_config()
        chroma_ok = True
        ollama_ok = True
        try:
            client = await get_chroma_client(cfg.chroma)
            await client.heartbeat()
        except Exception:
            chroma_ok = False
        try:
            async with httpx.AsyncClient(base_url=cfg.ollama.base_url, timeout=5) as c:
                await c.get("/api/tags")
        except Exception:
            ollama_ok = False
        return {"status": "ok", "ollama_available": ollama_ok, "chroma_available": chroma_ok}

    @mcp.tool()
    async def index_path(path: str) -> dict:
        """Index a directory or list of files."""
        from codepal.config import get_config
        from codepal.db.chroma import get_chroma_client
        from codepal.embeddings.ollama import OllamaEmbedder
        from codepal.indexer.pipeline import IndexerPipeline
        cfg = get_config()
        chroma = await get_chroma_client(cfg.chroma)
        embedder = OllamaEmbedder(cfg.ollama)
        pipeline = IndexerPipeline(chroma=chroma, embedder=embedder, cfg=cfg.indexer)
        await pipeline.init()
        indexed, errors = await pipeline.index_directory(path)
        return {"indexed": indexed, "errors": errors}

    @mcp.tool()
    async def search_code(q: str, limit: int = 5) -> dict:
        """Semantic search over indexed code."""
        import httpx

        from codepal.config import get_config
        cfg = get_config()
        base_url = f"http://{cfg.server.host}:{cfg.server.port}"
        async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
            r = await client.get("/v1/search", params={"q": q, "limit": limit})
            r.raise_for_status()
            return r.json()

    @mcp.tool()
    async def query_code(query: str, project_path: str) -> dict:
        """Query the codebase using the dispatcher (bug DB → local LLM → external LLM)."""
        import httpx

        from codepal.config import get_config
        cfg = get_config()
        base_url = f"http://{cfg.server.host}:{cfg.server.port}"
        async with httpx.AsyncClient(base_url=base_url, timeout=120) as client:
            r = await client.post("/v1/query", json={"query": query, "project_path": project_path})
            r.raise_for_status()
            return r.json()

    @mcp.tool()
    async def save_bug_solution(error: str, solution: str, context: str = "") -> dict:
        """Save a bug and its solution to the bug DB."""
        import httpx

        from codepal.config import get_config
        cfg = get_config()
        base_url = f"http://{cfg.server.host}:{cfg.server.port}"
        async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
            payload = {"error": error, "solution": solution, "context": context or None}
            r = await client.post("/v1/bugs", json=payload)
            r.raise_for_status()
            return r.json()

    @mcp.tool()
    async def search_bug_solutions(q: str, limit: int = 5) -> dict:
        """Search the bug solution database."""
        import httpx

        from codepal.config import get_config
        cfg = get_config()
        base_url = f"http://{cfg.server.host}:{cfg.server.port}"
        async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
            r = await client.get("/v1/bugs/search", params={"q": q, "limit": limit})
            r.raise_for_status()
            return r.json()

    return mcp.http_app()
