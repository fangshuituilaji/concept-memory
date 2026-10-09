from __future__ import annotations

from pathlib import Path

from memory_system.extractor import TreeSitterSourceAnalyzer
from memory_system.readers import CodeFile


def test_go_extracts_package_types_receiver_methods_and_generic_functions():
    source = (
        "package sample\n"
        "\n"
        "type Reader interface { Read(key string) string }\n"
        "type Store struct { value string }\n"
        "type ID string\n"
        "func NewStore() *Store { return &Store{} }\n"
        "func (s *Store) Get(key string) string { return s.value }\n"
        "func (s Store) Reset() {}\n"
        "func Map[T any](values []T) []T { return values }\n"
    )
    path = Path("pkg/store.go")
    facts = TreeSitterSourceAnalyzer().analyze(
        CodeFile(path=path, relative_path=path.as_posix(), text=source)
    )
    symbols = {symbol.qualified_name: symbol for symbol in facts.symbols}
    assert symbols["sample.Reader"].kind == "interface"
    assert symbols["sample.Reader.Read"].kind == "method"
    assert symbols["sample.Store"].kind == "struct"
    assert symbols["sample.ID"].kind == "type"
    assert symbols["sample.NewStore"].kind == "function"
    assert symbols["sample.Store.Get"].kind == "method"
    assert symbols["sample.Store.Reset"].kind == "method"
    assert symbols["sample.Map"].kind == "function"
    assert symbols["sample.Store.Get"].start_line == 7
