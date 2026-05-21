"""POST /v1/index — index a project directory or list of files."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from codepal.api.models import IndexRequest, IndexResponse

router = APIRouter(tags=["index"])


def _derive_slug(body: IndexRequest) -> str:
    """Derive a project slug from the request body."""
    raw = (
        body.project_slug
        or (Path(body.path).name if body.path else None)
        or (Path(body.files[0]).parent.name if body.files else "project")
    )
    return re.sub(r"[^a-z0-9_]", "_", (raw or "project").lower())[:40].strip("_") or "project"


@router.post("/index", response_model=IndexResponse)
async def index_code(request: Request, body: IndexRequest) -> IndexResponse:
    """Index a codebase or a list of specific files.

    - Provide ``path`` for a full directory scan.
    - Provide ``files`` for incremental indexing (e.g. from the git hook).
    - ``project_slug`` is optional; derived from path/file basename when omitted.
    - ``files`` takes precedence over ``path``.
    """
    if not body.path and not body.files:
        raise HTTPException(status_code=422, detail="Must provide 'path' or 'files'.")

    pipeline = request.app.state.pipeline
    slug = _derive_slug(body)

    if body.files:
        result = await pipeline._index_files([Path(f) for f in body.files], slug)
    else:
        result = await pipeline.index_path(Path(body.path), slug)  # type: ignore[arg-type]

    return IndexResponse(
        indexed=result["indexed"],
        skipped=result["skipped"],
        errors=result["errors"],
    )
