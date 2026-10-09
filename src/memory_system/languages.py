"""Canonical source-language and file-extension registry."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from functools import lru_cache
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import sys


@dataclass(frozen=True, slots=True)
class LanguageSpec:
    language_id: str
    extensions: tuple[str, ...]
    fence_tag: str
    grammar_module: str | None = None
    grammar_factory: str | None = None
    grammar_candidates: tuple[str, ...] = ()
    parser_version: str = "1"


_SPECS = (
    LanguageSpec("python", (".py",), "python"),
    LanguageSpec("markdown", (".md",), "markdown"),
    LanguageSpec("typescript", (".ts", ".mts", ".cts"), "typescript", "tree_sitter_typescript", "language_typescript"),
    LanguageSpec("tsx", (".tsx",), "tsx", "tree_sitter_typescript", "language_tsx"),
    LanguageSpec("javascript", (".js", ".mjs", ".cjs", ".jsx"), "javascript", "tree_sitter_javascript", "language"),
    LanguageSpec("java", (".java",), "java", "tree_sitter_java", "language"),
    LanguageSpec("go", (".go",), "go", "tree_sitter_go", "language"),
    LanguageSpec("rust", (".rs",), "rust", "tree_sitter_rust", "language"),
    LanguageSpec("c", (".c",), "c", "tree_sitter_c", "language"),
    LanguageSpec("c-header", (".h",), "c", grammar_candidates=("c", "cpp")),
    LanguageSpec("cpp", (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".C", ".H"), "cpp", "tree_sitter_cpp", "language"),
    LanguageSpec("csharp", (".cs",), "csharp", "tree_sitter_c_sharp", "language"),
)

LANGUAGES = {spec.language_id: spec for spec in _SPECS}
SUPPORTED_EXTENSIONS = frozenset(
    extension for spec in _SPECS for extension in spec.extensions
)
_EXTENSION_TO_SPEC = {
    extension.lower(): spec
    for spec in _SPECS
    for extension in spec.extensions
    if extension not in {".C", ".H"}
}


def resolve_language(path: str | Path) -> LanguageSpec | None:
    """Return the registered language for a source path, if it is supported."""

    suffix = Path(path).suffix
    if suffix in {".C", ".H"}:
        return LANGUAGES["cpp"]
    return _EXTENSION_TO_SPEC.get(suffix.lower())


def parser_signature_for_path(path: str | Path) -> str:
    """Fingerprint the selected parser and all grammars used for this path."""

    spec = resolve_language(path)
    if spec is None:
        raise ValueError(f"Unsupported source extension: {Path(path).suffix or '<none>'}")
    return _parser_signature(spec.language_id)


@lru_cache(maxsize=None)
def _parser_signature(language_id: str) -> str:
    spec = LANGUAGES[language_id]
    grammar_ids = spec.grammar_candidates or (spec.language_id,)
    package_versions: dict[str, str] = {}
    if any(LANGUAGES[item].grammar_module for item in grammar_ids):
        try:
            package_versions["tree-sitter"] = version("tree-sitter")
            for language_id in grammar_ids:
                grammar_module = LANGUAGES[language_id].grammar_module
                if grammar_module:
                    package = grammar_module.replace("_", "-")
                    package_versions[package] = version(package)
        except PackageNotFoundError as exc:
            raise ParserDependencyError(spec.language_id, exc) from exc
    payload = {
        "language_id": spec.language_id,
        "parser_version": spec.parser_version,
        "grammar_ids": grammar_ids,
        "package_versions": package_versions,
        "python_version": sys.version_info[:2] if spec.language_id == "python" else None,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


@lru_cache(maxsize=None)
def load_language(language_id: str):
    """Load one Tree-sitter grammar on demand with a useful install error."""

    spec = LANGUAGES.get(language_id)
    if spec is None:
        raise ValueError(f"Unsupported language id: {language_id}")
    if not spec.grammar_module or not spec.grammar_factory:
        raise ValueError(f"Language {language_id!r} does not use a Tree-sitter grammar")
    try:
        module = import_module(spec.grammar_module)
        from tree_sitter import Language

        return Language(getattr(module, spec.grammar_factory)())
    except (ImportError, AttributeError, OSError, TypeError, ValueError) as exc:
        raise ParserDependencyError(language_id, exc) from exc


class ParserDependencyError(RuntimeError):
    def __init__(self, language_id: str, cause: BaseException):
        super().__init__(
            f"Parser dependencies for {language_id!r} are unavailable or incompatible; "
            "reinstall Concept Memory with its parser dependencies."
        )
        self.language_id = language_id
        self.__cause__ = cause
