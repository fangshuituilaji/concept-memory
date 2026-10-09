from __future__ import annotations

from pathlib import Path

from memory_system.languages import SUPPORTED_EXTENSIONS
from memory_system.readers import DEFAULT_EXTENSIONS, discover_code_files


def test_default_discovery_covers_the_full_registered_extension_matrix(tmp_path):
    names = (
        "one.py", "notes.md", "app.ts", "native.mts", "common.cts", "view.tsx",
        "run.js", "module.mjs", "legacy.cjs", "view.jsx", "Main.java", "main.go",
        "lib.rs", "native.c", "header.h", "app.cpp", "legacy.cc", "native.cxx",
        "header.hpp", "legacy.hh", "native.hxx", "upper.C", "upper.H", "Main.cs",
        "upper.TS", "UPPER.MD",
    )
    for name in names:
        (tmp_path / name).write_text("source\n", encoding="utf-8")

    files = discover_code_files(tmp_path)
    assert DEFAULT_EXTENSIONS == SUPPORTED_EXTENSIONS
    assert {path.name for path in files} == set(names)
    assert [path.as_posix() for path in files] == sorted(path.as_posix() for path in files)


def test_discovery_excludes_generated_directories_and_minified_javascript(tmp_path):
    ignored = (".concept-memory", ".next", ".nuxt", "coverage", "target", "obj", "node_modules")
    for directory in ignored:
        path = tmp_path / directory
        path.mkdir()
        (path / "generated.go").write_text("package generated\n", encoding="utf-8")
    (tmp_path / "app.min.js").write_text("minified\n", encoding="utf-8")
    (tmp_path / "app.min.mjs").write_text("minified\n", encoding="utf-8")
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "kept.go").write_text("package vendor\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "sample.cs").write_text("class Test {}\n", encoding="utf-8")

    files = discover_code_files(tmp_path)
    relative = {path.relative_to(tmp_path).as_posix() for path in files}
    assert relative == {"vendor/kept.go", "tests/sample.cs"}


def test_explicit_extension_subset_and_single_file_input_are_preserved(tmp_path):
    python = tmp_path / "sample.py"
    typescript = tmp_path / "sample.ts"
    python.write_text("pass\n", encoding="utf-8")
    typescript.write_text("function run() {}\n", encoding="utf-8")

    assert discover_code_files(tmp_path, extensions=frozenset({".py"})) == [python]
    assert discover_code_files(typescript) == [typescript]
