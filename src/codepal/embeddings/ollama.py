"""Ollama embedding client using nomic-embed-text."""

from __future__ import annotations

import asyncio
import logging

import httpx

from codepal.config import OllamaConfig

logger = logging.getLogger(__name__)


class OllamaEmbedder:
    """Thin async wrapper around the Ollama /api/embeddings endpoint."""

    def __init__(self, cfg: OllamaConfig) -> None:
        self.cfg = cfg
        self._client = httpx.AsyncClient(base_url=cfg.base_url, timeout=cfg.embed_timeout)

    async def embed(self, text: str) -> list[float]:
        """Embed a single text string. Returns a float vector."""
        return (await self.embed_batch([text]))[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a list of texts. Returns a list of float vectors.
        Processes sequentially with retry logic.
        """
        results = []
        for text in texts:
            vector = await self._embed_with_retry(text)
            results.append(vector)
        return results

    async def _embed_with_retry(
        self, text: str, retries: int = 3, backoff: float = 1.0
    ) -> list[float]:
        """Call Ollama /api/embeddings with exponential backoff on connection errors."""
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                resp = await self._client.post(
                    "/api/embeddings",
                    json={"model": self.cfg.embed_model, "prompt": text},
                )
                resp.raise_for_status()
                data = resp.json()
                return data["embedding"]
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_error = exc
                wait = backoff * (2 ** attempt)
                logger.warning(
                    "Ollama embed attempt %d/%d failed: %s — retrying in %.1fs",
                    attempt + 1,
                    retries,
                    exc,
                    wait,
                )
                await asyncio.sleep(wait)
        raise RuntimeError(f"Ollama embed failed after {retries} retries: {last_error}")

    async def close(self) -> None:
        await self._client.aclose()
