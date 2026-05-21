"""ChromaDB async client singleton and collection helpers.

Provides:
- ``get_chroma_client``   — singleton AsyncClientAPI (persistent on disk)
- ``get_code_collection`` — per-project code-chunk collection
- ``get_bug_collection``  — shared bug-solutions collection
- ``upsert_chunks``       — batch upsert helper with deterministic IDs
- ``query_collection``    — vector similarity query helper
- ``_reset_client``       — test-only singleton reset
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

import chromadb
from chromadb import AsyncClientAPI
from chromadb.config import Settings

from codepal.config import ChromaConfig

logger = logging.getLogger(__name__)

BUG_COLLECTION_NAME = "codepal_bugs"

# Module-level singleton; reset in tests via _reset_client()
_client: AsyncClientAPI | None = None


# ---------------------------------------------------------------------------
# Client lifecycle
# ---------------------------------------------------------------------------


async def get_chroma_client(cfg: ChromaConfig) -> AsyncClientAPI:
    """Return (or lazily create) the singleton ChromaDB async client."""
    global _client
    if _client is None:
        persist_dir = str(Path(cfg.persist_dir).expanduser())
        Path(persist_dir).mkdir(parents=True, exist_ok=True)
        logger.debug("Initialising ChromaDB at %s", persist_dir)
        _client = await chromadb.AsyncClient(
            settings=Settings(
                is_persistent=True,
                persist_directory=persist_dir,
                anonymized_telemetry=False,
            )
        )
    return _client


def _reset_client() -> None:
    """Reset the singleton — for use in tests only."""
    global _client
    _client = None


# ---------------------------------------------------------------------------
# Collection helpers
# ---------------------------------------------------------------------------


def project_slug(project_path: str) -> str:
    """Slugify the basename of a project path for collection names.

    Example: ``/home/user/My-Project`` → ``my_project``
    """
    name = Path(project_path).name.lower()
    return re.sub(r"[^a-z0-9_]", "_", name)


def code_collection_name(project_path: str) -> str:
    return f"codepal_code_{project_slug(project_path)}"


async def get_code_collection(client: AsyncClientAPI, project_path: str):
    """Get or create the code-chunk collection for a project."""
    name = code_collection_name(project_path)
    return await client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )


async def get_bug_collection(client: AsyncClientAPI):
    """Get or create the shared bug-solutions collection."""
    return await client.get_or_create_collection(
        BUG_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


# ---------------------------------------------------------------------------
# Upsert / query helpers
# ---------------------------------------------------------------------------


def make_chunk_id(file_path: str, node_name: str, start_line: int) -> str:
    """Deterministic SHA-256 ID for a code chunk (32 hex chars)."""
    key = f"{file_path}::{node_name}::{start_line}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


async def upsert_chunks(
    collection,
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
    collection,
    *,
    query_embedding: list[float],
    n_results: int = 5,
    where: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Query a collection; returns flat list of result dicts with ``score`` field.

    Each dict contains: ``id``, ``score`` (1 − cosine_distance),
    ``document``, and all metadata fields.
    """
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
        entry["score"] = 1.0 - distances[i]
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
