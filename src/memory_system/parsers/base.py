"""Shared Tree-sitter loading, parsing, and error reporting."""

from __future__ import annotations

from tree_sitter import Parser

from ..languages import LANGUAGES, ParserDependencyError, load_language


class SourceParseError(ValueError):
    """Raised when a source file does not parse with its declared grammar."""


def parse_source(language_id: str, source: str, relative_path: str):
    """Parse UTF-8 source and reject ERROR/MISSING nodes with a one-based line."""

    tree = parse_tree(language_id, source)
    if tree.root_node.has_error:
        issue = _first_error(tree.root_node)
        line = issue.start_point.row + 1 if issue is not None else 1
        raise SourceParseError(
            f"Cannot parse {relative_path} as {language_id}: line {line}"
        )
    return tree


def parse_tree(language_id: str, source: str):
    """Return a Tree-sitter tree without applying the strict syntax-error policy."""

    spec = LANGUAGES.get(language_id)
    if spec is None:
        raise ValueError(f"Unsupported language id: {language_id}")
    if spec.grammar_candidates:
        raise ValueError(f"Language {language_id!r} requires grammar disambiguation")
    parser = Parser(load_language(language_id))
    return parser.parse(source.encode("utf-8"))


def parse_error_count(tree) -> int:
    """Count Tree-sitter ERROR and MISSING nodes for header-dialect selection."""

    count = 0
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            count += 1
        stack.extend(node.children)
    return count if count or not tree.root_node.has_error else 1


def make_symbol_fact(
    anchor_node,
    source: bytes,
    kind: str,
    qualified_name: str,
    *,
    name: str,
    signature_node=None,
    signature_prefix: str = "",
):
    """Build a one-based source fact, optionally anchoring its signature elsewhere."""

    from ..extractor import SymbolFact

    signature_node = signature_node or anchor_node
    body = signature_node.child_by_field_name("body")
    end_byte = body.start_byte if body is not None else signature_node.end_byte
    signature = signature_prefix + " ".join(
        source[signature_node.start_byte:end_byte].decode("utf-8").split()
    )
    return SymbolFact(
        name=name,
        kind=kind,
        qualified_name=qualified_name,
        start_line=source.count(b"\n", 0, anchor_node.start_byte) + 1,
        end_line=source.count(b"\n", 0, anchor_node.end_byte) + 1,
        signature=signature[:240],
        docstring=None,
    )


def _first_error(node):
    if node.type == "ERROR" or node.is_missing:
        return node
    for child in node.children:
        result = _first_error(child)
        if result is not None:
            return result
    return None


__all__ = [
    "ParserDependencyError",
    "SourceParseError",
    "load_language",
    "make_symbol_fact",
    "parse_error_count",
    "parse_source",
    "parse_tree",
]
