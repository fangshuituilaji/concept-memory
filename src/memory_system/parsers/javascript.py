"""JavaScript and JSX structure extraction, including CommonJS exports."""

from __future__ import annotations

from hashlib import sha256

from .base import make_symbol_fact, parse_source
from ..extractor import SourceFacts, _module_name
from ..readers import CodeFile


_FUNCTION_VALUES = {"arrow_function", "function_expression"}


def analyze(code_file: CodeFile) -> SourceFacts:
    tree = parse_source("javascript", code_file.text, code_file.relative_path)
    source = code_file.text.encode("utf-8")
    symbols = []
    _walk(tree.root_node, source, scopes=(), class_scopes=(), out=symbols)
    symbols.sort(key=lambda item: (item.start_line, item.qualified_name, item.signature))
    return SourceFacts(
        file=code_file,
        module=_module_name(code_file.relative_path),
        source_digest=sha256(source).hexdigest(),
        symbols=tuple(symbols),
        language_id="javascript",
    )


def _walk(node, source: bytes, *, scopes: tuple[str, ...], class_scopes: tuple[str, ...], out: list) -> None:
    if node.type == "export_statement":
        value = node.child_by_field_name("value")
        if value is not None and value.type in _FUNCTION_VALUES:
            name = f"default@L{node.start_point.row + 1}"
            out.append(
                make_symbol_fact(
                    node,
                    source,
                    "function",
                    name,
                    name=name,
                    signature_node=value,
                    signature_prefix="export default ",
                )
            )
        for child in node.children:
            _walk(child, source, scopes=scopes, class_scopes=class_scopes, out=out)
        return

    if node.type in {"class_declaration", "class_expression"}:
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source) if name_node is not None else ""
        if name:
            qualified = ".".join((*scopes, name))
            out.append(make_symbol_fact(node, source, "class", qualified, name=name))
        child_scopes = (*scopes, name) if name else scopes
        child_classes = (*class_scopes, name) if name else class_scopes
        for child in node.children:
            _walk(child, source, scopes=child_scopes, class_scopes=child_classes, out=out)
        return

    if node.type == "function_declaration":
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source) if name_node is not None else ""
        if name:
            qualified = ".".join((*scopes, name))
            kind = "method" if scopes and scopes[-1] in class_scopes else "function"
            out.append(make_symbol_fact(node, source, kind, qualified, name=name))
        child_scopes = (*scopes, name) if name else scopes
        for child in node.children:
            _walk(child, source, scopes=child_scopes, class_scopes=class_scopes, out=out)
        return

    if node.type == "method_definition":
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source) if name_node is not None else ""
        if name:
            qualified = ".".join((*scopes, name))
            is_method = bool(scopes and scopes[-1] in class_scopes)
            kind = "constructor" if name == "constructor" and is_method else "method" if is_method else "function"
            out.append(make_symbol_fact(node, source, kind, qualified, name=name))
        return

    if node.type in {"lexical_declaration", "variable_declaration"}:
        for child in node.children:
            if child.type != "variable_declarator":
                continue
            name_node = child.child_by_field_name("name")
            value_node = child.child_by_field_name("value")
            if name_node is None or value_node is None or value_node.type not in _FUNCTION_VALUES:
                continue
            name = _text(name_node, source)
            if not name:
                continue
            qualified = ".".join((*scopes, name))
            out.append(
                make_symbol_fact(
                    node,
                    source,
                    "function",
                    qualified,
                    name=name,
                    signature_node=value_node,
                    signature_prefix=f"const {name} = ",
                )
            )

    if node.type == "assignment_expression":
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        target = _commonjs_export_name(left, source) if left is not None else None
        if target and right is not None and right.type in _FUNCTION_VALUES:
            out.append(
                make_symbol_fact(
                    node,
                    source,
                    "function",
                    target,
                    name=target,
                    signature_node=right,
                    signature_prefix=f"{target} = ",
                )
            )

    for child in node.children:
        _walk(child, source, scopes=scopes, class_scopes=class_scopes, out=out)


def _commonjs_export_name(node, source: bytes) -> str | None:
    if node.type != "member_expression":
        return None
    target = _member_path(node, source)
    return target if target == "module.exports" or target.startswith("exports.") or target.startswith("module.exports.") else None


def _member_path(node, source: bytes) -> str:
    if node.type == "identifier":
        return _text(node, source)
    if node.type == "member_expression":
        obj = node.child_by_field_name("object")
        prop = node.child_by_field_name("property")
        if obj is not None and prop is not None:
            return f"{_member_path(obj, source)}.{_text(prop, source)}"
    return ""


def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8").strip()
