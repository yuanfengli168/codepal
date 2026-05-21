"""ChromaDB client singleton and collection helpers."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import chromadb
from chromadb import AsyncClientAPI

from codepal.config import ChromaConfig


_client: AsyncClientAPI | None = None


async def get_chroma_client(cfg: ChromaConfig) -> AsyncClientAPI:
    """Return (or create) the singleton AsyncChromaDB client."""
    global _client
    if _client is None:
        import os
        persist_dir = os.path.expanduser(cfg.persist_dir)
        os.makedirs(persist_dir, exist_ok=True)
        # Use EphemeralClient for tests; PersistentClient for production
        _client = await chromadb.AsyncClient(
            chromadb.Settings(
                chroma_db_impl="duckdb+parquet",
                persist_directory=persist_dir,
                anonymized_telemetry=False,
            )
        )
    return _client


def make_chunk_id(file_path: str, node_name: str, start_line: int) -> str:
    """Generate a deterministic ID for a code chunk."""
    key = f"{file_path}::{node_name}::{start_line}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def code_collection_name(project_slug: str) -> str:
    """Return the ChromaDB collection name for a project's code chunks."""
    return f"codepal_code_{project_slug}"


BUG_COLLECTION_NAME = "codepal_bugs"


async def get_or_create_collection(
    client: AsyncClientAPI,
    name: str,
    metadata: dict[str, Any] | None = None,
):
    """Get or create a ChromaDB collection by name."""
    return await client.get_or_create_collection(
        name=name,
        metadata=metadata or {},
    )
