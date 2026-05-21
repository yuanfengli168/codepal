"""Unit tests for db/chroma.py.

Uses make_ephemeral_client() (in-memory, no disk I/O).
Tests cover: slug helpers, collection get-or-create, upsert/query round-trip,
overwrite idempotency, metadata sanitisation.
"""

from __future__ import annotations

import pytest

from codepal.db.chroma import (
    BUG_COLLECTION_NAME,
    _reset_client,
    _sanitise_metadata,
    code_collection_name,
    get_bug_collection,
    get_code_collection,
    make_chunk_id,
    make_ephemeral_client,
    project_slug,
    query_collection,
    upsert_chunks,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singleton():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def mem_client():
    """Fresh in-memory ChromaClientWrapper for each test."""
    return make_ephemeral_client()


# ---------------------------------------------------------------------------
# Slug / naming helpers
# ---------------------------------------------------------------------------


def test_project_slug_basic():
    assert project_slug("/home/user/my-project") == "my_project"


def test_project_slug_uppercase():
    assert project_slug("/repos/MyService") == "myservice"


def test_project_slug_numbers():
    assert project_slug("/tmp/proj123") == "proj123"


def test_code_collection_name_with_slug():
    assert code_collection_name("codepal") == "codepal_code_codepal"


def test_code_collection_name_with_path():
    assert code_collection_name("/home/x/codepal") == "codepal_code_codepal"


def test_make_chunk_id_deterministic():
    a = make_chunk_id("foo.py", "bar", 10)
    b = make_chunk_id("foo.py", "bar", 10)
    assert a == b
    assert len(a) == 16


def test_make_chunk_id_unique_by_index():
    a = make_chunk_id("foo.py", "bar", 10)
    b = make_chunk_id("foo.py", "bar", 11)
    assert a != b


def test_make_chunk_id_unique_by_symbol():
    a = make_chunk_id("foo.py", "bar", 0)
    b = make_chunk_id("foo.py", "baz", 0)
    assert a != b


def test_make_chunk_id_hex_only():
    cid = make_chunk_id("src/main.py", "MyClass.method", 42)
    assert len(cid) == 16
    assert all(c in "0123456789abcdef" for c in cid)


# ---------------------------------------------------------------------------
# Metadata sanitisation
# ---------------------------------------------------------------------------


def test_sanitise_metadata_passthrough():
    m = {"a": "str", "b": 1, "c": 1.5, "d": True}
    assert _sanitise_metadata(m) == m


def test_sanitise_metadata_none_to_empty_string():
    m = {"x": None}
    assert _sanitise_metadata(m) == {"x": ""}


def test_sanitise_metadata_list_to_str():
    m = {"x": [1, 2, 3]}
    result = _sanitise_metadata(m)
    assert isinstance(result["x"], str)


# ---------------------------------------------------------------------------
# Collection get-or-create
# ---------------------------------------------------------------------------


async def test_get_code_collection_creates(mem_client):
    col = await get_code_collection(mem_client, "myapp")
    assert col is not None
    assert col.name == "codepal_code_myapp"


async def test_get_code_collection_idempotent(mem_client):
    col1 = await get_code_collection(mem_client, "myapp")
    col2 = await get_code_collection(mem_client, "myapp")
    assert col1.name == col2.name


async def test_get_bug_collection_creates(mem_client):
    col = await get_bug_collection(mem_client)
    assert col.name == BUG_COLLECTION_NAME


# ---------------------------------------------------------------------------
# Upsert + query round-trip
# ---------------------------------------------------------------------------


async def test_upsert_and_query_round_trip(mem_client):
    col = await get_code_collection(mem_client, "test")

    await upsert_chunks(
        col,
        ids=["id1", "id2"],
        embeddings=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        documents=["def foo(): pass", "class Bar: pass"],
        metadatas=[
            {"file_path": "a.py", "symbol_name": "foo", "start_line": 1, "end_line": 2},
            {"file_path": "b.py", "symbol_name": "Bar", "start_line": 5, "end_line": 10},
        ],
    )

    results = await query_collection(col, query_embedding=[1.0, 0.0, 0.0], n_results=2)

    assert len(results) == 2
    assert results[0]["id"] == "id1"
    assert results[0]["score"] > results[1]["score"]
    assert results[0]["document"] == "def foo(): pass"
    assert results[0]["file_path"] == "a.py"


async def test_upsert_overwrites_existing(mem_client):
    col = await get_code_collection(mem_client, "overwrite-test")

    await upsert_chunks(
        col,
        ids=["id1"],
        embeddings=[[1.0, 0.0, 0.0]],
        documents=["version 1"],
        metadatas=[{"file_path": "a.py", "symbol_name": "foo", "start_line": 1, "end_line": 1}],
    )
    await upsert_chunks(
        col,
        ids=["id1"],
        embeddings=[[1.0, 0.0, 0.0]],
        documents=["version 2"],
        metadatas=[{"file_path": "a.py", "symbol_name": "foo", "start_line": 1, "end_line": 1}],
    )

    assert await col.count() == 1

    results = await query_collection(col, query_embedding=[1.0, 0.0, 0.0], n_results=1)
    assert results[0]["document"] == "version 2"
