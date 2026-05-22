"""Bug solution save and search using ChromaDB."""

from __future__ import annotations

import hashlib
import logging
import time

from codepal.api.models import BugSearchResult
from codepal.db.chroma import ChromaClientWrapper, distance_to_score, get_bug_collection
from codepal.embeddings.ollama import OllamaEmbedder

logger = logging.getLogger(__name__)


def _bug_id(error: str, solution: str) -> str:
    """Generate a deterministic ID from error + solution text."""
    key = f"{error.strip()}::{solution.strip()}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


class BugStore:
    """Save and search bug solutions in ChromaDB."""

    def __init__(self, chroma: ChromaClientWrapper, embedder: OllamaEmbedder) -> None:
        self.chroma = chroma
        self.embedder = embedder
        self._collection = None

    async def init(self) -> None:
        """Initialize the bug solutions ChromaDB collection."""
        self._collection = await get_bug_collection(self.chroma)

    async def save(
        self,
        error: str,
        solution: str,
        context: str | None = None,
    ) -> str:
        """Embed and persist a bug/solution pair. Returns the generated ID."""
        assert self._collection is not None, "BugStore not initialized"

        bug_id = _bug_id(error, solution)
        embed_text = f"Error: {error}\nSolution: {solution}"
        if context:
            embed_text += f"\nContext: {context}"

        vector = await self.embedder.embed(embed_text)

        metadata = {
            "error": error,
            "solution": solution,
            "context": context or "",
            "timestamp": int(time.time()),
            "resolved": True,
        }

        await self._collection.upsert(
            ids=[bug_id],
            embeddings=[vector],
            documents=[embed_text],
            metadatas=[metadata],
        )

        logger.info("Saved bug solution id=%s", bug_id)
        return bug_id

    async def search(
        self,
        query: str,
        limit: int = 5,
    ) -> list[BugSearchResult]:
        """Return the closest matching bug solutions for the query."""
        assert self._collection is not None, "BugStore not initialized"

        vector = await self.embedder.embed(query)
        resp = await self._collection.query(
            query_embeddings=[vector],
            n_results=limit,
            include=["documents", "metadatas", "distances"],
        )

        results: list[BugSearchResult] = []
        if not resp["ids"] or not resp["ids"][0]:
            return results

        for i, bug_id in enumerate(resp["ids"][0]):
            meta = resp["metadatas"][0][i]  # type: ignore[index]
            distance = resp["distances"][0][i]  # type: ignore[index]
            score = distance_to_score(distance)
            results.append(
                BugSearchResult(
                    id=bug_id,
                    score=score,
                    error=meta.get("error", ""),
                    solution=meta.get("solution", ""),
                    context=meta.get("context") or None,
                )
            )

        return results
