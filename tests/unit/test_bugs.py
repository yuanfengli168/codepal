"""Unit tests for BugStore — uses an in-memory Chroma client + mocked embedder."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from codepal.bugs.store import BugStore, _bug_id
from codepal.db.chroma import make_ephemeral_client


def _fake_embedder(dim: int = 8):
    """Return a SimpleNamespace-like embedder whose vectors depend on text length."""
    async def embed(text: str) -> list[float]:
        base = float(len(text) % 11) / 10.0
        return [base + i * 0.01 for i in range(dim)]

    obj = AsyncMock()
    obj.embed = embed  # type: ignore[assignment]
    return obj


def test_bug_id_is_deterministic():
    a = _bug_id("err x", "fix y")
    b = _bug_id("err x", "fix y")
    c = _bug_id("err x", "different fix")
    assert a == b
    assert a != c
    assert len(a) == 32


@pytest.mark.asyncio
async def test_save_and_search_roundtrip():
    chroma = make_ephemeral_client()
    store = BugStore(chroma=chroma, embedder=_fake_embedder())
    await store.init()

    bug_id = await store.save(
        error="TypeError: cannot unpack non-iterable NoneType",
        solution="Guard against None before unpacking.",
        context="def parse(x): a, b = x",
    )
    assert isinstance(bug_id, str) and len(bug_id) == 32

    results = await store.search("cannot unpack NoneType", limit=3)
    assert results, "expected at least one result"
    top = results[0]
    assert top.id == bug_id
    assert "Guard against None" in top.solution
    assert top.context == "def parse(x): a, b = x"
    assert 0.0 <= top.score <= 1.0


@pytest.mark.asyncio
async def test_save_is_idempotent_on_same_inputs():
    chroma = make_ephemeral_client()
    store = BugStore(chroma=chroma, embedder=_fake_embedder())
    await store.init()

    id1 = await store.save(error="E", solution="S")
    id2 = await store.save(error="E", solution="S")
    assert id1 == id2

    results = await store.search("E", limit=5)
    matching = [r for r in results if r.id == id1]
    assert len(matching) == 1  # not duplicated


@pytest.mark.asyncio
async def test_search_empty_query_does_not_crash():
    """search() should always return a list, even with no matches."""
    chroma = make_ephemeral_client()
    store = BugStore(chroma=chroma, embedder=_fake_embedder())
    await store.init()
    result = await store.search("zzz no such bug zzz", limit=1)
    assert isinstance(result, list)
