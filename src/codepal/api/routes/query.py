"""POST /v1/query — route query through bug DB, local LLM, or smart proxy."""

from __future__ import annotations

from fastapi import APIRouter, Request

from codepal.api.models import QueryRequest, QueryResponse

router = APIRouter(tags=["query"])


@router.post("/query", response_model=QueryResponse)
async def query_code(request: Request, body: QueryRequest) -> QueryResponse:
    """
    Route the query through:
      - Path A: Bug DB hit (score >= threshold) → instant answer
      - Path B: Local Ollama LLM with RAG context
      - Path C: External LLM smart proxy fallback
    """
    dispatcher = request.app.state.dispatcher
    return await dispatcher.dispatch(query=body.query, project_path=body.project_path)
