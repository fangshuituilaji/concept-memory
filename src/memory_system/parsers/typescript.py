"""TypeScript and TSX structure extraction for semantic source facts."""

from __future__ import annotations

from hashlib import sha256

from .base import make_symbol_fact, parse_source
from ..extractor import SourceFacts, SymbolFact, _module_name
from ..readers import CodeFile


_DECLARATIONS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "type_alias_declaration": "type",
    "internal_module": "namespace",
}
_CLASS_LIKE = {"class_declaration", "interface_declaration", "enum_declaration"}
_FUNCTION_DECLARATIONS = {"function_declaration", "function_signature"}
_METHOD_DECLARATIONS = {"method_definition", "method_signature", "abstract_method_signature"}


def analyze(code_file: CodeFile, language_id: str) -> SourceFacts:
    tree = parse_source(language_id, code_file.text, code_file.relative_path)
    source = code_file.text.encode("utf-8")
    symbols: list[SymbolFact] = []
    _walk(tree.root_node, source, scopes=(), class_scopes=(), out=symbols)
    symbols.sort(key=lambda item: (item.start_line, item.qualified_name, item.signature))
    return SourceFacts(
        file=code_file,
        module=_module_name(code_file.relative_path),
        source_digest=sha256(source).hexdigest(),
        symbols=tuple(symbols),
        language_id=language_id,
    )


def _walk(node, source: bytes, *, scopes: tuple[str, ...], class_scopes: tuple[str, ...], out: list[SymbolFact]) -> None:
    kind = _DECLARATIONS.get(node.type)
    if kind is not None:
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source) if name_node is not None else ""
        qualified = ".".join((*scopes, name)) if name else ".".join(scopes)
        if name:
            out.append(_fact(node, source, kind, qualified, name=name))
        child_scopes = (*scopes, name) if name else scopes
        child_classes = (
            (*class_scopes, name) if name and node.type in _CLASS_LIKE else class_scopes
        )
        for child in node.children:
            _walk(child, source, scopes=child_scopes, class_scopes=child_classes, out=out)
        return

    if node.type in _FUNCTION_DECLARATIONS | _METHOD_DECLARATIONS:
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source) if name_node is not None else ""
        if name:
            qualified = ".".join((*scopes, name))
            if node.type in _METHOD_DECLARATIONS:
                kind = "constructor" if name == "constructor" else "method"
            else:
                kind = "method" if scopes and scopes[-1] in class_scopes else "function"
            out.append(_fact(node, source, kind, qualified, name=name))
        body = node.child_by_field_name("body")
        if body is not None:
            child_scopes = (*scopes, name) if name else scopes
            _walk(body, source, scopes=child_scopes, class_scopes=class_scopes, out=out)
        return

    if node.type in {"lexical_declaration", "variable_declaration"}:
        for child in node.children:
            if child.type != "variable_declarator":
                continue
            name_node = child.child_by_field_name("name")
            value_node = child.child_by_field_name("value")
            if name_node is None or value_node is None:
                continue
            if value_node.type not in {"arrow_function", "function_expression"}:
                continue
            name = _text(name_node, source)
            if not name:
                continue
            qualified = ".".join((*scopes, name))
            out.append(_arrow_fact(node, value_node, source, name, qualified))

    for child in node.children:
        _walk(child, source, scopes=scopes, class_scopes=class_scopes, out=out)


def _fact(node, source: bytes, kind: str, qualified: str, *, name: str) -> SymbolFact:
    return make_symbol_fact(node, source, kind, qualified, name=name)


def _arrow_fact(node, value_node, source: bytes, name: str, qualified: str) -> SymbolFact:
    return make_symbol_fact(
        node,
        source,
        "function",
        qualified,
        name=name,
        signature_node=value_node,
        signature_prefix=f"const {name} = ",
    )


def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8").strip()
