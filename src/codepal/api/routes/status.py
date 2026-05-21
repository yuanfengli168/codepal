"""GET /v1/status — health check endpoint."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request

from codepal.api.models import StatusResponse

router = APIRouter(tags=["status"])


@router.get("/status", response_model=StatusResponse)
async def get_status(request: Request) -> StatusResponse:
    """Return service health including Ollama and ChromaDB availability."""
    cfg = request.app.state.config

    # Check Ollama availability
    ollama_available = False
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{cfg.ollama.base_url}/api/tags")
            ollama_available = resp.status_code == 200
    except Exception:
        pass

    # Check ChromaDB availability
    chroma_available = False
    try:
        chroma = request.app.state.chroma
        # A simple heartbeat: list collections
        await chroma.list_collections()
        chroma_available = True
    except Exception:
        pass

    return StatusResponse(
        status="ok",
        ollama_available=ollama_available,
        chroma_available=chroma_available,
    )
