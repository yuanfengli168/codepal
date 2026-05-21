"""Tree-sitter multi-language parser — extracts top-level code symbols.

Returns list of ``ParsedChunk`` (one per top-level function/class/method).
Falls back to a single whole-file chunk when the language is unsupported
or when tree-sitter grammars are unavailable (e.g. in CI).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Language / extension mapping
# ---------------------------------------------------------------------------

EXT_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
}

# tree-sitter node types considered "top-level symbols" per language
SYMBOL_NODE_TYPES: dict[str, frozenset[str]] = {
    "python": frozenset(["function_definition", "class_definition", "decorated_definition"]),
    "javascript": frozenset(["function_declaration", "class_declaration", "lexical_declaration"]),
    "typescript": frozenset(
        ["function_declaration", "class_declaration", "method_definition", "lexical_declaration"]
    ),
    "go": frozenset(["function_declaration", "method_declaration", "type_declaration"]),
    "rust": frozenset(["function_item", "impl_item", "struct_item", "enum_item", "trait_item"]),
}

# Map language → tree-sitter Python package name
_LANG_MODULE: dict[str, str] = {
    "python": "tree_sitter_python",
    "javascript": "tree_sitter_javascript",
    "typescript": "tree_sitter_typescript",
    "go": "tree_sitter_go",
    "rust": "tree_sitter_rust",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ParsedChunk:
    """A top-level symbol extracted from a source file."""

    file_path: str
    symbol_name: str
    node_type: str
    language: str
    start_line: int  # 1-indexed
    end_line: int  # 1-indexed, inclusive
    source_text: str


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class CodeParser:
    """Lazily loads tree-sitter parsers and extracts top-level symbols."""

    def __init__(self) -> None:
        self._parsers: dict[str, object] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_file(self, file_path: str | Path) -> list[ParsedChunk]:
        """Parse *file_path* and return one ``ParsedChunk`` per top-level symbol.

        Falls back to a single whole-file chunk when:
        - The file extension is not in ``EXT_TO_LANGUAGE``
        - The tree-sitter grammar package is not installed
        - Parsing produces zero symbols (e.g. empty or header-only file)
        """
        path = Path(file_path)
        if not path.exists():
            logger.warning("File not found: %s", path)
            return []

        try:
            source_bytes = path.read_bytes()
        except OSError as exc:
            logger.error("Cannot read %s: %s", path, exc)
            return []

        language = EXT_TO_LANGUAGE.get(path.suffix.lower())
        source_text = source_bytes.decode("utf-8", errors="replace")

        if not language:
            return [self._whole_file_chunk(str(path), source_text, "unknown")]

        parser = self._get_parser(language)
        if parser is None:
            logger.debug("No tree-sitter parser for %s; falling back to whole-file", language)
            return [self._whole_file_chunk(str(path), source_text, language)]

        tree = parser.parse(source_bytes)  # type: ignore[union-attr]
        symbols = self._extract_symbols(tree.root_node, source_bytes, str(path), language)

        if not symbols:
            return [self._whole_file_chunk(str(path), source_text, language)]
        return symbols

    def detect_language(self, file_path: str | Path) -> str | None:
        """Return the language name for *file_path*, or ``None``."""
        return EXT_TO_LANGUAGE.get(Path(file_path).suffix.lower())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_parser(self, language: str):
        """Return a cached tree-sitter Parser for *language*, or ``None``."""
        if language in self._parsers:
            return self._parsers[language]

        module_name = _LANG_MODULE.get(language)
        if not module_name:
            return None

        try:
            import importlib

            from tree_sitter import Language, Parser

            lang_mod = importlib.import_module(module_name)
            lang_obj = Language(lang_mod.language())
            p = Parser(lang_obj)
            self._parsers[language] = p
            return p
        except Exception as exc:
            logger.warning("Failed to load tree-sitter grammar for %s: %s", language, exc)
            self._parsers[language] = None  # cache the failure
            return None

    def _extract_symbols(
        self,
        root_node,
        source_bytes: bytes,
        file_path: str,
        language: str,
    ) -> list[ParsedChunk]:
        """Walk the tree and collect top-level symbol nodes."""
        target_types = SYMBOL_NODE_TYPES.get(language, frozenset())
        symbols: list[ParsedChunk] = []

        def walk(node, depth: int) -> None:
            if node.type in target_types:
                name = _extract_name(node, source_bytes)
                start = node.start_point[0] + 1  # 1-indexed
                end = node.end_point[0] + 1
                text = source_bytes[node.start_byte : node.end_byte].decode(
                    "utf-8", errors="replace"
                )
                symbols.append(
                    ParsedChunk(
                        file_path=file_path,
                        symbol_name=name,
                        node_type=node.type,
                        language=language,
                        start_line=start,
                        end_line=end,
                        source_text=text,
                    )
                )
                return  # don't recurse into nested defs
            for child in node.children:
                walk(child, depth + 1)

        walk(root_node, 0)
        return symbols

    @staticmethod
    def _whole_file_chunk(file_path: str, source_text: str, language: str) -> ParsedChunk:
        lines = source_text.splitlines()
        return ParsedChunk(
            file_path=file_path,
            symbol_name=Path(file_path).name,
            node_type="file",
            language=language,
            start_line=1,
            end_line=max(1, len(lines)),
            source_text=source_text,
        )


# ---------------------------------------------------------------------------
# Module-level convenience (shared singleton)
# ---------------------------------------------------------------------------

_default_parser = CodeParser()


def parse_file(file_path: str | Path) -> list[ParsedChunk]:
    """Parse *file_path* using the default ``CodeParser`` singleton."""
    return _default_parser.parse_file(file_path)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _extract_name(node, source_bytes: bytes) -> str:
    """Best-effort extraction of an identifier name from a node."""
    # Try direct 'name' or 'identifier' child
    for child in node.children:
        if child.type in ("identifier", "name", "field_identifier"):
            return source_bytes[child.start_byte : child.end_byte].decode(
                "utf-8", errors="replace"
            )
    # For decorated_definition, look one level deeper
    for child in node.children:
        for grandchild in child.children:
            if grandchild.type in ("identifier", "name"):
                return source_bytes[grandchild.start_byte : grandchild.end_byte].decode(
                    "utf-8", errors="replace"
                )
    return "<anonymous>"
