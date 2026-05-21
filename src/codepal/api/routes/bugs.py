"""POST /v1/bugs + GET /v1/bugs/search — bug solution storage and retrieval."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status

from codepal.api.models import BugSaveRequest, BugSaveResponse, BugSearchResponse

router = APIRouter(tags=["bugs"])


@router.post("/bugs", response_model=BugSaveResponse, status_code=status.HTTP_201_CREATED)
async def save_bug(request: Request, body: BugSaveRequest) -> BugSaveResponse:
    """Persist a bug + solution to the ChromaDB bug solutions collection."""
    bug_store = request.app.state.bug_store
    bug_id = await bug_store.save(
        error=body.error,
        context=body.context,
        solution=body.solution,
    )
    return BugSaveResponse(id=bug_id)


@router.get("/bugs/search", response_model=BugSearchResponse)
async def search_bugs(
    request: Request,
    q: str = Query(..., description="Error text to search for"),
    limit: int = Query(5, ge=1, le=20),
) -> BugSearchResponse:
    """Return the closest matching bug solutions for the given error query."""
    bug_store = request.app.state.bug_store
    results = await bug_store.search(query=q, limit=limit)
    return BugSearchResponse(results=results)
