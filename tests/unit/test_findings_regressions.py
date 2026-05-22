"""Regression tests for F1–F4 from docs/manual-testing-findings.md.

Each test pins the user-observable behaviour that the corresponding fix
restored, so the issue cannot silently recur.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import chromadb
import pytest
from typer.testing import CliRunner

from codepal.bugs.store import BugStore
from codepal.cli.main import app as cli_app
from codepal.db.chroma import (
    _chroma_settings,
    distance_to_score,
    make_ephemeral_client,
    query_collection,
)
from codepal.indexer.parser import CodeParser


# ---------------------------------------------------------------------------
# F1 — tree-sitter grammar load must succeed for all supported languages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ext, language, source, expected_symbol",
    [
        (".py", "python", b"def my_func():\n    return 1\n", "my_func"),
        (".js", "javascript", b"function jsFunc() { return 1; }\n", "jsFunc"),
        (".go", "go", b"package main\nfunc goFunc() int { return 1 }\n", "goFunc"),
        (".rs", "rust", b"fn rust_func() -> i32 { 1 }\n", "rust_func"),
    ],
)
def test_f1_tree_sitter_grammars_load_and_extract_symbols(
    ext: str, language: str, source: bytes, expected_symbol: str
) -> None:
    """F1: real grammars must load (no ABI mismatch) and yield function-level chunks.

    The pre-fix bug was a silent fallback to a single whole-file chunk because
    ``tree_sitter`` 0.24 rejected ABI-15 grammars from ``tree_sitter_python`` 0.25.
    If that recurs the assertion ``node_type != 'file'`` fires.
    """
    with tempfile.NamedTemporaryFile(suffix=ext, mode="wb", delete=False) as f:
        f.write(source)
        fpath = f.name

    p = CodeParser()
    chunks = p.parse_file(fpath)

    assert len(chunks) >= 1
    assert chunks[0].language == language
    # The critical assertion: we got a function-level chunk, not a whole-file fallback.
    assert chunks[0].node_type != "file", (
        f"{language} grammar fell back to whole-file (likely ABI mismatch); "
        f"check tree-sitter / tree-sitter-{language} version pin"
    )
    assert chunks[0].symbol_name == expected_symbol


def test_f1_typescript_uses_language_typescript_entrypoint() -> None:
    """F1: tree_sitter_typescript exposes ``language_typescript()`` (not ``language()``).

    Calling the wrong entry function returned None and silently degraded
    TS parsing to whole-file. This pins the correct mapping.
    """
    from codepal.indexer.parser import _LANG_MODULE

    package, fn_name = _LANG_MODULE["typescript"]
    assert package == "tree_sitter_typescript"
    assert fn_name == "language_typescript"

    src = b"function tsFunc(): number { return 1; }\n"
    with tempfile.NamedTemporaryFile(suffix=".ts", mode="wb", delete=False) as f:
        f.write(src)
        fpath = f.name

    chunks = CodeParser().parse_file(fpath)
    assert chunks and chunks[0].node_type != "file"
    assert chunks[0].symbol_name == "tsFunc"


def test_f1_grammar_load_failure_emits_actionable_warning(caplog) -> None:
    """F1: when a grammar fails to load the warning must name the package + exception.

    The pre-fix message was ``Failed to load tree-sitter grammar for python: ...``
    which buried the cause. We now emit ``language=… package=… error=ExceptionClass: …``.
    """
    p = CodeParser()
    with patch("importlib.import_module", side_effect=ImportError("boom")):
        with caplog.at_level(logging.WARNING, logger="codepal.indexer.parser"):
            result = p._get_parser("python")

    assert result is None
    joined = " ".join(rec.message for rec in caplog.records)
    assert "language=python" in joined
    assert "package=tree_sitter_python" in joined
    assert "ImportError" in joined


# ---------------------------------------------------------------------------
# F2 — single scoring convention used by both code search and bug search
# ---------------------------------------------------------------------------


def test_f2_distance_to_score_pins_formula() -> None:
    """F2: the public score formula must be ``max(0, 1 - distance)``."""
    assert distance_to_score(0.0) == 1.0
    assert distance_to_score(0.118) == pytest.approx(0.882, abs=1e-9)
    assert distance_to_score(0.372) == pytest.approx(0.628, abs=1e-9)
    assert distance_to_score(1.0) == 0.0
    assert distance_to_score(1.5) == 0.0  # clipped, never negative
    assert distance_to_score(2.0) == 0.0


@pytest.mark.asyncio
async def test_f2_bug_search_and_query_collection_use_same_formula() -> None:
    """F2: ``BugStore.search`` and ``query_collection`` must score identical
    distances identically. Pre-fix each file inlined its own ``1 - distance``
    expression; that's now a shared helper so this stays true by construction.
    """
    fake_distance = 0.42

    fake_resp = {
        "ids": [["bug-1"]],
        "metadatas": [[{"error": "e", "solution": "s", "context": ""}]],
        "distances": [[fake_distance]],
        "documents": [["doc"]],
    }

    # BugStore.search path
    bug_collection = MagicMock()
    bug_collection.query = AsyncMock(return_value=fake_resp)
    embedder = AsyncMock()
    embedder.embed = AsyncMock(return_value=[0.1] * 8)

    store = BugStore(chroma=MagicMock(), embedder=embedder)
    store._collection = bug_collection
    bug_results = await store.search("q", limit=1)

    # query_collection (code search) path
    code_collection = MagicMock()
    code_collection.query = AsyncMock(
        return_value={
            "ids": [["chunk-1"]],
            "metadatas": [[{"file_path": "f.py"}]],
            "distances": [[fake_distance]],
            "documents": [["doc"]],
        }
    )
    code_results = await query_collection(
        code_collection, query_embedding=[0.1] * 8, n_results=1
    )

    assert bug_results[0].score == code_results[0]["score"]
    assert bug_results[0].score == distance_to_score(fake_distance)


# ---------------------------------------------------------------------------
# F3 — Chroma anonymous telemetry must be disabled
# ---------------------------------------------------------------------------


def test_f3_chroma_settings_disable_anonymous_telemetry() -> None:
    """F3: every Chroma client we build must have ``anonymized_telemetry=False``.

    Pre-fix the default ``Settings`` had it on and Chroma's broken posthog
    integration spammed ``capture() takes 1 positional argument`` for every
    collection call.
    """
    settings = _chroma_settings()
    assert settings.anonymized_telemetry is False


def test_f3_ephemeral_client_no_telemetry_log_spam(caplog) -> None:
    """F3: instantiating + using an ephemeral client must NOT emit the
    posthog telemetry error log."""
    with caplog.at_level(logging.ERROR, logger="chromadb.telemetry.product.posthog"):
        client = make_ephemeral_client()
        # Trigger a collection op — the pre-fix code spammed at this point.
        sync_client = client._c
        sync_client.get_or_create_collection("f3_probe")

    telemetry_errors = [
        r for r in caplog.records if "telemetry" in r.name or "telemetry" in r.message
    ]
    assert telemetry_errors == [], (
        "Chroma telemetry produced log records — Settings(anonymized_telemetry=False) "
        f"may not be wired correctly: {[r.message for r in telemetry_errors]}"
    )


# ---------------------------------------------------------------------------
# F4 — CLI search must read the real /v1/search response fields
# ---------------------------------------------------------------------------


def test_f4_cli_search_reads_current_field_names() -> None:
    """F4: ``codepal search`` formats results using the actual /v1/search
    schema (``file_path``, ``start_line``, ``end_line``, ``symbol_name``, ``text``).

    Pre-fix the CLI still read ``file`` / ``lines`` / ``symbol`` / ``snippet``
    and would ``KeyError`` against a real response.
    """
    real_response = {
        "results": [
            {
                "file_path": "examples/buggy_repo/src/inventory.py",
                "symbol_name": "get_page",
                "start_line": 10,
                "end_line": 18,
                "score": 0.873,
                "text": "def get_page(items, page, size):\n    ...\n",
            }
        ]
    }

    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json = MagicMock(return_value=real_response)

    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=fake_resp)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    runner = CliRunner()
    with patch("httpx.AsyncClient", return_value=fake_client):
        result = runner.invoke(cli_app, ["search", "pagination off-by-one"])

    assert result.exit_code == 0, result.output
    assert "get_page" in result.output
    assert "inventory.py" in result.output
    assert "L10-18" in result.output
    assert "0.873" in result.output
    # The old field names must NOT appear (they would only appear from a KeyError trace)
    assert "KeyError" not in result.output
