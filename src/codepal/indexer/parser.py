"""Tree-sitter multi-language parser for extracting code symbols."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Extension → language name mapping
EXT_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
}

# Node type names per language that represent top-level symbols
SYMBOL_NODE_TYPES: dict[str, list[str]] = {
    "python": ["function_definition", "class_definition", "decorated_definition"],
    "javascript": ["function_declaration", "class_declaration", "arrow_function"],
    "typescript": ["function_declaration", "class_declaration", "method_definition"],
    "go": ["function_declaration", "method_declaration", "type_declaration"],
    "rust": ["function_item", "impl_item", "struct_item", "enum_item"],
}


@dataclass
class ParsedSymbol:
    file_path: str
    language: str
    node_type: str
    node_name: str
    start_line: int
    end_line: int
    source: str


class CodeParser:
    """Load tree-sitter grammars and extract symbols from source files."""

    def __init__(self) -> None:
        self._parsers: dict[str, object] = {}
        self._languages: dict[str, object] = {}

    def _get_parser(self, language: str):
        """Lazily load and cache a tree-sitter parser for the given language."""
        if language in self._parsers:
            return self._parsers[language]

        try:
            from tree_sitter import Language, Parser

            # Import the language-specific module
            lang_module_map = {
                "python": "tree_sitter_python",
                "javascript": "tree_sitter_javascript",
                "typescript": "tree_sitter_typescript",
                "go": "tree_sitter_go",
                "rust": "tree_sitter_rust",
            }
            module_name = lang_module_map.get(language)
            if not module_name:
                return None

            import importlib
            lang_module = importlib.import_module(module_name)
            lang_obj = Language(lang_module.language())
            parser = Parser(lang_obj)
            self._parsers[language] = parser
            self._languages[language] = lang_obj
            return parser
        except Exception as exc:
            logger.warning("Failed to load tree-sitter parser for %s: %s", language, exc)
            return None

    def detect_language(self, file_path: str) -> str | None:
        """Return the language name for the given file path, or None if unsupported."""
        ext = Path(file_path).suffix.lower()
        return EXT_TO_LANGUAGE.get(ext)

    def parse_file(self, file_path: str) -> list[ParsedSymbol]:
        """
        Parse a source file and extract top-level symbols.

        Falls back to treating the whole file as a single chunk for unknown languages.
        """
        path = Path(file_path)
        if not path.exists():
            logger.warning("File not found: %s", file_path)
            return []

        try:
            source = path.read_bytes()
        except OSError as exc:
            logger.error("Cannot read %s: %s", file_path, exc)
            return []

        language = self.detect_language(file_path)
        if not language:
            # Fallback: whole file as one chunk
            return [
                ParsedSymbol(
                    file_path=file_path,
                    language="unknown",
                    node_type="file",
                    node_name=path.name,
                    start_line=1,
                    end_line=source.count(b"\n") + 1,
                    source=source.decode("utf-8", errors="replace"),
                )
            ]

        parser = self._get_parser(language)
        if not parser:
            # Fallback to whole file if parser unavailable
            text = source.decode("utf-8", errors="replace")
            return [
                ParsedSymbol(
                    file_path=file_path,
                    language=language,
                    node_type="file",
                    node_name=path.name,
                    start_line=1,
                    end_line=text.count("\n") + 1,
                    source=text,
                )
            ]

        tree = parser.parse(source)
        text = source.decode("utf-8", errors="replace")
        symbols = self._extract_symbols(tree.root_node, text, file_path, language)
        return symbols if symbols else [
            ParsedSymbol(
                file_path=file_path,
                language=language,
                node_type="file",
                node_name=path.name,
                start_line=1,
                end_line=text.count("\n") + 1,
                source=text,
            )
        ]

    def _extract_symbols(
        self, root_node, text: str, file_path: str, language: str
    ) -> list[ParsedSymbol]:
        """Walk the syntax tree and extract top-level symbol nodes."""
        symbols: list[ParsedSymbol] = []
        target_types = set(SYMBOL_NODE_TYPES.get(language, []))
        lines = text.splitlines()

        def walk(node, depth: int = 0) -> None:
            if node.type in target_types:
                name = _extract_node_name(node, text)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                source_lines = lines[start_line - 1 : end_line]
                symbols.append(
                    ParsedSymbol(
                        file_path=file_path,
                        language=language,
                        node_type=node.type,
                        node_name=name,
                        start_line=start_line,
                        end_line=end_line,
                        source="\n".join(source_lines),
                    )
                )
                # Don't recurse into nested definitions for top-level only
                return
            for child in node.children:
                walk(child, depth + 1)

        walk(root_node)
        return symbols


def _extract_node_name(node, text: str) -> str:
    """Attempt to extract the name identifier from a syntax node."""
    for child in node.children:
        if child.type == "identifier" or child.type == "name":
            start = child.start_byte
            end = child.end_byte
            return text[start:end]
    return "anonymous"
