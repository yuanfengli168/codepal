"""Pydantic request/response models for all API endpoints."""

from __future__ import annotations

from typing import Any, List, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

class StatusResponse(BaseModel):
    status: str
    ollama_available: bool
    chroma_available: bool


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

class IndexRequest(BaseModel):
    path: Optional[str] = Field(None, description="Absolute path to a directory to index")
    files: Optional[List[str]] = Field(None, description="List of specific file paths to index")


class IndexResponse(BaseModel):
    indexed: int
    errors: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class CodeChunk(BaseModel):
    file: str
    symbol: str
    lines: List[int] = Field(..., description="[start_line, end_line]")
    score: float
    snippet: str


class SearchResponse(BaseModel):
    results: List[CodeChunk]


# ---------------------------------------------------------------------------
# Bugs
# ---------------------------------------------------------------------------

class BugSaveRequest(BaseModel):
    error: str = Field(..., description="Error message or description")
    context: Optional[str] = Field(None, description="Code context where the bug occurred")
    solution: str = Field(..., description="How the bug was resolved")


class BugSaveResponse(BaseModel):
    id: str


class BugSearchResult(BaseModel):
    id: str
    score: float
    error: str
    solution: str
    context: Optional[str] = None


class BugSearchResponse(BaseModel):
    results: List[BugSearchResult]


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str = Field(..., description="The question or query about the codebase")
    project_path: str = Field(..., description="Absolute path to the project root")


class QueryResponse(BaseModel):
    answer: str
    source: str = Field(..., description="One of: bug_db, local_llm, external_llm")
    context_chunks: List[CodeChunk] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
