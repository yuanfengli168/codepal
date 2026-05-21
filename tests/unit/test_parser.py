"""Unit tests for indexer/parser.py.

Tests run without tree-sitter grammars installed (mocked or skipped).
We test:
- Language detection from file extension
- Whole-file fallback when parser unavailable
- Symbol extraction with a mocked tree-sitter tree
- Unknown extension → whole-file chunk with language="unknown"
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codepal.indexer.parser import (
    CodeParser,
    EXT_TO_LANGUAGE,
    ParsedChunk,
    _extract_name,
    parse_file,
)


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


def test_ext_to_language_python():
    p = CodeParser()
    assert p.detect_language("foo.py") == "python"


def test_ext_to_language_javascript():
    p = CodeParser()
    assert p.detect_language("app.js") == "javascript"
    assert p.detect_language("module.jsx") == "javascript"


def test_ext_to_language_typescript():
    p = CodeParser()
    assert p.detect_language("types.ts") == "typescript"
    assert p.detect_language("component.tsx") == "typescript"


def test_ext_to_language_go():
    p = CodeParser()
    assert p.detect_language("main.go") == "go"


def test_ext_to_language_rust():
    p = CodeParser()
    assert p.detect_language("lib.rs") == "rust"


def test_ext_to_language_unknown():
    p = CodeParser()
    assert p.detect_language("data.json") is None
    assert p.detect_language("README.md") is None


# ---------------------------------------------------------------------------
# Whole-file fallback for unsupported extension
# ---------------------------------------------------------------------------


def test_parse_unsupported_extension_returns_whole_file():
    with tempfile.NamedTemporaryFile(suffix=".cfg", mode="w", delete=False) as f:
        f.write("[section]\nkey = value\n")
        fpath = f.name
    p = CodeParser()
    chunks = p.parse_file(fpath)
    assert len(chunks) == 1
    assert chunks[0].node_type == "file"
    assert chunks[0].language == "unknown"
    assert "key = value" in chunks[0].source_text


def test_parse_missing_file_returns_empty():
    p = CodeParser()
    result = p.parse_file("/nonexistent/path/to/file.py")
    assert result == []


# ---------------------------------------------------------------------------
# Fallback when tree-sitter grammar unavailable
# ---------------------------------------------------------------------------


def test_parse_file_fallback_when_parser_unavailable():
    """When tree-sitter grammar import fails, return a whole-file chunk."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("def hello():\n    return 42\n")
        fpath = f.name

    p = CodeParser()
    with patch.object(p, "_get_parser", return_value=None):
        chunks = p.parse_file(fpath)

    assert len(chunks) == 1
    assert chunks[0].node_type == "file"
    assert chunks[0].language == "python"
    assert "def hello" in chunks[0].source_text


# ---------------------------------------------------------------------------
# Symbol extraction with mocked tree-sitter
# ---------------------------------------------------------------------------


def _make_mock_node(node_type: str, start_line: int, end_line: int, text: bytes, name_text: bytes):
    """Build a minimal mock tree-sitter node."""
    name_child = MagicMock()
    name_child.type = "identifier"
    name_child.start_byte = 0
    name_child.end_byte = len(name_text)

    node = MagicMock()
    node.type = node_type
    node.start_point = (start_line - 1, 0)
    node.end_point = (end_line - 1, 0)
    node.start_byte = 0
    node.end_byte = len(text)
    node.children = [name_child]
    return node, name_text


def test_extract_symbols_from_mocked_tree():
    """Test _extract_symbols directly with a mock AST."""
    source = b"def greet(name):\n    return f'Hello {name}'\n"
    func_name = b"greet"

    name_child = MagicMock()
    name_child.type = "identifier"
    name_child.start_byte = 4
    name_child.end_byte = 9
    name_child.children = []

    func_node = MagicMock()
    func_node.type = "function_definition"
    func_node.start_point = (0, 0)
    func_node.end_point = (1, 0)
    func_node.start_byte = 0
    func_node.end_byte = len(source)
    func_node.children = [name_child]

    root = MagicMock()
    root.type = "module"
    root.children = [func_node]

    p = CodeParser()
    symbols = p._extract_symbols(root, source, "greet.py", "python")

    assert len(symbols) == 1
    assert symbols[0].symbol_name == "greet"
    assert symbols[0].language == "python"
    assert symbols[0].node_type == "function_definition"
    assert symbols[0].start_line == 1


def test_extract_symbols_empty_tree_returns_empty():
    root = MagicMock()
    root.type = "module"
    root.children = []

    p = CodeParser()
    symbols = p._extract_symbols(root, b"", "empty.py", "python")
    assert symbols == []


# ---------------------------------------------------------------------------
# ParsedChunk dataclass
# ---------------------------------------------------------------------------


def test_parsed_chunk_fields():
    chunk = ParsedChunk(
        file_path="src/main.py",
        symbol_name="MyClass",
        node_type="class_definition",
        language="python",
        start_line=10,
        end_line=50,
        source_text="class MyClass:\n    pass\n",
    )
    assert chunk.file_path == "src/main.py"
    assert chunk.symbol_name == "MyClass"
    assert chunk.start_line == 10
    assert chunk.end_line == 50


# ---------------------------------------------------------------------------
# Module-level parse_file convenience function
# ---------------------------------------------------------------------------


def test_module_level_parse_file_real_python():
    """Integration: parse a real Python file with the default singleton parser."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("def foo():\n    pass\n\nclass Bar:\n    pass\n")
        fpath = f.name

    # This may use tree-sitter if installed, or fall back gracefully
    chunks = parse_file(fpath)
    assert len(chunks) >= 1
    # At minimum we should have the file content somewhere
    all_text = " ".join(c.source_text for c in chunks)
    assert "foo" in all_text or "Bar" in all_text or "pass" in all_text
