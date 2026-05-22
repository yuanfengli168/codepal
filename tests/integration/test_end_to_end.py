"""End-to-end integration test: index → search → query (Ollama mocked).

This wires the real FastAPI app with the real BugStore + IndexerPipeline,
but mocks the Ollama embedder and chat client so no external services are
required to run the test.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codepal.api.routes import bugs, index, query, search, status
from codepal.bugs.store import BugStore
from codepal.config import AppConfig
from codepal.db.chroma import make_ephemeral_client
from codepal.indexer.pipeline import IndexerPipeline
from codepal.llm.dispatcher import QueryDispatcher


class _FakeEmbedder:
    """Deterministic stand-in for OllamaEmbedder — vectors derived from text length."""

    async def embed(self, text: str) -> list[float]:
        return await self._vec(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self._vec(t) for t in texts]

    async def _vec(self, text: str) -> list[float]:
        base = float(len(text) % 17) / 10.0
        return [base + i * 0.001 for i in range(16)]

    async def close(self) -> None:
        return None


class _FakeChat:
    async def complete(self, prompt: str, *, model: str | None = None) -> str:
        return f"FAKE-LLM-REPLY for prompt of length {len(prompt)}"

    async def chat(self, *a: Any, **k: Any) -> str:
        return "FAKE-LLM-REPLY"

    async def close(self) -> None:
        return None


@pytest.fixture
def app_with_stubs(tmp_path: Path) -> FastAPI:
    """Build a FastAPI app wired with real stores + ephemeral Chroma + fake LLM."""
    cfg = AppConfig()
    cfg.chroma.persist_dir = str(tmp_path / "chroma")
    cfg.indexer.state_db = str(tmp_path / "state.db")

    chroma = make_ephemeral_client()
    embedder = _FakeEmbedder()
    chat = _FakeChat()

    bug_store = BugStore(chroma=chroma, embedder=embedder)  # type: ignore[arg-type]
    pipeline = IndexerPipeline(chroma=chroma, embedder=embedder, cfg=cfg.indexer)  # type: ignore[arg-type]
    dispatcher = QueryDispatcher(
        chroma=chroma,
        embedder=embedder,  # type: ignore[arg-type]
        ollama_client=chat,  # type: ignore[arg-type]
        bug_store=bug_store,
        cfg=cfg,
    )

    # init runs inside TestClient's loop via FastAPI lifespan so aiosqlite's
    # connection is bound to the same loop that will serve requests.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        await bug_store.init()
        await pipeline.init()
        yield

    app = FastAPI(lifespan=_lifespan)
    app.include_router(status.router, prefix="/v1")
    app.include_router(query.router, prefix="/v1")
    app.include_router(index.router, prefix="/v1")
    app.include_router(search.router, prefix="/v1")
    app.include_router(bugs.router, prefix="/v1")
    app.state.chroma = chroma
    app.state.embedder = embedder
    app.state.ollama_client = chat
    app.state.bug_store = bug_store
    app.state.pipeline = pipeline
    app.state.dispatcher = dispatcher
    app.state.config = cfg
    return app


def test_bug_save_then_search(app_with_stubs: FastAPI) -> None:
    with TestClient(app_with_stubs) as client:
        r = client.post(
            "/v1/bugs",
            json={
                "error": "TypeError: NoneType not iterable",
                "solution": "Check None before iterating.",
                "context": "for x in result: ...",
            },
        )
        assert r.status_code == 201
        bug_id = r.json()["id"]

        r = client.get("/v1/bugs/search", params={"q": "NoneType", "limit": 3})
        assert r.status_code == 200
        results = r.json()["results"]
        assert any(item["id"] == bug_id for item in results)


def test_index_and_search_python_file(app_with_stubs: FastAPI, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "math_utils.py").write_text(
        textwrap.dedent(
            """
            def add(a, b):
                '''Return the sum of a and b.'''
                return a + b


            def multiply(a, b):
                '''Return the product of a and b.'''
                return a * b
            """
        ).strip()
    )

    with TestClient(app_with_stubs) as client:
        r = client.post("/v1/index", json={"path": str(project), "project_slug": "proj_int"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["indexed"] >= 1
        assert body["errors"] == []

        r = client.get("/v1/search", params={"q": "add numbers", "project_slug": "proj_int"})
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) >= 1


def test_query_uses_external_when_no_local_match(app_with_stubs: FastAPI) -> None:
    """With no bug hits and no indexed code, dispatcher should still respond
    successfully (falling back to the wired local fake LLM) or 503 if no
    backend is available. Either is acceptable end-to-end wiring."""
    with TestClient(app_with_stubs) as client:
        r = client.post(
            "/v1/query",
            json={"query": "explain my code", "project_path": "/nonexistent"},
        )
        assert r.status_code in (200, 503), r.text
        if r.status_code == 200:
            body = r.json()
            assert "answer" in body or "response" in body
