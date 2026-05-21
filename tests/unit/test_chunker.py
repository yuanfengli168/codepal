"""Unit tests for indexer/chunker.py.

Tests focus on the token-boundary logic:
- Under-budget symbol → single chunk, chunk_index=0
- Over-budget symbol → multiple chunks, correct indices
- Overlap lines carried forward
- Empty source → handled gracefully
- token_count field populated correctly
"""

from __future__ import annotations

import pytest

from codepal.indexer.chunker import (
    TextChunk,
    _tail_lines,
    chunk_symbol,
    chunk_symbols,
    count_tokens,
)
from codepal.indexer.parser import ParsedChunk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_symbol(source_text: str, symbol_name: str = "test_func") -> ParsedChunk:
    lines = source_text.splitlines()
    return ParsedChunk(
        file_path="src/test.py",
        symbol_name=symbol_name,
        node_type="function_definition",
        language="python",
        start_line=1,
        end_line=len(lines),
        source_text=source_text,
    )


# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------


def test_count_tokens_returns_positive():
    assert count_tokens("hello world") > 0


def test_count_tokens_empty_string():
    # Empty string: tiktoken returns 0, fallback returns max(1, 0//4)=1
    result = count_tokens("")
    assert result >= 0


def test_count_tokens_longer_is_more():
    short = count_tokens("def f(): pass")
    long = count_tokens("def f():\n" + "    x = 1\n" * 50)
    assert long > short


# ---------------------------------------------------------------------------
# Under-budget: single chunk
# ---------------------------------------------------------------------------


def test_under_budget_single_chunk():
    source = "def hello():\n    return 42\n"
    sym = _make_symbol(source)
    chunks = chunk_symbol(sym, token_budget=400)

    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].text == source
    assert chunks[0].symbol_name == "test_func"
    assert chunks[0].file_path == "src/test.py"


def test_under_budget_token_count_populated():
    source = "def hello():\n    return 42\n"
    sym = _make_symbol(source)
    chunks = chunk_symbol(sym, token_budget=400)
    assert chunks[0].token_count == count_tokens(source)


def test_under_budget_preserves_metadata():
    source = "class Foo:\n    pass\n"
    sym = ParsedChunk(
        file_path="lib/foo.py",
        symbol_name="Foo",
        node_type="class_definition",
        language="python",
        start_line=5,
        end_line=6,
        source_text=source,
    )
    chunks = chunk_symbol(sym, token_budget=400)
    assert len(chunks) == 1
    c = chunks[0]
    assert c.language == "python"
    assert c.node_type == "class_definition"
    assert c.start_line == 5
    assert c.end_line == 6


# ---------------------------------------------------------------------------
# Over-budget: multiple chunks
# ---------------------------------------------------------------------------


def test_over_budget_produces_multiple_chunks():
    # 60 lines × ~5 tokens each ≈ 300 tokens → split at budget=100
    source = "\n".join(f"    x_{i} = {i} * 2  # some padding comment here" for i in range(60))
    sym = _make_symbol(source)
    chunks = chunk_symbol(sym, token_budget=100, overlap=0)

    assert len(chunks) > 1
    for c in chunks:
        assert c.token_count <= 120  # allow slight overshoot on last line


def test_over_budget_chunk_indices_sequential():
    source = "\n".join(f"    variable_{i} = {i}" for i in range(80))
    sym = _make_symbol(source)
    chunks = chunk_symbol(sym, token_budget=80, overlap=0)

    indices = [c.chunk_index for c in chunks]
    assert indices == list(range(1, len(chunks) + 1))


def test_over_budget_all_text_preserved():
    """All source lines should appear in at least one chunk."""
    lines = [f"line_{i} = {i}" for i in range(50)]
    source = "\n".join(lines)
    sym = _make_symbol(source)
    chunks = chunk_symbol(sym, token_budget=60, overlap=0)

    combined = "\n".join(c.text for c in chunks)
    for line in lines:
        assert line in combined, f"Line not found in any chunk: {line}"


# ---------------------------------------------------------------------------
# Overlap
# ---------------------------------------------------------------------------


def test_overlap_lines_carried_forward():
    """With overlap > 0, the tail of chunk N should appear at the start of chunk N+1."""
    source = "\n".join(f"x_{i} = {i}" for i in range(60))
    sym = _make_symbol(source)
    chunks = chunk_symbol(sym, token_budget=80, overlap=30)

    if len(chunks) < 2:
        pytest.skip("Source not large enough to produce multiple chunks at this budget")

    # Last line of chunk[0] should appear somewhere in chunk[1]
    last_line_of_first = chunks[0].text.splitlines()[-1]
    assert last_line_of_first in chunks[1].text


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_source():
    sym = _make_symbol("")
    chunks = chunk_symbol(sym, token_budget=400)
    # Empty source should produce one (possibly empty) chunk
    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0


def test_single_very_long_line():
    """A single line exceeding the budget cannot be split further — emitted as-is."""
    long_line = "x = " + "a" * 2000  # definitely > 400 tokens
    sym = _make_symbol(long_line)
    chunks = chunk_symbol(sym, token_budget=100, overlap=0)
    # Can't split a single line; all text must still be preserved
    assert any(long_line in c.text for c in chunks)


# ---------------------------------------------------------------------------
# chunk_symbols (batch)
# ---------------------------------------------------------------------------


def test_chunk_symbols_aggregates():
    s1 = _make_symbol("def foo():\n    pass\n", "foo")
    s2 = _make_symbol("def bar():\n    pass\n", "bar")
    chunks = chunk_symbols([s1, s2], token_budget=400)
    names = [c.symbol_name for c in chunks]
    assert "foo" in names
    assert "bar" in names


def test_chunk_symbols_empty_list():
    assert chunk_symbols([]) == []


# ---------------------------------------------------------------------------
# _tail_lines helper
# ---------------------------------------------------------------------------


def test_tail_lines_respects_budget():
    lines = ["a b c d e\n"] * 20  # ~4 tokens each ≈ 80 tokens total
    tail = _tail_lines(lines, max_tokens=20)
    total = count_tokens("".join(tail))
    assert total <= 20 + count_tokens(lines[0])  # off-by-one line tolerance


def test_tail_lines_empty():
    assert _tail_lines([], max_tokens=50) == []
