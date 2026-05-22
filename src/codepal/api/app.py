"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI

from codepal.api.routes import bugs, index, query, search, status
from codepal.bugs.store import BugStore
from codepal.config import get_config
from codepal.db.chroma import get_chroma_client
from codepal.embeddings.ollama import OllamaEmbedder
from codepal.indexer.pipeline import IndexerPipeline
from codepal.llm.dispatcher import QueryDispatcher
from codepal.llm.ollama import OllamaChatClient
from codepal.logging_config import configure_logging

logger = structlog.get_logger(__name__)


async def _probe_ollama(base_url: str, timeout: float = 3.0) -> bool:
    """Best-effort reachability check for Ollama; logs a warning when unavailable."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"{base_url}/api/tags")
            return r.status_code == 200
    except Exception as exc:
        logger.warning("ollama.probe_failed", base_url=base_url, error=str(exc))
        return False


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialize and tear down shared singletons."""
    configure_logging()
    cfg = get_config()

    # Probe Ollama early — service still starts, but log a clear warning
    ollama_ok = await _probe_ollama(cfg.ollama.base_url)
    if not ollama_ok:
        logger.warning(
            "ollama.unavailable_at_startup",
            base_url=cfg.ollama.base_url,
            note="Local LLM path B will fail until Ollama is reachable.",
        )

    # ChromaDB
    chroma = await get_chroma_client(cfg.chroma)

    # Ollama clients
    embedder = OllamaEmbedder(cfg.ollama)
    ollama_client = OllamaChatClient(cfg.ollama)

    # Bug store
    bug_store = BugStore(chroma=chroma, embedder=embedder)
    await bug_store.init()

    # Indexer pipeline
    pipeline = IndexerPipeline(chroma=chroma, embedder=embedder, cfg=cfg.indexer)
    await pipeline.init()

    # Query dispatcher (uses OllamaChatClient for Path B)
    dispatcher = QueryDispatcher(
        chroma=chroma,
        embedder=embedder,
        ollama_client=ollama_client,
        bug_store=bug_store,
        cfg=cfg,
    )

    # Attach to app state for dependency injection
    app.state.chroma = chroma
    app.state.embedder = embedder
    app.state.ollama_client = ollama_client
    app.state.bug_store = bug_store
    app.state.pipeline = pipeline
    app.state.dispatcher = dispatcher
    app.state.config = cfg

    logger.info(
        "codepal.startup_complete",
        ollama_available=ollama_ok,
        host=cfg.server.host,
        port=cfg.server.port,
    )

    try:
        yield
    finally:
        # Graceful shutdown: close httpx clients then drop state
        logger.info("codepal.shutdown")
        try:
            await embedder.close()
        except Exception as exc:
            logger.warning("shutdown.embedder_close_failed", error=str(exc))
        try:
            await ollama_client.close()
        except Exception as exc:
            logger.warning("shutdown.chat_close_failed", error=str(exc))


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(
        title="CodePal",
        version="0.1.0",
        description="Local AI coding assistant",
        lifespan=lifespan,
    )

    # Enable CORS for browser sidecar and localhost
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost",
            "http://localhost:8742",
            "https://yuanfengli168.github.io"
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routers
    app.include_router(status.router, prefix="/v1")
    app.include_router(query.router, prefix="/v1")
    app.include_router(index.router, prefix="/v1")
    app.include_router(search.router, prefix="/v1")
    app.include_router(bugs.router, prefix="/v1")

    # Mount MCP server
    from codepal.mcp_server import create_mcp_app

    mcp_app = create_mcp_app(app)
    app.mount("/mcp", mcp_app)

    return app
