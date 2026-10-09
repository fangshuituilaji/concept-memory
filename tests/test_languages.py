from __future__ import annotations

import importlib
from pathlib import Path


def test_supported_extensions_resolve_to_the_expected_language():
    languages = importlib.import_module("memory_system.languages")
    expected = {
        ".py": "python", ".md": "markdown",
        ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
        ".tsx": "tsx", ".js": "javascript", ".mjs": "javascript",
        ".cjs": "javascript", ".jsx": "javascript",
        ".java": "java", ".go": "go", ".rs": "rust",
        ".c": "c", ".h": "c-header",
        ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
        ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
        ".cs": "csharp",
    }
    for suffix, language_id in expected.items():
        assert languages.resolve_language(Path(f"sample{suffix}")).language_id == language_id


def test_language_resolution_handles_case_and_declaration_suffixes():
    languages = importlib.import_module("memory_system.languages")
    assert languages.resolve_language(Path("component.TS")).language_id == "typescript"
    assert languages.resolve_language(Path("component.TSX")).language_id == "tsx"
    assert languages.resolve_language(Path("component.C")).language_id == "cpp"
    assert languages.resolve_language(Path("component.H")).language_id == "cpp"
    assert languages.resolve_language(Path("types.d.ts")).language_id == "typescript"
    assert languages.resolve_language(Path("unknown.xyz")) is None


def test_c_header_records_both_candidate_grammars():
    languages = importlib.import_module("memory_system.languages")
    spec = languages.resolve_language(Path("legacy.h"))
    assert spec.grammar_candidates == ("c", "cpp")
