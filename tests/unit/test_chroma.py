"""Unit tests for db/chroma.py — uses chromadb EphemeralClient (in-memory)."""

from __future__ import annotations

import asyncio

import chromadb
import pytest

from codepal.db.chroma import (
    BUG_COLLECTION_NAME,
    _reset_client,
    code_collection_name,
    get_bug_collection,
    get_code_collection,
    make_chunk_id,
    project_slug,
    query_collection,
    upsert_chunks,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the chroma singleton before/after each test."""
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def mem_client():
    """In-memory ChromaDB EphemeralClient (sync, wrapped for async tests)."""
    return chromadb.EphemeralClient()


# ---------------------------------------------------------------------------
# Pure-function helpers
# ---------------------------------------------------------------------------


def test_project_slug_basic():
    assert project_slug("/home/user/my-project") == "my_project"


def test_project_slug_uppercase():
    assert project_slug("/repos/MyService") == "myservice"


def test_project_slug_numbers():
    assert project_slug("/tmp/proj123") == "proj123"


def test_code_collection_name():
    assert code_collection_name("/home/x/codepal") == "codepal_code_codepal"


def test_make_chunk_id_deterministic():
    a = make_chunk_id("foo.py", "bar", 10)
    b = make_chunk_id("foo.py", "bar", 10)
    assert a == b
    assert len(a) == 32


def test_make_chunk_id_unique():
    a = make_chunk_id("foo.py", "bar", 10)
    b = make_chunk_id("foo.py", "bar", 11)
    assert a != b


# ---------------------------------------------------------------------------
# Collection get-or-create (sync EphemeralClient)
# ---------------------------------------------------------------------------


def test_get_code_collection_creates(mem_client):
    name = code_collection_name("/projects/myapp")
    col = mem_client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})
    assert col is not None
    assert col.name == name


def test_get_code_collection_idempotent(mem_client):
    name = code_collection_name("/projects/myapp")
    col1 = mem_client.get_or_create_collection(name)
    col2 = mem_client.get_or_create_collection(name)
    assert col1.name == col2.name


def test_get_bug_collection_creates(mem_client):
    col = mem_client.get_or_create_collection(BUG_COLLECTION_NAME)
    assert col.name == BUG_COLLECTION_NAME


# ---------------------------------------------------------------------------
# Upsert + query round-trip (sync, tests the logic not async path)
# ---------------------------------------------------------------------------


def test_upsert_and_query_round_trip(mem_client):
    col = mem_client.get_or_create_collection(
        code_collection_name("/projects/test"),
        metadata={"hnsw:space": "cosine"},
    )

    col.upsert(
        ids=["id1", "id2"],
        embeddings=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        documents=["def foo(): pass", "class Bar: pass"],
        metadatas=[
            {"file_path": "a.py", "node_name": "foo", "start_line": 1, "end_line": 2},
            {"file_path": "b.py", "node_name": "Bar", "start_line": 5, "end_line": 10},
        ],
    )

    results = col.query(
        query_embeddings=[[1.0, 0.0, 0.0]],
        n_results=2,
        include=["metadatas", "distances", "documents"],
    )

    assert len(results["ids"][0]) == 2
    # Closest result should be id1 (identical direction → distance ≈ 0)
    assert results["ids"][0][0] == "id1"
    d0, d1 = results["distances"][0][0], results["distances"][0][1]
    assert d0 < d1  # id1 closer than id2


def test_upsert_overwrites_existing(mem_client):
    """Re-upserting the same ID should update, not duplicate."""
    col = mem_client.get_or_create_collection(code_collection_name("/projects/overwrite-test"))

    col.upsert(
        ids=["id1"],
        embeddings=[[1.0, 0.0, 0.0]],
        documents=["version 1"],
        metadatas=[{"file_path": "a.py", "node_name": "foo", "start_line": 1, "end_line": 1}],
    )
    col.upsert(
        ids=["id1"],
        embeddings=[[1.0, 0.0, 0.0]],
        documents=["version 2"],
        metadatas=[{"file_path": "a.py", "node_name": "foo", "start_line": 1, "end_line": 1}],
    )

    assert col.count() == 1  # not duplicated

    results = col.query(
        query_embeddings=[[1.0, 0.0, 0.0]],
        n_results=1,
        include=["documents"],
    )
    assert results["documents"][0][0] == "version 2"


def test_make_chunk_id_format():
    cid = make_chunk_id("src/main.py", "MyClass.method", 42)
    assert len(cid) == 32
    assert all(c in "0123456789abcdef" for c in cid)
