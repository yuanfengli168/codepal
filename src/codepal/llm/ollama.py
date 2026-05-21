"""Ollama LLM client — chat completions via /api/chat.

Separate from OllamaEmbedder (embeddings/ollama.py).
Handles code-chat against qwen3:14b (or configured model)
with a 4-connection limit and raise-on-ConnectError contract.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from codepal.config import OllamaConfig

logger = logging.getLogger(__name__)

# Cap concurrent Ollama chat requests to avoid overwhelming a local instance
_MAX_CONNECTIONS = 4


class OllamaChatClient:
    """Async wrapper around the Ollama /api/chat endpoint.

    Design contract:
    - Max 4 concurrent connections (``httpx.Limits``).
    - 120 s timeout for chat completions (configurable via ``cfg.chat_timeout``).
    - Raises ``httpx.ConnectError`` immediately on connection failure (no silent fallback).
    - Retries up to 3 times with exponential back-off on transient errors only
      (``ConnectError``, ``TimeoutException``).
    """

    def __init__(self, cfg: OllamaConfig) -> None:
        self._cfg = cfg
        self._client = httpx.AsyncClient(
            base_url=cfg.base_url,
            timeout=httpx.Timeout(cfg.chat_timeout, connect=10.0),
            limits=httpx.Limits(
                max_connections=_MAX_CONNECTIONS,
                max_keepalive_connections=_MAX_CONNECTIONS,
            ),
        )

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        stream: bool = False,
        options: dict[str, Any] | None = None,
    ) -> str:
        """Send a chat request to Ollama; return the assistant reply text.

        Args:
            messages: List of ``{"role": ..., "content": ...}`` dicts.
            model: Override the model from config (default: ``cfg.chat_model``).
            stream: Must be False; streaming is not supported by this client.
            options: Optional Ollama model options (temperature, etc.).

        Returns:
            The assistant message content string.

        Raises:
            httpx.ConnectError: If Ollama is unreachable.
            httpx.HTTPStatusError: On non-2xx responses.
            RuntimeError: After exhausting retries on transient errors.
        """
        if stream:
            raise NotImplementedError("Streaming is not supported; set stream=False.")

        payload: dict[str, Any] = {
            "model": model or self._cfg.chat_model,
            "messages": messages,
            "stream": False,
        }
        if options:
            payload["options"] = options

        return await self._post_with_retry(payload)

    async def complete(self, prompt: str, *, model: str | None = None) -> str:
        """Convenience wrapper: single user-turn prompt → reply text."""
        return await self.chat(
            [{"role": "user", "content": prompt}],
            model=model,
        )

    async def _post_with_retry(
        self,
        payload: dict[str, Any],
        retries: int = 3,
        backoff: float = 1.0,
    ) -> str:
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                resp = await self._client.post("/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data["message"]["content"]
            except httpx.ConnectError:
                # Don't retry connection errors — surface immediately so the
                # dispatcher can fall back to an external LLM quickly.
                raise
            except (httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
                last_error = exc
                wait = backoff * (2**attempt)
                logger.warning(
                    "Ollama chat attempt %d/%d failed (%s): %s — retrying in %.1fs",
                    attempt + 1,
                    retries,
                    type(exc).__name__,
                    exc,
                    wait,
                )
                await asyncio.sleep(wait)
            except httpx.HTTPStatusError:
                raise

        raise RuntimeError(
            f"Ollama chat failed after {retries} retries: {last_error}"
        )

    async def is_available(self) -> bool:
        """Quick health check — returns True if Ollama responds to /api/tags."""
        try:
            resp = await self._client.get("/api/tags", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()

    # Support async context manager usage
    async def __aenter__(self) -> OllamaChatClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()
