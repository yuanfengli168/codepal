"""Pydantic request/response models for all API endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class StatusResponse(BaseModel):
    status: str
    version: str = "0.1.0"
    ollama_available: bool
    chroma_available: bool


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


class IndexRequest(BaseModel):
    path: str | None = Field(None, description="Absolute path to a directory to index")
    files: list[str] | None = Field(None, description="List of specific file paths to index")
    project_slug: str | None = Field(
        None,
        description="Optional slug to name the ChromaDB collection. "
        "Derived from path basename when omitted.",
    )


class IndexResponse(BaseModel):
    indexed: int = Field(..., description="Number of chunks successfully indexed")
    skipped: int = Field(0, description="Files skipped because they are unchanged")
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class SearchResult(BaseModel):
    file_path: str
    symbol_name: str
    score: float
    text: str
    start_line: int = 0
    end_line: int = 0


class SearchResponse(BaseModel):
    results: list[SearchResult]


# ---------------------------------------------------------------------------
# Bugs
# ---------------------------------------------------------------------------


class BugSaveRequest(BaseModel):
    error: str = Field(..., description="Error message or description")
    context: str | None = Field(None, description="Code context where the bug occurred")
    solution: str = Field(..., description="How the bug was resolved")


class BugSaveResponse(BaseModel):
    id: str


class BugSearchResult(BaseModel):
    id: str
    score: float
    error: str
    solution: str
    context: str | None = None


class BugSearchResponse(BaseModel):
    results: list[BugSearchResult]


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


class QueryRequest(BaseModel):
    query: str = Field(..., description="The question or query about the codebase")
    project_path: str = Field(..., description="Absolute path to the project root")


class CodeChunk(BaseModel):
    """Used in QueryResponse context_chunks."""

    file: str
    symbol: str
    lines: list[int] = Field(..., description="[start_line, end_line]")
    score: float
    snippet: str


class QueryResponse(BaseModel):
    answer: str
    source: str = Field(..., description="One of: bug_db, local_llm, external_llm")
    context_chunks: list[CodeChunk] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
