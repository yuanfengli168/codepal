"""Unit tests for llm/ollama.py — mocked HTTP, no live Ollama required."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from codepal.config import OllamaConfig
from codepal.llm.ollama import OllamaChatClient, _MAX_CONNECTIONS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ollama_cfg() -> OllamaConfig:
    return OllamaConfig(
        base_url="http://localhost:11434",
        chat_model="qwen3:14b",
        chat_timeout=120,
        embed_model="nomic-embed-text",
        embed_timeout=30,
    )


@pytest.fixture
async def client(ollama_cfg) -> OllamaChatClient:
    c = OllamaChatClient(ollama_cfg)
    yield c
    await c.close()


# ---------------------------------------------------------------------------
# Connection limits
# ---------------------------------------------------------------------------


def test_client_respects_max_connections(ollama_cfg):
    c = OllamaChatClient(ollama_cfg)
    # httpx stores limits on the underlying httpcore pool
    pool = c._client._transport._pool
    assert pool._max_connections == _MAX_CONNECTIONS
    assert pool._max_keepalive_connections == _MAX_CONNECTIONS


# ---------------------------------------------------------------------------
# Successful chat
# ---------------------------------------------------------------------------


@respx.mock
async def test_chat_returns_content(client):
    respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "Hello from Ollama"}},
        )
    )

    result = await client.chat([{"role": "user", "content": "hi"}])
    assert result == "Hello from Ollama"


@respx.mock
async def test_complete_convenience(client):
    respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "42"}},
        )
    )

    result = await client.complete("What is the answer?")
    assert result == "42"


@respx.mock
async def test_chat_sends_correct_model(client):
    route = respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "ok"}},
        )
    )

    await client.chat([{"role": "user", "content": "hello"}])

    sent_payload = json.loads(route.calls[0].request.content)
    assert sent_payload["model"] == "qwen3:14b"
    assert sent_payload["stream"] is False


@respx.mock
async def test_chat_model_override(client):
    route = respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "ok"}},
        )
    )

    await client.chat([{"role": "user", "content": "hello"}], model="llama3")

    sent_payload = json.loads(route.calls[0].request.content)
    assert sent_payload["model"] == "llama3"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


async def test_connect_error_raises_immediately(ollama_cfg):
    """ConnectError must propagate without retrying (fail fast for dispatcher fallback)."""
    c = OllamaChatClient(ollama_cfg)
    with respx.mock:
        respx.post("http://localhost:11434/api/chat").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        with pytest.raises(httpx.ConnectError):
            await c.chat([{"role": "user", "content": "test"}])
        # Should have been called exactly once (no retry)
        assert respx.calls.call_count == 1
    await c.close()


@respx.mock
async def test_timeout_retries_and_raises(ollama_cfg):
    """TimeoutException should be retried up to 3 times then raise RuntimeError."""
    respx.post("http://localhost:11434/api/chat").mock(
        side_effect=httpx.TimeoutException("timed out")
    )
    c = OllamaChatClient(ollama_cfg)
    with pytest.raises(RuntimeError, match="failed after 3 retries"):
        # Patch asyncio.sleep to avoid real delays
        with patch("codepal.llm.ollama.asyncio.sleep", new_callable=AsyncMock):
            await c.chat([{"role": "user", "content": "test"}])
    assert respx.calls.call_count == 3
    await c.close()


@respx.mock
async def test_http_status_error_raises(client):
    respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.chat([{"role": "user", "content": "test"}])


def test_stream_not_supported(ollama_cfg):
    c = OllamaChatClient(ollama_cfg)
    with pytest.raises(NotImplementedError):
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            c.chat([{"role": "user", "content": "x"}], stream=True)
        )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@respx.mock
async def test_is_available_true(client):
    respx.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": []})
    )
    assert await client.is_available() is True


@respx.mock
async def test_is_available_false_on_connect_error(client):
    respx.get("http://localhost:11434/api/tags").mock(
        side_effect=httpx.ConnectError("refused")
    )
    assert await client.is_available() is False


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


async def test_async_context_manager(ollama_cfg):
    async with OllamaChatClient(ollama_cfg) as c:
        assert isinstance(c, OllamaChatClient)
    # After exit, client should be closed (aclose called)
    assert c._client.is_closed
