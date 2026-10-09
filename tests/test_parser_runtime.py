"""Ensure every selected grammar wheel runs on the supported Windows Python."""

from __future__ import annotations

from importlib import import_module, metadata

from tree_sitter import Language, Parser


GRAMMAR_SAMPLES = (
    ("tree_sitter_typescript", "language_typescript", "function greet(name: string): string { return name; }"),
    ("tree_sitter_typescript", "language_tsx", "const App = () => <main>Hello</main>;"),
    ("tree_sitter_javascript", "language", "function greet(name) { return name; }"),
    ("tree_sitter_javascript", "language", "export default function App() { return <main />; }"),
    ("tree_sitter_java", "language", "class Sample { String greet(String name) { return name; } }"),
    ("tree_sitter_go", "language", "package sample\nfunc greet(name string) string { return name }"),
    ("tree_sitter_rust", "language", "fn greet(name: &str) -> &str { name }"),
    ("tree_sitter_c", "language", "int greet(int value) { return value; }"),
    ("tree_sitter_cpp", "language", "namespace sample { class Store { int get() const { return 1; } }; }"),
    ("tree_sitter_c_sharp", "language", "namespace Sample; public class Store { public int Get() => 1; }"),
)

EXPECTED_VERSIONS = {
    "tree-sitter": "0.26.0",
    "tree-sitter-c": "0.24.2",
    "tree-sitter-c-sharp": "0.23.5",
    "tree-sitter-cpp": "0.23.4",
    "tree-sitter-go": "0.25.0",
    "tree-sitter-java": "0.23.5",
    "tree-sitter-javascript": "0.25.0",
    "tree-sitter-rust": "0.24.2",
    "tree-sitter-typescript": "0.23.2",
}


def test_parser_runtime_matches_the_verified_lock() -> None:
    for package, expected in EXPECTED_VERSIONS.items():
        assert metadata.version(package) == expected


def test_each_selected_grammar_wheel_parses_a_language_sample() -> None:
    for module_name, language_factory, source in GRAMMAR_SAMPLES:
        module = import_module(module_name)
        language = Language(getattr(module, language_factory)())
        tree = Parser(language).parse(source.encode("utf-8"))
        assert not tree.root_node.has_error, (module_name, source, tree.root_node)
