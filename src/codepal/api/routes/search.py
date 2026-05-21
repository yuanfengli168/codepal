"""GET /v1/search — semantic search over indexed code chunks."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from codepal.api.models import SearchResponse, SearchResult

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse)
async def search_code(
    request: Request,
    q: str = Query(..., description="Search query string"),
    limit: int = Query(5, ge=1, le=50, description="Max results to return"),
    project_slug: str = Query(
        "project",
        description="Project slug (ChromaDB collection suffix). "
        "Defaults to 'project' which searches the default collection.",
    ),
) -> SearchResponse:
    """Return semantically similar code chunks for the given query.

    Returns HTTP 200 with an empty ``results`` list when no matches are found.
    """
    pipeline = request.app.state.pipeline
    raw = await pipeline.search(query=q, project_slug=project_slug, limit=limit)

    results = [
        SearchResult(
            file_path=r["file_path"],
            symbol_name=r["symbol_name"],
            score=r["score"],
            text=r["text"],
            start_line=r["start_line"],
            end_line=r["end_line"],
        )
        for r in raw
    ]
    return SearchResponse(results=results)
