"""ChromaDB client singleton and collection helpers.

chromadb 1.x ships Rust-backed local clients (EphemeralClient / PersistentClient).
There is no in-process async client; AsyncHttpClient requires a running server.
We use the sync client everywhere and wrap blocking calls with asyncio.to_thread
so FastAPI's async handlers stay non-blocking.

Public API:
  get_chroma_client(cfg)              → ChromaClientWrapper (singleton)
  get_code_collection(client, slug)   → CollectionWrapper
  get_bug_collection(client)          → CollectionWrapper
  upsert_chunks(collection, ...)      → None
  query_collection(collection, ...)   → list[dict]
  make_chunk_id(file, symbol, idx)    → str (16-hex deterministic ID)
  project_slug(project_path)          → str
  code_collection_name(slug_or_path)  → str
  _reset_client()                     → None  (tests only)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.config import Settings

from codepal.config import ChromaConfig

logger = logging.getLogger(__name__)

BUG_COLLECTION_NAME = "codepal_bugs"

# Chroma's anonymous telemetry hook in 1.x can raise
# ``capture() takes 1 positional argument but 3 were given`` on every collection
# operation. We never need telemetry for a local-first tool, so silence it at
# client construction time. We build a fresh ``Settings`` per client because the
# in-memory client uses settings identity to key its system registry; sharing a
# single instance causes ephemeral clients to bleed collection state between
# tests.


def _chroma_settings() -> Settings:
    return Settings(anonymized_telemetry=False)

_client: ChromaClientWrapper | None = None


# ---------------------------------------------------------------------------
# Thin async wrapper around the sync client
# ---------------------------------------------------------------------------


class ChromaClientWrapper:
    """Wraps a sync chromadb Client so all calls are awaitable via to_thread."""

    def __init__(self, sync_client) -> None:
        self._c = sync_client

    async def get_or_create_collection(self, name: str, **kwargs) -> CollectionWrapper:
        col = await asyncio.to_thread(self._c.get_or_create_collection, name, **kwargs)
        return CollectionWrapper(col)

    async def get_collection(self, name: str) -> CollectionWrapper:
        col = await asyncio.to_thread(self._c.get_collection, name)
        return CollectionWrapper(col)

    async def list_collections(self) -> list:
        return await asyncio.to_thread(self._c.list_collections)

    async def heartbeat(self) -> int:
        return await asyncio.to_thread(self._c.heartbeat)

    async def delete_collection(self, name: str) -> None:
        await asyncio.to_thread(self._c.delete_collection, name)

    async def reset(self) -> bool:
        try:
            return await asyncio.to_thread(self._c.reset)
        except Exception:
            return False


class CollectionWrapper:
    """Wraps a sync Collection so all calls are awaitable."""

    def __init__(self, col: Collection) -> None:
        self._col = col

    @property
    def name(self) -> str:
        return self._col.name

    async def upsert(self, **kwargs) -> None:
        await asyncio.to_thread(self._col.upsert, **kwargs)

    async def add(self, **kwargs) -> None:
        await asyncio.to_thread(self._col.add, **kwargs)

    async def query(self, **kwargs) -> dict:
        return await asyncio.to_thread(self._col.query, **kwargs)

    async def get(self, **kwargs) -> dict:
        return await asyncio.to_thread(self._col.get, **kwargs)

    async def count(self) -> int:
        return await asyncio.to_thread(self._col.count)

    async def delete(self, **kwargs) -> None:
        await asyncio.to_thread(self._col.delete, **kwargs)


# ---------------------------------------------------------------------------
# Scoring convention (used by both code search and bug search)
# ---------------------------------------------------------------------------
#
# All Chroma collections in this project are created with
# ``metadata={"hnsw:space": "cosine"}``. Chroma then returns ``distance``
# values in the range ``[0, 2]`` (1 - cosine_similarity, where cosine_similarity
# is in ``[-1, 1]``). We expose a single similarity score in ``[0, 1]`` to the
# REST/MCP surface using the formula below. Both ``query_collection`` (code
# semantic search) and ``BugStore.search`` (bug DB) MUST use this helper so the
# dispatcher's ``bug_score_threshold`` is calibrated against one well-defined
# convention.


def distance_to_score(distance: float) -> float:
    """Convert a Chroma cosine distance to a similarity score in ``[0, 1]``.

    For unit-norm embeddings (Ollama's ``nomic-embed-text`` returns L2-normalised
    vectors) Chroma's cosine distance is effectively in ``[0, 1]``, so

        score = max(0, 1 - distance)

    yields ``1.0`` for an exact match and ``0.0`` for an orthogonal/opposite
    vector. The dispatcher's ``bug_score_threshold`` (default ``0.85``) is
    calibrated against this convention.
    """
    return max(0.0, 1.0 - float(distance))


# ---------------------------------------------------------------------------
# Client lifecycle
# ---------------------------------------------------------------------------


async def get_chroma_client(cfg: ChromaConfig) -> ChromaClientWrapper:
    """Return (or lazily create) the singleton ChromaDB client."""
    global _client
    if _client is None:
        persist_dir = str(Path(cfg.persist_dir).expanduser())
        Path(persist_dir).mkdir(parents=True, exist_ok=True)
        logger.debug("Initialising ChromaDB at %s", persist_dir)
        sync_client = await asyncio.to_thread(
            chromadb.PersistentClient, path=persist_dir, settings=_chroma_settings()
        )
        _client = ChromaClientWrapper(sync_client)
    return _client


def _reset_client() -> None:
    """Reset the singleton — for use in tests only."""
    global _client
    _client = None


def make_ephemeral_client() -> ChromaClientWrapper:
    """Create a fresh in-memory client — for use in tests only."""
    return ChromaClientWrapper(chromadb.EphemeralClient(settings=_chroma_settings()))


# ---------------------------------------------------------------------------
# Collection helpers
# ---------------------------------------------------------------------------


def project_slug(project_path: str) -> str:
    """Slugify the basename of a project path for collection names."""
    name = Path(project_path).name.lower()
    return re.sub(r"[^a-z0-9_]", "_", name)


def code_collection_name(project_slug_or_path: str) -> str:
    """Return the ChromaDB collection name for a project slug or path."""
    if "/" in project_slug_or_path or project_slug_or_path.startswith("~"):
        slug = project_slug(project_slug_or_path)
    else:
        slug = project_slug_or_path
    return f"codepal_code_{slug}"


async def get_code_collection(client: ChromaClientWrapper, project_slug_or_path: str):
    """Get or create the code-chunk collection for a project."""
    name = code_collection_name(project_slug_or_path)
    return await client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})


async def get_bug_collection(client: ChromaClientWrapper):
    """Get or create the shared bug-solutions collection."""
    return await client.get_or_create_collection(
        BUG_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


# ---------------------------------------------------------------------------
# Upsert / query helpers
# ---------------------------------------------------------------------------


def make_chunk_id(file_path: str, symbol_name: str, chunk_index: int) -> str:
    """Return a 16-hex-char deterministic ID for a chunk."""
    key = f"{file_path}::{symbol_name}::{chunk_index}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


async def upsert_chunks(
    collection: CollectionWrapper,
    *,
    ids: list[str],
    embeddings: list[list[float]],
    documents: list[str],
    metadatas: list[dict[str, Any]],
) -> None:
    """Upsert a batch of chunks; normalises metadata to ChromaDB-safe scalars."""
    safe_meta = [_sanitise_metadata(m) for m in metadatas]
    await collection.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=safe_meta,
    )


async def query_collection(
    collection: CollectionWrapper,
    *,
    query_embedding: list[float],
    n_results: int = 5,
    where: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Query a collection; returns flat list of result dicts with ``score`` field."""
    kwargs: dict[str, Any] = {
        "query_embeddings": [query_embedding],
        "n_results": n_results,
        "include": ["metadatas", "distances", "documents"],
    }
    if where:
        kwargs["where"] = where

    raw = await collection.query(**kwargs)

    results: list[dict[str, Any]] = []
    ids = raw.get("ids", [[]])[0]
    distances = raw.get("distances", [[]])[0]
    metas = raw.get("metadatas", [[]])[0]
    docs = raw.get("documents", [[]])[0]

    for i, chunk_id in enumerate(ids):
        entry: dict[str, Any] = {"id": chunk_id}
        entry["score"] = distance_to_score(distances[i])
        entry["document"] = docs[i] if i < len(docs) else ""
        entry.update(metas[i] if i < len(metas) else {})
        results.append(entry)

    return results


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _sanitise_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Coerce metadata values to types ChromaDB accepts (str/int/float/bool)."""
    safe: dict[str, Any] = {}
    for k, v in meta.items():
        if isinstance(v, (bool, str, int, float)):
            safe[k] = v
        elif v is None:
            safe[k] = ""
        else:
            safe[k] = str(v)
    return safe
