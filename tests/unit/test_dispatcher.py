"""Unit tests for the three-path query dispatcher.

Mocks the bug store, embedder, chroma collection, and Ollama chat client
so the routing logic can be exercised without any external services.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastapi import HTTPException

from codepal.api.models import BugSearchResult
from codepal.llm.dispatcher import QueryDispatcher, _to_response_chunk


def _cfg(*, bug_threshold=0.85, local_threshold=0.6, api_key: str | None = None):
    return SimpleNamespace(
        dispatcher=SimpleNamespace(
            bug_score_threshold=bug_threshold,
            local_llm_score_threshold=local_threshold,
        ),
        external_llm=SimpleNamespace(
            api_key=api_key,
            base_url="https://api.example.com/v1",
            model="gpt-test",
        ),
    )


def _make_dispatcher(
    *,
    bug_hits: list[BugSearchResult] | None = None,
    chunks: list[dict] | None = None,
    ollama_reply: str | Exception = "local-answer",
    cfg=None,
) -> QueryDispatcher:
    bug_store = SimpleNamespace(search=AsyncMock(return_value=bug_hits or []))
    embedder = SimpleNamespace(embed=AsyncMock(return_value=[0.1, 0.2, 0.3]))
    ollama = SimpleNamespace(
        complete=AsyncMock(
            side_effect=ollama_reply
            if isinstance(ollama_reply, Exception)
            else None,
            return_value=None
            if isinstance(ollama_reply, Exception)
            else ollama_reply,
        )
    )
    chroma = SimpleNamespace()
    disp = QueryDispatcher(
        chroma=chroma,
        embedder=embedder,
        ollama_client=ollama,
        bug_store=bug_store,
        cfg=cfg or _cfg(),
    )
    # Force _semantic_search to return canned chunks (skip Chroma)
    disp._semantic_search = AsyncMock(return_value=chunks or [])  # type: ignore[method-assign]
    return disp


# ---------------------------------------------------------------------------
# _to_response_chunk
# ---------------------------------------------------------------------------


def test_to_response_chunk_maps_pipeline_shape():
    out = _to_response_chunk(
        {
            "file_path": "src/foo.py",
            "symbol_name": "bar",
            "score": 0.91,
            "document": "def bar(): ...",
            "start_line": 10,
            "end_line": 12,
        }
    )
    assert out == {
        "file": "src/foo.py",
        "symbol": "bar",
        "lines": [10, 12],
        "score": 0.91,
        "snippet": "def bar(): ...",
    }


def test_to_response_chunk_truncates_snippet():
    big = "x" * 1000
    out = _to_response_chunk({"document": big})
    assert len(out["snippet"]) == 500


# ---------------------------------------------------------------------------
# Path A — bug DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_path_a_bug_db_hit():
    hit = BugSearchResult(
        id="abc123",
        score=0.95,
        error="ZeroDivisionError",
        solution="Don't divide by zero",
        context=None,
    )
    disp = _make_dispatcher(bug_hits=[hit])
    out = await disp.dispatch("how do I fix div by zero?", project_path="/x")
    assert out["source"] == "bug_db"
    assert out["answer"] == "Don't divide by zero"
    assert out["metadata"]["bug_id"] == "abc123"
    assert out["metadata"]["score"] == 0.95
    assert out["context_chunks"] == []


@pytest.mark.asyncio
async def test_path_a_score_below_threshold_falls_through():
    low_hit = BugSearchResult(
        id="abc", score=0.5, error="x", solution="y", context=None
    )
    disp = _make_dispatcher(
        bug_hits=[low_hit],
        chunks=[{"file_path": "f.py", "score": 0.9, "document": "code"}],
    )
    out = await disp.dispatch("q", project_path="/x")
    assert out["source"] == "local_llm"


# ---------------------------------------------------------------------------
# Path B — local Ollama RAG
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_path_b_local_llm_hit():
    chunks = [
        {
            "file_path": "src/foo.py",
            "symbol_name": "bar",
            "score": 0.88,
            "document": "def bar(): return 1",
            "start_line": 1,
            "end_line": 2,
        }
    ]
    disp = _make_dispatcher(chunks=chunks, ollama_reply="bar returns 1")
    out = await disp.dispatch("what does bar do?", project_path="/x")
    assert out["source"] == "local_llm"
    assert out["answer"] == "bar returns 1"
    assert len(out["context_chunks"]) == 1
    assert out["context_chunks"][0]["file"] == "src/foo.py"
    assert out["context_chunks"][0]["lines"] == [1, 2]


@pytest.mark.asyncio
async def test_path_b_falls_back_when_ollama_unavailable():
    chunks = [{"file_path": "f.py", "score": 0.9, "document": "code"}]
    disp = _make_dispatcher(
        chunks=chunks,
        ollama_reply=httpx.ConnectError("conn refused"),
        cfg=_cfg(api_key="sk-test"),
    )
    with respx.mock(base_url="https://api.example.com/v1") as r:
        r.post("/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "remote-answer"}}]
                },
            )
        )
        out = await disp.dispatch("q", project_path="/x")
    assert out["source"] == "external_llm"
    assert out["answer"] == "remote-answer"


@pytest.mark.asyncio
async def test_path_b_skipped_when_no_chunks():
    disp = _make_dispatcher(chunks=[], cfg=_cfg(api_key="sk-test"))
    with respx.mock(base_url="https://api.example.com/v1") as r:
        r.post("/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": "no-context-answer"}}]},
            )
        )
        out = await disp.dispatch("q", project_path="/x")
    assert out["source"] == "external_llm"


# ---------------------------------------------------------------------------
# Path C — external LLM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_path_c_503_when_no_api_key():
    disp = _make_dispatcher(chunks=[], cfg=_cfg(api_key=None))
    with pytest.raises(HTTPException) as exc:
        await disp.dispatch("q", project_path="/x")
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_path_c_used_when_chunk_score_below_threshold():
    chunks = [{"file_path": "f.py", "score": 0.1, "document": "code"}]
    disp = _make_dispatcher(chunks=chunks, cfg=_cfg(api_key="sk-test"))
    with respx.mock(base_url="https://api.example.com/v1") as r:
        r.post("/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ext"}}]},
            )
        )
        out = await disp.dispatch("q", project_path="/x")
    assert out["source"] == "external_llm"
    assert out["answer"] == "ext"
    # Ollama should NOT have been called because chunk score < local threshold
    disp._ollama.complete.assert_not_called()  # type: ignore[attr-defined]
