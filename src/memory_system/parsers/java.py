"""Java package, type, constructor, and method extraction."""

from __future__ import annotations

from hashlib import sha256

from .base import make_symbol_fact, parse_source
from ..extractor import SourceFacts, SymbolFact, _module_name
from ..readers import CodeFile


_TYPE_DECLARATIONS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "annotation",
}


def analyze(code_file: CodeFile) -> SourceFacts:
    tree = parse_source("java", code_file.text, code_file.relative_path)
    source = code_file.text.encode("utf-8")
    package_node = next(
        (node for node in tree.root_node.named_children if node.type == "package_declaration"),
        None,
    )
    scopes = (_package_name(package_node, source),) if package_node is not None else ()
    symbols: list[SymbolFact] = []
    _walk(tree.root_node, source, scopes=scopes, class_scopes=(), out=symbols)
    symbols.sort(key=lambda item: (item.start_line, item.qualified_name, item.signature))
    return SourceFacts(
        file=code_file,
        module=_module_name(code_file.relative_path),
        source_digest=sha256(source).hexdigest(),
        symbols=tuple(symbols),
        language_id="java",
    )


def _walk(node, source: bytes, *, scopes: tuple[str, ...], class_scopes: tuple[str, ...], out: list[SymbolFact]) -> None:
    kind = _TYPE_DECLARATIONS.get(node.type)
    if kind is not None:
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source) if name_node is not None else ""
        qualified = ".".join((*scopes, name)) if name else ".".join(scopes)
        if name:
            out.append(make_symbol_fact(node, source, kind, qualified, name=name))
        child_scopes = (*scopes, name) if name else scopes
        child_classes = (*class_scopes, name) if name else class_scopes
        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.children:
                _walk(child, source, scopes=child_scopes, class_scopes=child_classes, out=out)
        return

    if node.type in {"constructor_declaration", "method_declaration"}:
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source) if name_node is not None else ""
        if name:
            qualified = ".".join((*scopes, name))
            kind = "constructor" if node.type == "constructor_declaration" else "method"
            out.append(make_symbol_fact(node, source, kind, qualified, name=name))
        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.children:
                _walk(child, source, scopes=(*scopes, name) if name else scopes,
                      class_scopes=class_scopes, out=out)
        return

    for child in node.children:
        _walk(child, source, scopes=scopes, class_scopes=class_scopes, out=out)


def _package_name(node, source: bytes) -> str:
    declaration = source[node.start_byte:node.end_byte].decode("utf-8").strip()
    return declaration.removeprefix("package").strip().removesuffix(";").strip()


def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8").strip()
