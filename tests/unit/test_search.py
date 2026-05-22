"""Unit tests for GET /v1/search — uses a fake pipeline injected into app.state."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codepal.api.routes import search as search_route


def _make_app(pipeline) -> FastAPI:
    app = FastAPI()
    app.include_router(search_route.router, prefix="/v1")
    app.state.pipeline = pipeline
    return app


def test_search_returns_results_from_pipeline():
    pipeline = AsyncMock()
    pipeline.search.return_value = [
        {
            "file_path": "src/foo.py",
            "symbol_name": "bar",
            "score": 0.9,
            "text": "def bar(): ...",
            "start_line": 1,
            "end_line": 3,
        }
    ]
    app = _make_app(pipeline)
    client = TestClient(app)

    r = client.get("/v1/search", params={"q": "bar", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert len(body["results"]) == 1
    assert body["results"][0]["file_path"] == "src/foo.py"
    assert body["results"][0]["symbol_name"] == "bar"
    assert body["results"][0]["start_line"] == 1


def test_search_returns_empty_results_when_no_match():
    pipeline = AsyncMock()
    pipeline.search.return_value = []
    client = TestClient(_make_app(pipeline))
    r = client.get("/v1/search", params={"q": "nothing"})
    assert r.status_code == 200
    assert r.json() == {"results": []}


def test_search_requires_q_param():
    pipeline = AsyncMock()
    client = TestClient(_make_app(pipeline))
    r = client.get("/v1/search")
    assert r.status_code == 422


@pytest.mark.parametrize("limit", [0, 51, -1])
def test_search_rejects_out_of_range_limit(limit):
    pipeline = AsyncMock()
    client = TestClient(_make_app(pipeline))
    r = client.get("/v1/search", params={"q": "x", "limit": limit})
    assert r.status_code == 422
