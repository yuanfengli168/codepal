"""Token-budget chunker with overlap for large code symbols."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from codepal.indexer.parser import ParsedSymbol

logger = logging.getLogger(__name__)


@dataclass
class CodeChunk:
    """A chunk ready for embedding and storage."""
    file_path: str
    language: str
    node_type: str
    node_name: str
    start_line: int
    end_line: int
    source: str
    chunk_index: int = 0  # 0 = whole symbol; >0 = split part


def _count_tokens(text: str) -> int:
    """Approximate token count using tiktoken (cl100k_base)."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        # Fallback: ~4 chars per token
        return len(text) // 4


def chunk_symbol(
    symbol: ParsedSymbol,
    token_budget: int = 512,
    overlap: int = 50,
) -> list[CodeChunk]:
    """
    Split a ParsedSymbol into chunks if it exceeds the token budget.
    Returns a list of CodeChunk objects (usually just one for typical functions).
    """
    lines = symbol.source.splitlines()
    total_tokens = _count_tokens(symbol.source)

    if total_tokens <= token_budget:
        return [
            CodeChunk(
                file_path=symbol.file_path,
                language=symbol.language,
                node_type=symbol.node_type,
                node_name=symbol.node_name,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                source=symbol.source,
                chunk_index=0,
            )
        ]

    # Split by lines, accumulating up to token_budget tokens
    chunks: list[CodeChunk] = []
    current_lines: list[str] = []
    current_tokens = 0
    chunk_start_line = symbol.start_line
    chunk_index = 1

    for i, line in enumerate(lines):
        line_tokens = _count_tokens(line)
        if current_tokens + line_tokens > token_budget and current_lines:
            # Emit current chunk
            chunk_source = "\n".join(current_lines)
            chunks.append(
                CodeChunk(
                    file_path=symbol.file_path,
                    language=symbol.language,
                    node_type=symbol.node_type,
                    node_name=f"{symbol.node_name}[{chunk_index}]",
                    start_line=chunk_start_line,
                    end_line=chunk_start_line + len(current_lines) - 1,
                    source=chunk_source,
                    chunk_index=chunk_index,
                )
            )
            chunk_index += 1
            # Overlap: keep last `overlap` tokens worth of lines
            overlap_lines = _trim_to_tokens(current_lines, overlap)
            current_lines = overlap_lines + [line]
            current_tokens = _count_tokens("\n".join(current_lines))
            chunk_start_line = symbol.start_line + i - len(overlap_lines)
        else:
            current_lines.append(line)
            current_tokens += line_tokens

    # Emit remainder
    if current_lines:
        chunks.append(
            CodeChunk(
                file_path=symbol.file_path,
                language=symbol.language,
                node_type=symbol.node_type,
                node_name=f"{symbol.node_name}[{chunk_index}]",
                start_line=chunk_start_line,
                end_line=chunk_start_line + len(current_lines) - 1,
                source="\n".join(current_lines),
                chunk_index=chunk_index,
            )
        )

    return chunks


def _trim_to_tokens(lines: list[str], max_tokens: int) -> list[str]:
    """Return the last N lines that fit within max_tokens."""
    result: list[str] = []
    tokens = 0
    for line in reversed(lines):
        t = _count_tokens(line)
        if tokens + t > max_tokens:
            break
        result.insert(0, line)
        tokens += t
    return result
