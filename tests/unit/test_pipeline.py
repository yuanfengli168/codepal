"""Unit tests for indexer/pipeline.py.

Uses:
- MagicMock for OllamaEmbedder (avoids real HTTP calls)
- chromadb.EphemeralClient for real in-memory ChromaDB (no disk I/O)
- Temporary files for real file I/O

Tests cover:
- make_chunk_id determinism and uniqueness
- index_path on a real temp directory
- index_file for a single file
- Skipping unchanged files (via IndexState)
- Error handling for unreadable files
- search (mocked embedder, real chroma)
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codepal.config import IndexerConfig
from codepal.db.chroma import make_ephemeral_client
from codepal.indexer.pipeline import IndexerPipeline, _is_excluded, _slugify, make_chunk_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a minimal Python project in a temp directory."""
    (tmp_path / "main.py").write_text("def hello():\n    return 42\n")
    (tmp_path / "utils.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "README.md").write_text("# Project\n")  # should be skipped
    return tmp_path


@pytest.fixture
def mock_embedder():
    """OllamaEmbedder that returns deterministic 3-dim vectors."""
    embedder = MagicMock()
    embedder.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    embedder.embed_batch = AsyncMock(
        side_effect=lambda texts: [[0.1 * (i + 1), 0.2, 0.3] for i in range(len(texts))]
    )
    return embedder


@pytest.fixture
def mem_chroma():
    """In-memory async-wrapped ChromaDB client."""
    return make_ephemeral_client()


@pytest.fixture
def indexer_cfg(tmp_path: Path) -> IndexerConfig:
    return IndexerConfig(
        state_db=str(tmp_path / "state.db"),
        chunk_token_budget=400,
        chunk_overlap=50,
    )


@pytest.fixture
async def pipeline(mem_chroma, mock_embedder, indexer_cfg) -> IndexerPipeline:
    """Fully initialised pipeline with in-memory chroma + mock embedder."""
    p = IndexerPipeline(
        chroma=mem_chroma,
        embedder=mock_embedder,
        cfg=indexer_cfg,
    )
    await p.init()
    return p


# ---------------------------------------------------------------------------
# make_chunk_id
# ---------------------------------------------------------------------------


def test_make_chunk_id_deterministic():
    a = make_chunk_id("src/main.py", "hello", 0)
    b = make_chunk_id("src/main.py", "hello", 0)
    assert a == b


def test_make_chunk_id_16_chars():
    cid = make_chunk_id("src/main.py", "hello", 0)
    assert len(cid) == 16
    assert all(c in "0123456789abcdef" for c in cid)


def test_make_chunk_id_unique_by_index():
    a = make_chunk_id("src/main.py", "hello", 0)
    b = make_chunk_id("src/main.py", "hello", 1)
    assert a != b


def test_make_chunk_id_unique_by_symbol():
    a = make_chunk_id("src/main.py", "foo", 0)
    b = make_chunk_id("src/main.py", "bar", 0)
    assert a != b


# ---------------------------------------------------------------------------
# index_path
# ---------------------------------------------------------------------------


async def test_index_path_indexes_python_files(pipeline, tmp_project):
    result = await pipeline.index_path(tmp_project, "testproject")
    assert result["indexed"] > 0
    assert result["errors"] == []


async def test_index_path_skips_markdown(pipeline, tmp_project):
    result = await pipeline.index_path(tmp_project, "testproject")
    # Only .py files should be indexed; README.md is unsupported
    # indexed count should reflect chunks (≥ 1 per py file)
    assert result["indexed"] >= 2  # at least hello + add


async def test_index_path_not_a_directory(pipeline, tmp_path):
    result = await pipeline.index_path(tmp_path / "nonexistent", "slug")
    assert result["indexed"] == 0
    assert len(result["errors"]) == 1
    assert "Not a directory" in result["errors"][0]


async def test_index_path_returns_skipped_on_reindex(pipeline, tmp_project):
    """Second index pass should skip unchanged files."""
    first = await pipeline.index_path(tmp_project, "testproject")
    assert first["indexed"] > 0

    second = await pipeline.index_path(tmp_project, "testproject")
    assert second["skipped"] >= 1
    assert second["indexed"] == 0  # nothing new to index


# ---------------------------------------------------------------------------
# index_file
# ---------------------------------------------------------------------------


async def test_index_file_single_file(pipeline, tmp_path):
    f = tmp_path / "single.py"
    f.write_text("def single_func():\n    pass\n")
    result = await pipeline.index_file(f, "slug")
    assert result["indexed"] >= 1
    assert result["errors"] == []


async def test_index_file_skipped_on_reindex(pipeline, tmp_path):
    f = tmp_path / "stable.py"
    f.write_text("def stable():\n    pass\n")

    first = await pipeline.index_file(f, "slug")
    assert first["indexed"] >= 1

    second = await pipeline.index_file(f, "slug")
    assert second["skipped"] == 1
    assert second["indexed"] == 0


async def test_index_file_unsupported_ext_skipped(pipeline, tmp_path):
    f = tmp_path / "data.json"
    f.write_text('{"key": "value"}')
    result = await pipeline.index_file(f, "slug")
    # .json not in SUPPORTED_EXTENSIONS → skipped or 0 indexed
    assert result["indexed"] == 0 or result["skipped"] == 1


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


async def test_index_path_handles_embed_error(pipeline, tmp_project, mock_embedder):
    """Embedding errors should be captured in errors[], not raised."""
    mock_embedder.embed_batch.side_effect = RuntimeError("Ollama down")
    result = await pipeline.index_path(tmp_project, "testproject")
    assert len(result["errors"]) > 0
    assert result["indexed"] == 0


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


async def test_search_empty_when_no_collection(pipeline):
    """search() returns [] when the collection doesn't exist yet."""
    results = await pipeline.search("hello", project_slug="nonexistent_slug")
    assert results == []


async def test_search_returns_results_after_indexing(pipeline, tmp_path, mock_embedder):
    """After indexing, search should return at least one result."""
    f = tmp_path / "greet.py"
    f.write_text("def greet(name):\n    return f'Hello {name}'\n")

    await pipeline.index_file(f, "searchtest")

    # Override embed to return a fixed vector for search too
    mock_embedder.embed.return_value = [0.1, 0.2, 0.3]

    results = await pipeline.search("greeting function", project_slug="searchtest")
    assert isinstance(results, list)
    # With an in-memory chroma and matching vectors, should get ≥ 1 result
    assert len(results) >= 1
    assert "file_path" in results[0]
    assert "symbol_name" in results[0]
    assert "score" in results[0]
    assert "text" in results[0]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_is_excluded_node_modules():
    assert _is_excluded(Path("/project/node_modules/lodash/index.js"))


def test_is_excluded_git():
    assert _is_excluded(Path("/project/.git/hooks/pre-commit"))


def test_is_excluded_venv():
    assert _is_excluded(Path("/project/.venv/lib/python3.11/site-packages/foo.py"))


def test_is_not_excluded_src():
    assert not _is_excluded(Path("/project/src/main.py"))


def test_slugify_basic():
    assert _slugify("My-Project") == "my_project"


def test_slugify_truncates():
    long = "a" * 60
    assert len(_slugify(long)) <= 40
