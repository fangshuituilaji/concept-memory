"""C# namespace, type, constructor, method, and property extraction."""

from __future__ import annotations

from hashlib import sha256

from .base import make_symbol_fact, parse_source
from ..extractor import SourceFacts, SymbolFact, _module_name
from ..readers import CodeFile


_TYPE_NODES = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "struct_declaration": "struct",
    "record_declaration": "record",
    "enum_declaration": "enum",
    "delegate_declaration": "delegate",
}


def analyze(code_file: CodeFile) -> SourceFacts:
    tree = parse_source("csharp", code_file.text, code_file.relative_path)
    source = code_file.text.encode("utf-8")
    symbols: list[SymbolFact] = []
    scopes: tuple[str, ...] = ()
    for child in tree.root_node.children:
        if child.type == "file_scoped_namespace_declaration":
            name_node = child.child_by_field_name("name")
            name = _text(name_node, source) if name_node is not None else ""
            if name:
                qualified = ".".join((*scopes, name))
                symbols.append(make_symbol_fact(child, source, "namespace", qualified, name=name))
                scopes = (*scopes, name)
            continue
        _walk(child, source, scopes=scopes, out=symbols)
    symbols.sort(key=lambda item: (item.start_line, item.qualified_name, item.signature))
    return SourceFacts(
        file=code_file,
        module=_module_name(code_file.relative_path),
        source_digest=sha256(source).hexdigest(),
        symbols=tuple(symbols),
        language_id="csharp",
    )


def _walk(node, source: bytes, *, scopes: tuple[str, ...], out: list[SymbolFact]) -> None:
    if node.type in {"namespace_declaration", "file_scoped_namespace_declaration"}:
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source) if name_node is not None else ""
        if name:
            qualified = ".".join((*scopes, name))
            out.append(make_symbol_fact(node, source, "namespace", qualified, name=name))
        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.children:
                _walk(child, source, scopes=(*scopes, name) if name else scopes, out=out)
        else:
            for child in node.children:
                _walk(child, source, scopes=(*scopes, name) if name else scopes, out=out)
        return

    kind = _TYPE_NODES.get(node.type)
    if kind is not None:
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source) if name_node is not None else ""
        qualified = ".".join((*scopes, name)) if name else ".".join(scopes)
        if name:
            out.append(make_symbol_fact(node, source, kind, qualified, name=name))
        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.children:
                _walk(child, source, scopes=(*scopes, name) if name else scopes, out=out)
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
                _walk(child, source, scopes=(*scopes, name) if name else scopes, out=out)
        return

    if node.type in {"property_declaration", "indexer_declaration"}:
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source) if name_node is not None else ""
        if name:
            qualified = ".".join((*scopes, name))
            out.append(make_symbol_fact(node, source, "property", qualified, name=name))
        return

    for child in node.children:
        _walk(child, source, scopes=scopes, out=out)


def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8").strip()
