"""Full indexing pipeline: parse → chunk → embed → upsert ChromaDB."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from re import sub
from typing import Any

from chromadb import AsyncClientAPI

from codepal.api.models import CodeChunk, IndexResponse
from codepal.config import IndexerConfig
from codepal.db.chroma import (
    get_code_collection,
    make_chunk_id,
    upsert_chunks,
)
from codepal.embeddings.ollama import OllamaEmbedder
from codepal.indexer.chunker import chunk_parsed
from codepal.indexer.parser import EXT_TO_LANG, ParsedChunk, parse_file
from codepal.indexer.state import IndexState

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = set(EXT_TO_LANG.keys()) | {".jsx", ".tsx"}


def _slugify(text: str) -> str:
    """Convert a string to a safe slug for ChromaDB collection names."""
    return sub(r"[^a-zA-Z0-9_]", "_", text).strip("_")[:40].lower()


class IndexerPipeline:
    """Orchestrates file parsing, chunking, embedding, and ChromaDB upsert."""

    def __init__(
        self,
        chroma: AsyncClientAPI,
        embedder: OllamaEmbedder,
        cfg: IndexerConfig,
    ) -> None:
        self.chroma = chroma
        self.embedder = embedder
        self.cfg = cfg
        self.state: IndexState | None = None

    async def init(self) -> None:
        """Initialize the SQLite state tracker."""
        self.state = IndexState(self.cfg.state_db)
        await self.state.init()

    async def run(
        self,
        path: str | None = None,
        files: list[str] | None = None,
    ) -> IndexResponse:
        """Index a directory or a specific list of files.

        - ``files`` takes precedence over ``path``.
        - Returns :class:`IndexResponse` with chunk count and any errors.
        """
        target_files: list[str] = []

        if files:
            target_files = [f for f in files if Path(f).suffix in SUPPORTED_EXTENSIONS]
        elif path:
            root = Path(path)
            if not root.is_dir():
                return IndexResponse(indexed=0, errors=[f"Not a directory: {path}"])
            target_files = [
                str(p)
                for p in root.rglob("*")
                if p.suffix in SUPPORTED_EXTENSIONS and not _is_excluded(p)
            ]

        if not target_files:
            return IndexResponse(indexed=0, errors=[])

        # Derive project path/slug for collection naming
        project_path = path or str(Path(target_files[0]).parent)
        project_slug = _slugify(os.path.basename(project_path) or "project")
        collection = await get_code_collection(self.chroma, project_path)

        indexed = 0
        errors: list[str] = []

        for file_path in target_files:
            if self.state and not await self.state.needs_index(project_slug, file_path):
                logger.debug("Skipping unchanged file: %s", file_path)
                continue
            try:
                parsed_chunks: list[ParsedChunk] = parse_file(file_path)
                all_chunk_ids: list[str] = []
                all_embeddings: list[list[float]] = []
                all_docs: list[str] = []
                all_metas: list[dict[str, Any]] = []

                for pc in parsed_chunks:
                    chunks = chunk_parsed(
                        pc,
                        budget=self.cfg.chunk_token_budget,
                        overlap=self.cfg.chunk_overlap,
                    )
                    for chunk in chunks:
                        vector = await self.embedder.embed(chunk.text)
                        chunk_id = make_chunk_id(
                            chunk.file_path, chunk.node_name, chunk.start_line
                        )
                        all_chunk_ids.append(chunk_id)
                        all_embeddings.append(vector)
                        all_docs.append(chunk.text)
                        all_metas.append(
                            {
                                "file_path": chunk.file_path,
                                "language": chunk.language,
                                "node_type": chunk.node_type,
                                "node_name": chunk.node_name,
                                "start_line": chunk.start_line,
                                "end_line": chunk.end_line,
                                "project": project_slug,
                            }
                        )

                if all_chunk_ids:
                    await upsert_chunks(
                        collection,
                        ids=all_chunk_ids,
                        embeddings=all_embeddings,
                        documents=all_docs,
                        metadatas=all_metas,
                    )
                    indexed += len(all_chunk_ids)

                if self.state:
                    await self.state.mark_indexed(project_slug, file_path)

            except Exception as exc:
                logger.error("Error indexing %s: %s", file_path, exc)
                errors.append(f"{file_path}: {exc}")

        return IndexResponse(indexed=indexed, errors=errors)

    async def index_files(
        self, files: list[str], project_path: str
    ) -> tuple[int, list[str]]:
        """Convenience wrapper used by API routes (returns tuple instead of model)."""
        resp = await self.run(path=project_path, files=files)
        return resp.indexed, resp.errors

    async def index_directory(self, directory: str) -> tuple[int, list[str]]:
        """Index an entire directory tree."""
        resp = await self.run(path=directory)
        return resp.indexed, resp.errors

    async def search(self, query: str, limit: int = 5) -> list[CodeChunk]:
        """Semantic search across all indexed code collections."""
        vector = await self.embedder.embed(query)
        results: list[CodeChunk] = []

        collections = await self.chroma.list_collections()
        for coll_meta in collections:
            name = coll_meta.name
            if not name.startswith("codepal_code_"):
                continue
            collection = await self.chroma.get_collection(name)
            resp = await collection.query(
                query_embeddings=[vector],
                n_results=min(limit, 10),
                include=["documents", "metadatas", "distances"],
            )
            if not resp["ids"] or not resp["ids"][0]:
                continue
            for i, _doc_id in enumerate(resp["ids"][0]):
                meta = resp["metadatas"][0][i]  # type: ignore[index]
                doc = resp["documents"][0][i]  # type: ignore[index]
                distance = resp["distances"][0][i]  # type: ignore[index]
                score = max(0.0, 1.0 - distance)
                results.append(
                    CodeChunk(
                        file=meta.get("file_path", ""),
                        symbol=meta.get("node_name", ""),
                        lines=[meta.get("start_line", 0), meta.get("end_line", 0)],
                        score=score,
                        snippet=doc[:300] if doc else "",
                    )
                )

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]


def _is_excluded(path: Path) -> bool:
    """Return True if the path should be excluded from indexing."""
    excluded_dirs = {
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        "dist", "build", ".mypy_cache", ".ruff_cache",
    }
    return any(part in excluded_dirs for part in path.parts)
