from __future__ import annotations

from pathlib import Path

from memory_system.extractor import TreeSitterSourceAnalyzer
from memory_system.readers import CodeFile


def test_rust_extracts_modules_types_traits_and_disambiguated_impl_methods():
    source = (
        "pub mod model {\n"
        "    pub struct Store { value: String }\n"
        "    pub trait Read { fn get(&self, key: &str) -> Option<&String>; }\n"
        "    impl Store {\n"
        "        pub fn new(value: String) -> Self { Self { value } }\n"
        "        pub fn get(&self, key: &str) -> Option<&String> { None }\n"
        "    }\n"
        "    impl Read for Store {\n"
        "        fn get(&self, key: &str) -> Option<&String> { None }\n"
        "    }\n"
        "    pub enum State { Ready, Busy }\n"
        "    pub type StoreId = u64;\n"
        "    macro_rules! make_fake { () => { fn fake() {} } }\n"
        "    make_fake!();\n"
        "    fn helper() {}\n"
        "}\n"
    )
    path = Path("src/model.rs")
    facts = TreeSitterSourceAnalyzer().analyze(
        CodeFile(path=path, relative_path=path.as_posix(), text=source)
    )
    symbols = {symbol.qualified_name: symbol for symbol in facts.symbols}
    assert symbols["model::Store"].kind == "struct"
    assert symbols["model::Read"].kind == "trait"
    assert symbols["model::State"].kind == "enum"
    assert symbols["model::StoreId"].kind == "type"
    assert symbols["model::Read::get"].kind == "method"
    assert symbols["model::Store::new"].kind == "method"
    assert symbols["model::Store::get"].kind == "method"
    trait_impl = [s for s in facts.symbols if s.qualified_name == "model::Store as Read::get"]
    assert len(trait_impl) == 1
    assert trait_impl[0].kind == "method"
    assert trait_impl[0].start_line == 9
    assert symbols["model::helper"].kind == "function"
    assert not any(symbol.name == "fake" for symbol in facts.symbols)


def test_rust_large_tree_walk_survives_windows_gc_pressure():
    import os
    import subprocess
    import sys

    script = r'''
import gc
from pathlib import Path
from memory_system.extractor import TreeSitterSourceAnalyzer
from memory_system.readers import CodeFile
from memory_system.parsers import rust

source = "\n".join(
    f"pub fn operation_{index}(input: usize) -> usize {{ "
    f"let mut total = input + {index}; "
    "for step in 0..4 { if total > step { total += step; } else { total += 1; } } "
    "total }"
    for index in range(160)
)
path = Path("src/stress.rs")
events = []
walking = [False]
original_walk = rust._walk
def track_walk(*args, **kwargs):
    walking[0] = True
    try:
        return original_walk(*args, **kwargs)
    finally:
        walking[0] = False
rust._walk = track_walk
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
assert not events, f"GC ran while Rust syntax nodes were live: {events}"
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


def test_symbol_line_numbers_use_utf8_byte_offsets():
    from memory_system.parsers.base import make_symbol_fact

    source = "// 中文注释🙂\nfn example() {}\n".encode("utf-8")

    class Anchor:
        start_byte = source.index(b"fn")
        end_byte = source.index(b"\n", start_byte)

        def child_by_field_name(self, name):
            return None

        @property
        def start_point(self):
            raise AssertionError("Tree-sitter Point access is unsafe in the packaged runtime")

        @property
        def end_point(self):
            raise AssertionError("Tree-sitter Point access is unsafe in the packaged runtime")

    fact = make_symbol_fact(
        Anchor(),
        source,
        "function",
        "example",
        name="example",
    )
    assert fact.start_line == 2
    assert fact.end_line == 2
