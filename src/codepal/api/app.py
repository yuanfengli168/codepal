"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from codepal.api.routes import bugs, index, query, search, status
from codepal.config import get_config
from codepal.db.chroma import get_chroma_client
from codepal.embeddings.ollama import OllamaEmbedder
from codepal.indexer.pipeline import IndexerPipeline
from codepal.llm.dispatcher import QueryDispatcher
from codepal.bugs.store import BugStore


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialize and tear down shared singletons."""
    cfg = get_config()

    # Initialize ChromaDB client
    chroma = await get_chroma_client(cfg.chroma)

    # Initialize embedder
    embedder = OllamaEmbedder(cfg.ollama)

    # Initialize bug store
    bug_store = BugStore(chroma=chroma, embedder=embedder)
    await bug_store.init()

    # Initialize indexer pipeline
    pipeline = IndexerPipeline(
        chroma=chroma,
        embedder=embedder,
        cfg=cfg.indexer,
    )
    await pipeline.init()

    # Initialize query dispatcher
    dispatcher = QueryDispatcher(
        chroma=chroma,
        embedder=embedder,
        bug_store=bug_store,
        cfg=cfg,
    )

    # Attach to app state for dependency injection
    app.state.chroma = chroma
    app.state.embedder = embedder
    app.state.bug_store = bug_store
    app.state.pipeline = pipeline
    app.state.dispatcher = dispatcher
    app.state.config = cfg

    yield

    # Cleanup (ChromaDB client doesn't need explicit close, but good practice)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    cfg = get_config()

    app = FastAPI(
        title="CodePal",
        version="0.1.0",
        description="Local AI coding assistant",
        lifespan=lifespan,
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
