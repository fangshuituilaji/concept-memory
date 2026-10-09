from __future__ import annotations

from pathlib import Path

from memory_system.extractor import TreeSitterSourceAnalyzer
from memory_system.readers import CodeFile


def _facts(filename: str, source: str):
    path = Path(filename)
    return TreeSitterSourceAnalyzer().analyze(
        CodeFile(path=path, relative_path=filename, text=source)
    )


def test_javascript_extracts_esm_commonjs_functions_and_class_methods():
    source = (
        "// function fake() {}\n"
        "export function greet(name) { return name; }\n"
        "export class Store {\n"
        "  get(key) { return key; }\n"
        "}\n"
        "const make = (name) => ({ name });\n"
        "const legacy = function legacyLoad(path) { return path; };\n"
        "exports.lookup = (id) => id;\n"
        "module.exports = function start() { return 1; };\n"
    )
    facts = _facts("api.cjs", source)
    symbols = {symbol.qualified_name: symbol for symbol in facts.symbols}
    assert "greet" in symbols and symbols["greet"].kind == "function"
    assert "Store" in symbols and symbols["Store"].kind == "class"
    assert "Store.get" in symbols and symbols["Store.get"].kind == "method"
    assert "make" in symbols and symbols["make"].kind == "function"
    assert "legacy" in symbols and "legacyLoad" in symbols["legacy"].signature
    assert "exports.lookup" in symbols
    assert symbols["exports.lookup"].kind == "function"
    assert "module.exports" in symbols
    assert symbols["module.exports"].kind == "function"
    assert not any("fake" in symbol.qualified_name for symbol in facts.symbols)


def test_jsx_grammar_indexes_named_and_default_components():
    source = (
        "export const App = () => <main>Hello</main>;\n"
        "export default function Panel() { return <section />; }\n"
        "export default () => <aside />;\n"
    )
    facts = _facts("App.jsx", source)
    symbols = {symbol.qualified_name: symbol for symbol in facts.symbols}
    assert symbols["App"].kind == "function"
    assert (symbols["App"].start_line, symbols["App"].end_line) == (1, 1)
    assert symbols["Panel"].kind == "function"
    assert (symbols["Panel"].start_line, symbols["Panel"].end_line) == (2, 2)
    assert "default@L3" in symbols
    assert (symbols["default@L3"].start_line, symbols["default@L3"].end_line) == (3, 3)
