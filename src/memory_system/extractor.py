"""Python source analysis used as factual input to semantic synthesis.

This module deliberately does not emit one card per symbol. It only extracts
stable facts (symbols, line ranges, signatures and docstrings) that a semantic
synthesizer can use to produce a small set of file-level concepts.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from .readers import CodeFile


@dataclass(frozen=True, slots=True)
class SymbolFact:
    """A source fact used to anchor a model-generated concept."""

    name: str
    kind: str
    qualified_name: str
    start_line: int
    end_line: int
    signature: str
    docstring: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "qualified_name": self.qualified_name,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "signature": self.signature,
            "docstring": self.docstring,
        }


@dataclass(frozen=True, slots=True)
class SourceFacts:
    """A complete, bounded representation of one source file."""

    file: CodeFile
    module: str
    source_digest: str
    symbols: tuple[SymbolFact, ...]

    def to_prompt_text(self, *, max_source_chars: int = 120_000) -> str:
        source = self.file.text
        truncated = len(source) > max_source_chars
        if truncated:
            source = source[:max_source_chars]
        symbol_index = "\n".join(
            f"- {symbol.qualified_name} [{symbol.kind}] "
            f"lines {symbol.start_line}-{symbol.end_line}: {symbol.signature}"
            + (f"；docstring: {symbol.docstring}" if symbol.docstring else "")
            for symbol in self.symbols
        )
        truncation_note = "\n[源码因长度限制被截断；请仅依据可见源码和符号索引。]" if truncated else ""
        return (
            f"文件: {self.file.relative_path}\n"
            f"模块: {self.module}\n"
            f"符号索引:\n{symbol_index or '- 无显式类或函数符号'}\n\n"
            f"源码:\n```python\n{source}\n```{truncation_note}"
        )


class ConceptExtractionError(ValueError):
    """Raised when a source file cannot be parsed as Python."""


class PythonSourceAnalyzer:
    """Extract source facts without prematurely turning symbols into concepts."""

    def analyze(self, code_file: CodeFile) -> SourceFacts:
        try:
            tree = ast.parse(code_file.text, filename=code_file.relative_path)
        except SyntaxError as exc:
            raise ConceptExtractionError(
                f"Cannot parse {code_file.relative_path}: line {exc.lineno}: {exc.msg}"
            ) from exc

        symbols: list[SymbolFact] = []
        for node in tree.body:
            symbols.extend(self._visit(node, scopes=(), class_scopes=()))
        return SourceFacts(
            file=code_file,
            module=_module_name(code_file.relative_path),
            source_digest=sha256(code_file.text.encode("utf-8")).hexdigest(),
            symbols=tuple(symbols),
        )

    def _visit(
        self,
        node: ast.AST,
        *,
        scopes: tuple[str, ...],
        class_scopes: tuple[str, ...],
    ) -> list[SymbolFact]:
        if isinstance(node, ast.ClassDef):
            qualified_name = ".".join((*scopes, node.name))
            result = [_symbol_fact(node, "class", qualified_name)]
            child_scopes = (*scopes, node.name)
            child_classes = (*class_scopes, node.name)
            for child in node.body:
                result.extend(self._visit(child, scopes=child_scopes, class_scopes=child_classes))
            return result
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualified_name = ".".join((*scopes, node.name))
            kind = "method" if scopes and scopes[-1] in class_scopes else "function"
            result = [_symbol_fact(node, kind, qualified_name)]
            child_scopes = (*scopes, node.name)
            for child in node.body:
                result.extend(self._visit(child, scopes=child_scopes, class_scopes=class_scopes))
            return result
        return []


def _symbol_fact(node: ast.AST, kind: str, qualified_name: str) -> SymbolFact:
    try:
        signature = ast.unparse(node).splitlines()[0]
    except (AttributeError, TypeError):
        signature = qualified_name
    return SymbolFact(
        name=getattr(node, "name", qualified_name.rsplit(".", 1)[-1]),
        kind=kind,
        qualified_name=qualified_name,
        start_line=getattr(node, "lineno", 1),
        end_line=getattr(node, "end_lineno", getattr(node, "lineno", 1)),
        signature=signature,
        docstring=ast.get_docstring(node, clean=True),
    )


def _module_name(relative_path: str) -> str:
    path = Path(relative_path)
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or path.stem


# ---------------------------------------------------------------------------
# Multi-language tree-sitter support
# ---------------------------------------------------------------------------

_TS_SYMBOL_TYPES = frozenset({
    "class_declaration",
    "function_declaration",
    "method_definition",
    "lexical_declaration",
})
_PY_LANG = None
_TS_LANG = None
_JS_LANG = None


def _analyze_markdown(code_file: CodeFile) -> SourceFacts:
    """Anchor markdown concepts on headings; the body feeds the synthesizer."""
    symbols: list[SymbolFact] = []
    inside_fence = False
    for line_number, line in enumerate(code_file.text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            inside_fence = not inside_fence
            continue
        if inside_fence:
            continue
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                symbols.append(
                    SymbolFact(
                        name=heading,
                        kind="heading",
                        qualified_name=f"{_module_name(code_file.relative_path)} · {heading}",
                        start_line=line_number,
                        end_line=line_number,
                        signature=stripped,
                        docstring=None,
                    )
                )
    source_bytes = code_file.text.encode("utf-8")
    return SourceFacts(
        file=code_file,
        module=_module_name(code_file.relative_path),
        source_digest=sha256(source_bytes).hexdigest(),
        symbols=tuple(symbols),
    )


def _get_language(suffix: str):
    global _PY_LANG, _TS_LANG, _JS_LANG
    from tree_sitter import Language
    if suffix == ".py":
        if _PY_LANG is None:
            import tree_sitter_python as tspython
            _PY_LANG = Language(tspython.language())
        return _PY_LANG
    if suffix in (".ts", ".tsx"):
        if _TS_LANG is None:
            import tree_sitter_typescript as tsts
            _TS_LANG = Language(tsts.language_typescript())
        return _TS_LANG
    if suffix in (".js", ".mjs"):
        if _JS_LANG is None:
            import tree_sitter_javascript as tsjs
            _JS_LANG = Language(tsjs.language())
        return _JS_LANG
    return None


class TreeSitterSourceAnalyzer:
    """Extract source facts for Python, TypeScript and JavaScript files.

    Uses the built-in ``ast`` module for Python (more accurate) and
    tree-sitter for TS/JS. Both produce the same ``SourceFacts`` output.
    """

    def __init__(self):
        self._python = PythonSourceAnalyzer()

    def analyze(self, code_file: CodeFile) -> SourceFacts:
        # Python stays on the stdlib AST path so the first-phase Python install
        # does not require the optional Tree-sitter extras.
        if code_file.path.suffix == ".py":
            return self._python.analyze(code_file)
        if code_file.path.suffix == ".md":
            return _analyze_markdown(code_file)
        lang = _get_language(code_file.path.suffix)
        if lang is None:
            raise ConceptExtractionError(
                f"Unsupported language: {code_file.path.suffix}"
            )
        return self._analyze_ts_js(code_file, lang)

    def _analyze_ts_js(self, code_file: CodeFile, lang) -> SourceFacts:
        from tree_sitter import Parser

        parser = Parser(lang)
        source_bytes = code_file.text.encode("utf-8")
        tree = parser.parse(source_bytes)
        root = tree.root_node
        symbols: list[SymbolFact] = []
        self._walk(root, source_bytes, scopes=(), class_scopes=(), out=symbols)
        return SourceFacts(
            file=code_file,
            module=_module_name(code_file.relative_path),
            source_digest=sha256(source_bytes).hexdigest(),
            symbols=tuple(symbols),
        )

    def _walk(self, node, src: bytes, *, scopes, class_scopes, out: list[SymbolFact]) -> None:
        if node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            name = src[name_node.start_byte:name_node.end_byte].decode() if name_node else ""
            qualified = ".".join((*scopes, name)) if name else ".".join(scopes)
            out.append(self._fact(node, src, "class", qualified))
            child_scopes = (*scopes, name) if name else scopes
            child_classes = (*class_scopes, name) if name else class_scopes
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    self._walk(child, src, scopes=child_scopes, class_scopes=child_classes, out=out)
            return

        if node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            name = src[name_node.start_byte:name_node.end_byte].decode() if name_node else ""
            qualified = ".".join((*scopes, name)) if name else ".".join(scopes)
            kind = "method" if scopes and scopes[-1] in class_scopes else "function"
            out.append(self._fact(node, src, kind, qualified))
            return

        if node.type == "method_definition":
            name_node = node.child_by_field_name("name")
            name = src[name_node.start_byte:name_node.end_byte].decode() if name_node else ""
            qualified = ".".join((*scopes, name)) if name else ".".join(scopes)
            out.append(self._fact(node, src, "method", qualified))
            return

        if node.type == "lexical_declaration" or node.type == "variable_declaration":
            # Arrow functions / consts assigned to functions
            for child in node.children:
                if child.type != "variable_declarator":
                    continue
                name_node = child.child_by_field_name("name")
                value_node = child.child_by_field_name("value")
                if not name_node or not value_node:
                    continue
                if value_node.type not in ("arrow_function", "function_expression"):
                    continue
                value_src = src[value_node.start_byte:value_node.end_byte].decode()
                name = src[name_node.start_byte:name_node.end_byte].decode()
                qualified = ".".join((*scopes, name)) if name else ".".join(scopes)
                out.append(SymbolFact(
                    name=name,
                    kind="function",
                    qualified_name=qualified,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    signature=f"const {name} = {value_src[:80]}...",
                    docstring=None,
                ))
            return

        for child in node.children:
            self._walk(child, src, scopes=scopes, class_scopes=class_scopes, out=out)

    def _fact(self, node, src: bytes, kind: str, qualified_name: str) -> SymbolFact:
        name_node = node.child_by_field_name("name")
        name = src[name_node.start_byte:name_node.end_byte].decode() if name_node else qualified_name.rsplit(".", 1)[-1]
        first_line = src[node.start_byte:].split(b"\n")[0].decode(errors="replace")[:120]
        return SymbolFact(
            name=name,
            kind=kind,
            qualified_name=qualified_name,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            signature=first_line,
            docstring=None,
        )
