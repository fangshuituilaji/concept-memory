from __future__ import annotations

from pathlib import Path

from memory_system.extractor import TreeSitterSourceAnalyzer
from memory_system.readers import CodeFile


def _facts(source: str):
    path = Path("src/com/example/Store.java")
    return TreeSitterSourceAnalyzer().analyze(
        CodeFile(path=path, relative_path=path.as_posix(), text=source)
    )


def test_java_extracts_package_nested_types_constructors_and_overloads():
    source = (
        "package com.example;\n"
        "class Store<T> {\n"
        "  Store() { }\n"
        "  Store(T initial) { }\n"
        "  T get() { return null; }\n"
        "  T get(String ignored) { return null; }\n"
        "  interface Reader { T read(); }\n"
        "  enum Status { READY, BUSY }\n"
        "  record Entry(String key, String value) {}\n"
        "  static class Inner { void run() {} }\n"
        "}\n"
        "interface API { String read(); }\n"
        "public record PublicEntry(String key) {}\n"
    )
    facts = _facts(source)
    symbols = {symbol.qualified_name: symbol for symbol in facts.symbols}
    assert "com.example.Store" in symbols
    assert symbols["com.example.Store"].kind == "class"
    assert "com.example.Store.Store" in symbols
    constructors = [s for s in facts.symbols if s.qualified_name == "com.example.Store.Store"]
    assert len(constructors) == 2
    assert all(symbol.kind == "constructor" for symbol in constructors)
    assert [symbol.start_line for symbol in constructors] == [3, 4]
    methods = [symbol for symbol in facts.symbols if symbol.qualified_name == "com.example.Store.get"]
    assert len(methods) == 2
    assert all(symbol.kind == "method" for symbol in methods)
    assert [symbol.start_line for symbol in methods] == [5, 6]
    assert symbols["com.example.Store.Reader"].kind == "interface"
    assert symbols["com.example.Store.Reader.read"].kind == "method"
    assert symbols["com.example.Store.Status"].kind == "enum"
    assert symbols["com.example.Store.Entry"].kind == "record"
    assert symbols["com.example.Store.Inner.run"].kind == "method"
    assert symbols["com.example.API"].kind == "interface"
    assert symbols["com.example.PublicEntry"].kind == "record"
