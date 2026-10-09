"""Rust modules, type declarations, traits, and impl method extraction."""

from __future__ import annotations

from hashlib import sha256

from .base import make_symbol_fact, parse_source
from ..extractor import SourceFacts, SymbolFact, _module_name
from ..readers import CodeFile


_DECLARATIONS = {
    "mod_item": "module",
    "struct_item": "struct",
    "enum_item": "enum",
    "union_item": "union",
    "trait_item": "trait",
    "type_item": "type",
}

def analyze(code_file: CodeFile) -> SourceFacts:
    return _analyze_tree(code_file)


def _analyze_tree(code_file: CodeFile) -> SourceFacts:
    tree = parse_source("rust", code_file.text, code_file.relative_path)
    source = code_file.text.encode("utf-8")
    symbols: list[SymbolFact] = []
    _walk(tree.root_node, source, scopes=(), method_scope=False, out=symbols)
    return SourceFacts(
        file=code_file,
        module=_module_name(code_file.relative_path),
        source_digest=sha256(source).hexdigest(),
        symbols=tuple(symbols),
        language_id="rust",
    )


def _walk(node, source: bytes, *, scopes: tuple[str, ...], method_scope: bool, out: list[SymbolFact]) -> None:
    """Iterate named syntax nodes without recursive Python/native stack growth."""

    pending = [(node, scopes, method_scope)]
    while pending:
        current, current_scopes, current_method_scope = pending.pop()
        kind = _DECLARATIONS.get(current.type)
        if kind is not None:
            name_node = current.child_by_field_name("name")
            name = _text(name_node, source) if name_node is not None else ""
            qualified = "::".join((*current_scopes, name)) if name else "::".join(current_scopes)
            if name:
                out.append(make_symbol_fact(current, source, kind, qualified, name=name))
            body = current.child_by_field_name("body")
            child_scopes = (*current_scopes, name) if name else current_scopes
            if body is not None:
                pending.extend(
                    (child, child_scopes, current.type == "trait_item")
                    for child in reversed(body.named_children)
                )
            continue

        if current.type == "impl_item":
            type_node = current.child_by_field_name("type")
            trait_node = current.child_by_field_name("trait")
            type_name = _first_type_name(type_node, source) if type_node is not None else ""
            trait_name = _first_type_name(trait_node, source) if trait_node is not None else ""
            owner = f"{type_name} as {trait_name}" if trait_name and type_name else type_name
            child_scopes = (*current_scopes, owner) if owner else current_scopes
            body = current.child_by_field_name("body")
            if body is not None:
                pending.extend(
                    (child, child_scopes, True) for child in reversed(body.named_children)
                )
            continue

        if current.type in {"function_item", "function_signature_item"}:
            name_node = current.child_by_field_name("name")
            name = _text(name_node, source) if name_node is not None else ""
            if name:
                qualified = "::".join((*current_scopes, name))
                fact_kind = "method" if current_method_scope else "function"
                out.append(make_symbol_fact(current, source, fact_kind, qualified, name=name))
            body = current.child_by_field_name("body")
            child_scopes = (*current_scopes, name) if name else current_scopes
            if body is not None:
                pending.extend(
                    (child, child_scopes, False) for child in reversed(body.named_children)
                )
            continue

        pending.extend(
            (child, current_scopes, current_method_scope)
            for child in reversed(current.named_children)
        )


def _first_type_name(node, source: bytes) -> str:
    pending = [node]
    while pending:
        current = pending.pop()
        if current.type in {"type_identifier", "scoped_type_identifier", "identifier"}:
            return _text(current, source).split("::")[-1]
        pending.extend(reversed(current.named_children))
    return ""


def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8").strip()
