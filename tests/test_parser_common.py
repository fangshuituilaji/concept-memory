from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest


def test_common_parser_creates_independent_parsers_and_uses_utf8_bytes():
    parser_module = importlib.import_module("memory_system.parsers.base")
    source = "// 中文注释\nfunction greet(name: string) { return name; }\n"
    first_tree = parser_module.parse_source("typescript", source, "src/你好.ts")
    second_tree = parser_module.parse_source("typescript", source, "src/你好.ts")
    assert not first_tree.root_node.has_error
    assert first_tree.root_node.end_point == second_tree.root_node.end_point
    assert first_tree is not second_tree


def test_common_parser_reports_relative_path_language_and_one_based_line():
    parser_module = importlib.import_module("memory_system.parsers.base")
    source = "class Good {}\nclass Broken { void call( }\n"
    with pytest.raises(parser_module.SourceParseError) as error:
        parser_module.parse_source("java", source, "src/Broken.java")
    message = str(error.value)
    assert "src/Broken.java" in message
    assert "java" in message
    assert "line 2" in message


def test_missing_grammar_reports_install_guidance():
    parser_module = importlib.import_module("memory_system.parsers.base")
    languages = importlib.import_module("memory_system.languages")
    languages.load_language.cache_clear()
    try:
        with patch.object(languages, "import_module", side_effect=ModuleNotFoundError):
            with pytest.raises(parser_module.ParserDependencyError, match="parser dependencies"):
                parser_module.load_language("java")
    finally:
        languages.load_language.cache_clear()


def test_existing_analyzer_routes_tree_sitter_languages_through_registry():
    languages = importlib.import_module("memory_system.languages")
    from memory_system.extractor import TreeSitterSourceAnalyzer
    from memory_system.readers import CodeFile

    code_file = CodeFile(
        path=Path("sample.ts"), relative_path="sample.ts",
        text="function greet(name: string) { return name; }\n",
    )
    with patch("memory_system.parsers.base.load_language", wraps=languages.load_language) as load:
        facts = TreeSitterSourceAnalyzer().analyze(code_file)
    load.assert_called_once_with("typescript")
    assert facts.symbols[0].qualified_name == "greet"
