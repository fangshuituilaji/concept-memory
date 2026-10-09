"""Go package/type/function/method fact extraction."""

from __future__ import annotations

from hashlib import sha256

from .base import make_symbol_fact, parse_source
from ..extractor import SourceFacts, SymbolFact, _module_name
from ..readers import CodeFile


def analyze(code_file: CodeFile) -> SourceFacts:
    tree = parse_source("go", code_file.text, code_file.relative_path)
    source = code_file.text.encode("utf-8")
    package = next(
        (child for child in tree.root_node.named_children if child.type == "package_clause"),
        None,
    )
    package_name = (
        _text(package.named_children[-1], source)
        if package is not None and package.named_children
        else ""
    )
    scopes = (package_name,) if package_name else ()
    symbols: list[SymbolFact] = []
    _walk(tree.root_node, source, scopes=scopes, out=symbols)
    symbols.sort(key=lambda item: (item.start_line, item.qualified_name, item.signature))
    return SourceFacts(
        file=code_file,
        module=_module_name(code_file.relative_path),
        source_digest=sha256(source).hexdigest(),
        symbols=tuple(symbols),
        language_id="go",
    )


def _walk(node, source: bytes, *, scopes: tuple[str, ...], out: list[SymbolFact]) -> None:
    if node.type == "type_spec":
        name_node = node.child_by_field_name("name")
        type_node = node.child_by_field_name("type")
        name = _text(name_node, source) if name_node is not None else ""
        if name:
            kind = (
                "struct" if type_node is not None and type_node.type == "struct_type"
                else "interface" if type_node is not None and type_node.type == "interface_type"
                else "type"
            )
            qualified = ".".join((*scopes, name))
            out.append(make_symbol_fact(node, source, kind, qualified, name=name))
            if type_node is not None and type_node.type == "interface_type":
                for child in type_node.children:
                    _walk(child, source, scopes=(*scopes, name), out=out)
        return

    if node.type == "function_declaration":
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source) if name_node is not None else ""
        if name:
            qualified = ".".join((*scopes, name))
            out.append(make_symbol_fact(node, source, "function", qualified, name=name))
        return

    if node.type == "method_declaration":
        name_node = node.child_by_field_name("name")
        receiver_node = node.child_by_field_name("receiver")
        name = _text(name_node, source) if name_node is not None else ""
        receiver_type = _receiver_type(receiver_node, source) if receiver_node is not None else ""
        if name and receiver_type:
            qualified = ".".join((*scopes, receiver_type, name))
            out.append(make_symbol_fact(node, source, "method", qualified, name=name))
        return

    if node.type == "method_elem":
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source) if name_node is not None else ""
        if name:
            qualified = ".".join((*scopes, name))
            out.append(make_symbol_fact(node, source, "method", qualified, name=name))
        return

    for child in node.children:
        _walk(child, source, scopes=scopes, out=out)


def _receiver_type(node, source: bytes) -> str:
    for current in _walk_nodes(node):
        if current.type == "type_identifier":
            return _text(current, source)
    return ""


def _walk_nodes(node):
    yield node
    for child in node.children:
        yield from _walk_nodes(child)


def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8").strip()
