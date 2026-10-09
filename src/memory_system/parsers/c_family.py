"""Shared C and C++ declaration extraction and .h grammar selection."""

from __future__ import annotations

from hashlib import sha256

from .base import (
    SourceParseError,
    make_symbol_fact,
    parse_error_count,
    parse_source,
    parse_tree,
)
from ..extractor import SourceFacts, SymbolFact, _module_name
from ..readers import CodeFile


_TYPE_NODES = {
    "struct_specifier": "struct",
    "class_specifier": "class",
    "union_specifier": "union",
    "enum_specifier": "enum",
}
_CPP_ONLY_NODES = {
    "alias_declaration",
    "class_specifier",
    "namespace_definition",
    "operator_name",
    "template_declaration",
    "using_declaration",
}


def analyze(code_file: CodeFile, language_id: str, tree=None) -> SourceFacts:
    tree = tree or parse_source(language_id, code_file.text, code_file.relative_path)
    source = code_file.text.encode("utf-8")
    symbols: list[SymbolFact] = []
    _walk(tree.root_node, source, language_id=language_id, scopes=(), class_scopes=(), out=symbols)
    symbols.sort(key=lambda item: (item.start_line, item.qualified_name, item.signature))
    return SourceFacts(
        file=code_file,
        module=_module_name(code_file.relative_path),
        source_digest=sha256(source).hexdigest(),
        symbols=tuple(symbols),
        language_id=language_id,
    )


def analyze_header(code_file: CodeFile) -> SourceFacts:
    """Choose C/C++ for a shared .h file by the grammar error count; ties go to C."""

    trees = {
        language_id: parse_tree(language_id, code_file.text)
        for language_id in ("c", "cpp")
    }
    counts = {language_id: parse_error_count(tree) for language_id, tree in trees.items()}
    if counts["c"] and counts["cpp"]:
        language_id = min(("c", "cpp"), key=lambda item: counts[item])
        tree = trees[language_id]
        from .base import _first_error

        issue = _first_error(tree.root_node)
        line = issue.start_point.row + 1 if issue is not None else 1
        raise SourceParseError(
            f"Neither C nor C++ grammar can parse {code_file.relative_path}: line {line}"
        )
    if counts["c"] == 0 and counts["cpp"] == 0:
        language_id = "cpp" if _contains_cpp_construct(trees["cpp"].root_node) else "c"
        return analyze(code_file, language_id, tree=trees[language_id])
    if counts["c"] == 0:
        return analyze(code_file, "c", tree=trees["c"])
    return analyze(code_file, "cpp", tree=trees["cpp"])


def _contains_cpp_construct(root) -> bool:
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in _CPP_ONLY_NODES:
            return True
        stack.extend(node.children)
    return False


def _walk(node, source: bytes, *, language_id: str, scopes: tuple[str, ...],
          class_scopes: tuple[str, ...], out: list[SymbolFact]) -> None:
    if node.type == "namespace_definition" and language_id == "cpp":
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source) if name_node is not None else ""
        if name:
            qualified = _qualify(scopes, name)
            out.append(make_symbol_fact(node, source, "namespace", qualified, name=name))
        body = node.child_by_field_name("body")
        child_scopes = (*scopes, name) if name else scopes
        if body is not None:
            for child in body.children:
                _walk(child, source, language_id=language_id, scopes=child_scopes,
                      class_scopes=class_scopes, out=out)
        return

    if node.type in _TYPE_NODES:
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source) if name_node is not None else ""
        if name:
            qualified = _qualify(scopes, name)
            out.append(make_symbol_fact(node, source, _TYPE_NODES[node.type], qualified, name=name))
        body = node.child_by_field_name("body")
        child_scopes = (*scopes, name) if name else scopes
        is_class = language_id == "cpp" and node.type in {"class_specifier", "struct_specifier"}
        child_classes = (*class_scopes, name) if name and is_class else class_scopes
        if body is not None:
            for child in body.children:
                _walk(child, source, language_id=language_id, scopes=child_scopes,
                      class_scopes=child_classes, out=out)
        return

    if node.type == "alias_declaration" and language_id == "cpp":
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source) if name_node is not None else ""
        if name:
            qualified = _qualify(scopes, name)
            out.append(make_symbol_fact(node, source, "type", qualified, name=name))
        return

    if node.type == "type_definition":
        declarator = node.child_by_field_name("declarator")
        name = _alias_name(declarator, source) if declarator is not None else ""
        if name:
            qualified = _qualify(scopes, name)
            out.append(make_symbol_fact(node, source, "type", qualified, name=name))
        for child in node.children:
            _walk(child, source, language_id=language_id, scopes=scopes,
                  class_scopes=class_scopes, out=out)
        return

    if node.type == "function_definition":
        declarator = node.child_by_field_name("declarator")
        name = _function_name(declarator, source) if declarator is not None else ""
        if name:
            qualified = _qualify(scopes, name)
            out.append(_function_fact(node, source, name, qualified, language_id, class_scopes))
        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.children:
                _walk(child, source, language_id=language_id, scopes=scopes,
                      class_scopes=class_scopes, out=out)
        return

    if node.type in {"declaration", "field_declaration"}:
        for child in node.named_children:
            if child.type != "function_declarator":
                continue
            name = _function_name(child, source)
            if name:
                qualified = _qualify(scopes, name)
                out.append(_function_fact(node, source, name, qualified, language_id, class_scopes))
        return

    for child in node.children:
        _walk(child, source, language_id=language_id, scopes=scopes,
              class_scopes=class_scopes, out=out)


def _function_fact(node, source: bytes, name: str, qualified: str, language_id: str,
                   class_scopes: tuple[str, ...]) -> SymbolFact:
    leaf = name.rsplit("::", 1)[-1]
    owner = qualified.rsplit("::", 1)[-2] if "::" in qualified else ""
    is_method = language_id == "cpp" and (bool(class_scopes) or "::" in name)
    if is_method and leaf == owner:
        kind = "constructor"
    elif is_method and leaf.startswith("~"):
        kind = "destructor"
    else:
        kind = "method" if is_method else "function"
    return make_symbol_fact(node, source, kind, qualified, name=leaf)


def _function_name(node, source: bytes) -> str:
    if node is None or node.type != "function_declarator":
        return ""
    return _declarator_name(node.child_by_field_name("declarator"), source)


def _declarator_name(node, source: bytes) -> str:
    if node is None:
        return ""
    if node.type in {"identifier", "field_identifier", "qualified_identifier",
                     "operator_name", "destructor_name", "template_function"}:
        return _text(node, source)
    if node.type == "parenthesized_declarator":
        child = node.child_by_field_name("declarator")
        if child is not None and child.type == "pointer_declarator":
            return ""
        return _declarator_name(child, source)
    return ""


def _alias_name(node, source: bytes) -> str:
    if node is None:
        return ""
    if node.type in {"identifier", "type_identifier", "field_identifier"}:
        return _text(node, source)
    for child in reversed(node.named_children):
        name = _alias_name(child, source)
        if name:
            return name
    return ""


def _qualify(scopes: tuple[str, ...], name: str) -> str:
    return "::".join((*scopes, *[part for part in name.split("::") if part]))


def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8").strip()
