from __future__ import annotations

from pathlib import Path

import pytest

from memory_system.extractor import TreeSitterSourceAnalyzer
from memory_system.parsers.base import SourceParseError
from memory_system.readers import CodeFile


def _facts(filename: str, source: str):
    path = Path(filename)
    return TreeSitterSourceAnalyzer().analyze(
        CodeFile(path=path, relative_path=filename, text=source)
    )


def test_cpp_extracts_namespaces_classes_constructors_overloads_and_templates():
    source = (
        "namespace sample {\n"
        "class Store { public: Store(); ~Store(); int get(); int get(int key); };\n"
        "Store::Store() {}\n"
        "Store::~Store() {}\n"
        "int Store::get() { return 0; }\n"
        "int Store::get(int key) { return key; }\n"
        "template<typename T> T identity(T value) { return value; }\n"
        "using StoreId = long;\n"
        "}\n"
    )
    facts = _facts("store.cpp", source)
    symbols = {symbol.qualified_name: symbol for symbol in facts.symbols}
    assert symbols["sample::Store"].kind == "class"
    assert symbols["sample::identity"].kind == "function"
    assert symbols["sample::StoreId"].kind == "type"
    constructors = [s for s in facts.symbols if s.qualified_name == "sample::Store::Store"]
    destructors = [s for s in facts.symbols if s.qualified_name == "sample::Store::~Store"]
    overloads = [s for s in facts.symbols if s.qualified_name == "sample::Store::get"]
    assert len(constructors) == 2
    assert len(destructors) == 2
    assert len(overloads) == 4
    assert [s.start_line for s in overloads] == [2, 2, 5, 6]


def test_c_header_with_errors_in_both_grammars_fails_with_path_and_line():
    with pytest.raises(SourceParseError, match=r"Broken\.h.*line 1"):
        _facts("Broken.h", "namespace Broken { class ;\n")
