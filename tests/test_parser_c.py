from __future__ import annotations

from pathlib import Path

from memory_system.extractor import TreeSitterSourceAnalyzer
from memory_system.readers import CodeFile


def _facts(filename: str, source: str):
    path = Path(filename)
    return TreeSitterSourceAnalyzer().analyze(
        CodeFile(path=path, relative_path=filename, text=source)
    )


def test_c_extracts_functions_types_and_typedefs_but_not_function_pointers():
    source = (
        "typedef struct Node { struct Node *next; } Node;\n"
        "typedef enum State { READY, BUSY } State;\n"
        "union Value { int integer; float decimal; };\n"
        "int read_value(int key);\n"
        "int read_value(int key) { return key; }\n"
        "int (*callback)(int);\n"
    )
    facts = _facts("value.c", source)
    symbols = {symbol.qualified_name: symbol for symbol in facts.symbols}
    assert symbols["Node"].kind in {"struct", "type"}
    assert symbols["State"].kind in {"enum", "type"}
    assert symbols["Value"].kind == "union"
    read_functions = [s for s in facts.symbols if s.qualified_name == "read_value"]
    assert len(read_functions) == 2
    assert [s.start_line for s in read_functions] == [4, 5]
    assert not any("callback" in s.qualified_name for s in facts.symbols)


def test_h_header_prefers_cpp_constructs_even_when_c_grammar_recovers_them():
    facts = _facts("legacy.h", "namespace sample { class Store {}; }\n")
    assert any(s.qualified_name == "sample::Store" for s in facts.symbols)
