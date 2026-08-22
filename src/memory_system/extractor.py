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
