"""GET /v1/search — semantic search over indexed code chunks."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from codepal.api.models import SearchResponse

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse)
async def search_code(
    request: Request,
    q: str = Query(..., description="Search query string"),
    limit: int = Query(5, ge=1, le=50, description="Max results to return"),
) -> SearchResponse:
    """Return semantically similar code chunks for the given query."""
    pipeline = request.app.state.pipeline
    results = await pipeline.search(query=q, limit=limit)
    return SearchResponse(results=results)
