"""POST /v1/index — index a project or specific files."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from codepal.api.models import IndexRequest, IndexResponse

router = APIRouter(tags=["index"])


@router.post("/index", response_model=IndexResponse)
async def index_code(request: Request, body: IndexRequest) -> IndexResponse:
    """
    Index a codebase or a list of specific files.

    - Provide `path` for a full directory scan.
    - Provide `files` for incremental indexing (e.g. from git hook).
    - Both `path` and `files` are mutually exclusive; `files` takes precedence.
    """
    if not body.path and not body.files:
        raise HTTPException(status_code=422, detail="Must provide 'path' or 'files'")

    pipeline = request.app.state.pipeline
    return await pipeline.run(path=body.path, files=body.files)
