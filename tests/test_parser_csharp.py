from __future__ import annotations

from pathlib import Path

from memory_system.extractor import TreeSitterSourceAnalyzer
from memory_system.readers import CodeFile


def test_csharp_extracts_namespaces_types_constructors_methods_and_properties():
    source = (
        "namespace Demo.Core;\n"
        "public interface IStore { string Get(int id); }\n"
        "public record Item(string Name);\n"
        "public class Store<T> : IStore {\n"
        "    public Store(T value) { }\n"
        "    public string Get(int id) => id.ToString();\n"
        "    public string Get(string id) => id;\n"
        "    public string Name => \"store\";\n"
        "    public void Reset() { }\n"
        "}\n"
    )
    path = Path("src/Store.cs")
    facts = TreeSitterSourceAnalyzer().analyze(
        CodeFile(path=path, relative_path=path.as_posix(), text=source)
    )
    symbols = {symbol.qualified_name: symbol for symbol in facts.symbols}
    assert symbols["Demo.Core.IStore"].kind == "interface"
    assert symbols["Demo.Core.IStore.Get"].kind == "method"
    assert symbols["Demo.Core.Item"].kind == "record"
    assert symbols["Demo.Core.Store"].kind == "class"
    assert symbols["Demo.Core.Store.Store"].kind == "constructor"
    overloads = [s for s in facts.symbols if s.qualified_name == "Demo.Core.Store.Get"]
    assert len(overloads) == 2
    assert [symbol.start_line for symbol in overloads] == [6, 7]
    assert symbols["Demo.Core.Store.Name"].kind == "property"
    assert symbols["Demo.Core.Store.Reset"].kind == "method"
