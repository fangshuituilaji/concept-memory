from __future__ import annotations

from pathlib import Path

from memory_system.extractor import TreeSitterSourceAnalyzer
from memory_system.readers import CodeFile


def _facts(filename: str, source: str):
    path = Path(filename)
    return TreeSitterSourceAnalyzer().analyze(
        CodeFile(path=path, relative_path=filename, text=source)
    )


def test_typescript_extracts_types_namespaces_methods_and_overloads():
    source = (
        "// function fake() {}\n"
        "interface Store {\n"
        "  get(key: string): Promise<string>;\n"
        "}\n"
        "type Key = string | number;\n"
        "enum State { Ready, Busy }\n"
        "namespace Outer {\n"
        "  export const find = (id: Key) => id;\n"
        "}\n"
        "export class Api<T> {\n"
        "  get(key: Key): T;\n"
        "  get(key: string): T;\n"
        "  get(key: any): T { return key as T; }\n"
        "}\n"
        "export function build(): Api<string> { return new Api('x'); }\n"
    )
    facts = _facts("api.ts", source)
    symbols = {symbol.qualified_name: symbol for symbol in facts.symbols}
    assert "Store" in symbols
    assert symbols["Store"].kind == "interface"
    assert symbols["Store"].start_line == 2 and symbols["Store"].end_line == 4
    assert "Store.get" in symbols
    assert symbols["Store.get"].kind == "method"
    assert "Key" in symbols and symbols["Key"].kind == "type"
    assert "State" in symbols and symbols["State"].kind == "enum"
    assert "Outer" in symbols and symbols["Outer"].kind == "namespace"
    assert symbols["Outer.find"].start_line == 8
    assert "Api" in symbols and symbols["Api"].kind == "class"
    overloads = [symbol for symbol in facts.symbols if symbol.qualified_name == "Api.get"]
    assert len(overloads) == 3
    assert [symbol.start_line for symbol in overloads] == [11, 12, 13]
    assert symbols["build"].kind == "function"
    assert not any("fake" in symbol.qualified_name for symbol in facts.symbols)


def test_tsx_uses_jsx_grammar_and_indexes_both_component_forms():
    source = (
        "type Props = { label: string };\n"
        "export const App = ({ label }: Props) => <main>{label}</main>;\n"
        "export default function Panel() { return <section />; }\n"
    )
    facts = _facts("App.tsx", source)
    symbols = {symbol.qualified_name: symbol for symbol in facts.symbols}
    assert "Props" in symbols and symbols["Props"].kind == "type"
    assert symbols["App"].kind == "function"
    assert (symbols["App"].start_line, symbols["App"].end_line) == (2, 2)
    assert symbols["Panel"].kind == "function"
    assert (symbols["Panel"].start_line, symbols["Panel"].end_line) == (3, 3)


def test_typescript_large_tree_walk_defers_gc_while_nodes_are_live():
    import os
    import subprocess
    import sys

    script = r'''
import gc
from pathlib import Path
from memory_system.extractor import TreeSitterSourceAnalyzer
from memory_system.readers import CodeFile
from memory_system.parsers import typescript

source = "\n".join(
    f"export function operation_{index}(value: number): number {{ "
    f"const nested = value + {index}; return nested; }}"
    for index in range(160)
)
path = Path("src/stress.ts")
events = []
walking = [False]
original_walk = typescript._walk
def track_walk(*args, **kwargs):
    walking[0] = True
    try:
        return original_walk(*args, **kwargs)
    finally:
        walking[0] = False
typescript._walk = track_walk
def track_gc(phase, info):
    if walking[0]:
        events.append(phase)
old_threshold = gc.get_threshold()
gc.set_threshold(16, 8, 8)
parser_threshold = gc.get_threshold()
gc.callbacks.append(track_gc)
try:
    facts = TreeSitterSourceAnalyzer().analyze(
        CodeFile(path=path, relative_path=path.as_posix(), text=source)
    )
    threshold_after = gc.get_threshold()
finally:
    gc.callbacks.remove(track_gc)
    gc.set_threshold(*old_threshold)
assert not events, f"GC ran while TypeScript syntax nodes were live: {events}"
assert threshold_after == parser_threshold
assert gc.isenabled()
assert sum(symbol.kind == "function" for symbol in facts.symbols) == 160
'''
    root = Path(__file__).resolve().parents[1]
    source_root = root / "src"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(source_root), env.get("PYTHONPATH", "")) if item
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
