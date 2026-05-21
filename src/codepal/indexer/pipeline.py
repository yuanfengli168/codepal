"""Indexing pipeline: parse → chunk → embed → upsert ChromaDB.

Public API:
  pipeline.index_path(path, project_slug)  — full directory scan
  pipeline.index_file(file, project_slug)  — single file
  pipeline.search(query, project_slug, limit) — semantic search
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from codepal.config import IndexerConfig
from codepal.db.chroma import (
    ChromaClientWrapper,
    get_code_collection,
    query_collection,
    upsert_chunks,
)
from codepal.embeddings.ollama import OllamaEmbedder
from codepal.indexer.chunker import TextChunk, chunk_symbols
from codepal.indexer.parser import EXT_TO_LANGUAGE, CodeParser, ParsedChunk
from codepal.indexer.state import IndexState

if TYPE_CHECKING:
    from codepal.api.models import IndexResponse

logger = logging.getLogger(__name__)

# Directories that are never worth indexing
_EXCLUDED_DIRS = frozenset(
    [
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".env",
        "dist",
        "build",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "target",  # Rust/Maven
        "vendor",  # Go/Ruby
    ]
)

SUPPORTED_EXTENSIONS = frozenset(EXT_TO_LANGUAGE.keys())


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def make_chunk_id(file_path: str, symbol_name: str, chunk_index: int) -> str:
    """Return a 16-hex-char deterministic ID for a TextChunk."""
    key = f"{file_path}::{symbol_name}::{chunk_index}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class IndexerPipeline:
    """Orchestrates file parsing, chunking, embedding, and ChromaDB upsert."""

    def __init__(
        self,
        chroma: ChromaClientWrapper,
        embedder: OllamaEmbedder,
        cfg: IndexerConfig,
    ) -> None:
        self._chroma = chroma
        self._embedder = embedder
        self._cfg = cfg
        self._parser = CodeParser()
        self._state: IndexState | None = None

    async def init(self) -> None:
        """Open (or create) the SQLite state tracker."""
        self._state = IndexState(self._cfg.state_db)
        await self._state.init()

    # ------------------------------------------------------------------
    # Public indexing API
    # ------------------------------------------------------------------

    async def index_path(
        self, path: Path, project_slug: str
    ) -> dict[str, Any]:
        """Recursively index all supported files under *path*.

        Returns ``{indexed: int, skipped: int, errors: list[str]}``.
        """
        if not path.is_dir():
            return {"indexed": 0, "skipped": 0, "errors": [f"Not a directory: {path}"]}

        files = [
            p
            for p in path.rglob("*")
            if p.is_file()
            and p.suffix in SUPPORTED_EXTENSIONS
            and not _is_excluded(p)
        ]
        return await self._index_files(files, project_slug)

    async def index_file(
        self, file: Path, project_slug: str
    ) -> dict[str, Any]:
        """Index a single file.

        Returns ``{indexed: int, skipped: int, errors: list[str]}``.
        """
        return await self._index_files([file], project_slug)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self, query: str, project_slug: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Embed *query* and return top-*limit* results from the project collection.

        Each result dict has: ``file_path``, ``symbol_name``, ``score``, ``text``,
        ``start_line``, ``end_line``.
        """
        collection_name = f"codepal_code_{project_slug}"
        try:
            collection = await self._chroma.get_collection(collection_name)
        except Exception:
            logger.debug("Collection %s not found", collection_name)
            return []

        embedding = await self._embedder.embed(query)
        raw = await query_collection(collection, query_embedding=embedding, n_results=limit)

        results = []
        for item in raw:
            results.append(
                {
                    "file_path": item.get("file_path", ""),
                    "symbol_name": item.get("symbol_name", item.get("node_name", "")),
                    "score": item.get("score", 0.0),
                    "text": item.get("document", ""),
                    "start_line": item.get("start_line", 0),
                    "end_line": item.get("end_line", 0),
                }
            )
        return results

    # ------------------------------------------------------------------
    # Backward-compat wrappers used by older routes / MCP tools
    # ------------------------------------------------------------------

    async def run(
        self,
        path: str | None = None,
        files: list[str] | None = None,
    ) -> IndexResponse:
        """Backward-compatible wrapper; returns an IndexResponse model."""
        from codepal.api.models import IndexResponse

        if files:
            project_slug = _slug_from_files(files)
            result = await self._index_files(
                [Path(f) for f in files], project_slug
            )
        elif path:
            project_slug = _slugify(Path(path).name)
            result = await self.index_path(Path(path), project_slug)
        else:
            return IndexResponse(indexed=0, skipped=0, errors=[])

        return IndexResponse(
            indexed=result["indexed"],
            skipped=result["skipped"],
            errors=result["errors"],
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _index_files(
        self, files: list[Path], project_slug: str
    ) -> dict[str, Any]:
        collection = await get_code_collection(
            self._chroma, project_slug  # pass slug directly, not a full path
        )
        indexed = skipped = 0
        errors: list[str] = []

        for file_path in files:
            fp = str(file_path)
            try:
                if file_path.suffix not in SUPPORTED_EXTENSIONS:
                    skipped += 1
                    continue

                if self._state and not await self._state.is_changed(project_slug, fp):
                    skipped += 1
                    continue

                symbols: list[ParsedChunk] = self._parser.parse_file(file_path)
                if not symbols:
                    skipped += 1
                    continue

                chunks: list[TextChunk] = chunk_symbols(
                    symbols,
                    token_budget=self._cfg.chunk_token_budget,
                    overlap=self._cfg.chunk_overlap,
                )
                if not chunks:
                    skipped += 1
                    continue

                embeddings = await self._embedder.embed_batch([c.text for c in chunks])

                ids = [make_chunk_id(c.file_path, c.symbol_name, c.chunk_index) for c in chunks]
                metas = [
                    {
                        "file_path": c.file_path,
                        "symbol_name": c.symbol_name,
                        "chunk_index": c.chunk_index,
                        "language": c.language,
                        "node_type": c.node_type,
                        "start_line": c.start_line,
                        "end_line": c.end_line,
                        "project": project_slug,
                    }
                    for c in chunks
                ]

                await upsert_chunks(
                    collection,
                    ids=ids,
                    embeddings=embeddings,
                    documents=[c.text for c in chunks],
                    metadatas=metas,
                )

                if self._state:
                    await self._state.mark_indexed(project_slug, fp)

                indexed += len(chunks)

            except Exception as exc:
                logger.error("Error indexing %s: %s", fp, exc, exc_info=True)
                errors.append(f"{fp}: {exc}")

        return {"indexed": indexed, "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_excluded(path: Path) -> bool:
    return any(part in _EXCLUDED_DIRS for part in path.parts)


def _slugify(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9_]", "_", name.lower())[:40].strip("_") or "project"


def _slug_from_files(files: list[str]) -> str:
    if not files:
        return "project"
    return _slugify(Path(files[0]).parent.name)
