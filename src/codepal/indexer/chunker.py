"""Token-budget chunker — splits ``ParsedChunk`` objects into ``TextChunk`` objects.

Design:
- Default budget: 400 tokens (per spec)
- If a symbol is under budget, emit it as-is (chunk_index=0)
- If over budget, split on line boundaries, never exceeding the budget
- Each split chunk overlaps the previous by up to ``overlap`` tokens worth of lines
- Token counting uses ``tiktoken`` cl100k_base; falls back to len//4 if unavailable
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from codepal.indexer.parser import ParsedChunk

logger = logging.getLogger(__name__)

_DEFAULT_BUDGET = 400
_DEFAULT_OVERLAP = 50


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class TextChunk:
    """A chunk ready for embedding and vector storage."""

    file_path: str
    symbol_name: str
    chunk_index: int  # 0 = whole symbol; ≥1 = split part number
    text: str
    token_count: int
    # Derived from the source ParsedChunk
    language: str = ""
    node_type: str = ""
    start_line: int = 0
    end_line: int = 0


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

_tiktoken_enc = None


def _get_encoder():
    global _tiktoken_enc
    if _tiktoken_enc is None:
        try:
            import tiktoken

            _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            pass  # fallback path used below
    return _tiktoken_enc


def count_tokens(text: str) -> int:
    """Count tokens using tiktoken; fall back to len(text)//4."""
    enc = _get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------


def chunk_symbol(
    parsed: ParsedChunk,
    token_budget: int = _DEFAULT_BUDGET,
    overlap: int = _DEFAULT_OVERLAP,
) -> list[TextChunk]:
    """Split a ``ParsedChunk`` into one or more ``TextChunk`` objects.

    - Under budget → single TextChunk with chunk_index=0.
    - Over budget  → multiple TextChunks with chunk_index=1,2,…
                     Each starts with the overlap tail of the previous chunk.
    """
    total = count_tokens(parsed.source_text)

    if total <= token_budget:
        return [
            TextChunk(
                file_path=parsed.file_path,
                symbol_name=parsed.symbol_name,
                chunk_index=0,
                text=parsed.source_text,
                token_count=total,
                language=parsed.language,
                node_type=parsed.node_type,
                start_line=parsed.start_line,
                end_line=parsed.end_line,
            )
        ]

    # Split on line boundaries
    lines = parsed.source_text.splitlines(keepends=True)
    chunks: list[TextChunk] = []
    chunk_index = 1
    current: list[str] = []
    current_tokens = 0
    chunk_start = parsed.start_line

    for line_no, line in enumerate(lines):
        line_tokens = count_tokens(line)

        # If adding this line would overflow, flush current buffer
        if current_tokens + line_tokens > token_budget and current:
            text = "".join(current)
            chunks.append(
                TextChunk(
                    file_path=parsed.file_path,
                    symbol_name=parsed.symbol_name,
                    chunk_index=chunk_index,
                    text=text,
                    token_count=current_tokens,
                    language=parsed.language,
                    node_type=parsed.node_type,
                    start_line=chunk_start,
                    end_line=chunk_start + len(current) - 1,
                )
            )
            chunk_index += 1

            # Overlap: carry forward last N lines that fit under overlap budget
            overlap_lines = _tail_lines(current, overlap)
            overlap_text = "".join(overlap_lines)
            overlap_tokens = count_tokens(overlap_text)
            current = overlap_lines + [line]
            current_tokens = overlap_tokens + line_tokens
            chunk_start = parsed.start_line + line_no - len(overlap_lines)
        else:
            current.append(line)
            current_tokens += line_tokens

    # Emit remainder
    if current:
        text = "".join(current)
        chunks.append(
            TextChunk(
                file_path=parsed.file_path,
                symbol_name=parsed.symbol_name,
                chunk_index=chunk_index,
                text=text,
                token_count=count_tokens(text),
                language=parsed.language,
                node_type=parsed.node_type,
                start_line=chunk_start,
                end_line=chunk_start + len(current) - 1,
            )
        )

    return chunks


def chunk_symbols(
    symbols: list[ParsedChunk],
    token_budget: int = _DEFAULT_BUDGET,
    overlap: int = _DEFAULT_OVERLAP,
) -> list[TextChunk]:
    """Chunk a list of symbols; convenience wrapper over ``chunk_symbol``."""
    result: list[TextChunk] = []
    for sym in symbols:
        result.extend(chunk_symbol(sym, token_budget=token_budget, overlap=overlap))
    return result


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _tail_lines(lines: list[str], max_tokens: int) -> list[str]:
    """Return the suffix of *lines* whose total token count ≤ *max_tokens*."""
    result: list[str] = []
    tokens = 0
    for line in reversed(lines):
        t = count_tokens(line)
        if tokens + t > max_tokens:
            break
        result.insert(0, line)
        tokens += t
    return result
