"""Local code-file discovery and safe source reading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from .languages import SUPPORTED_EXTENSIONS


# Keep default discovery aligned with the canonical analyzer registry.
DEFAULT_EXTENSIONS = SUPPORTED_EXTENSIONS
DEFAULT_IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        ".concept-memory",
        ".next",
        ".nuxt",
        "coverage",
        "node_modules",
        "obj",
        "target",
        "venv",
    }
)


@dataclass(frozen=True, slots=True)
class CodeFile:
    """A decoded source file and its path relative to the analysis root."""

    path: Path
    relative_path: str
    text: str


def discover_code_files(
    path: str | Path,
    *,
    extensions: frozenset[str] = DEFAULT_EXTENSIONS,
    ignored_directories: frozenset[str] = DEFAULT_IGNORED_DIRECTORIES,
) -> list[Path]:
    """Return supported files in deterministic path order."""
    target = Path(path).expanduser()
    if not target.exists():
        raise FileNotFoundError(f"Analysis path does not exist: {target}")
    if target.is_file():
        if not _is_supported_source(target, extensions):
            raise ValueError(f"Unsupported source extension: {target.suffix or '<none>'}")
        return [target]

    files: list[Path] = []
    ignored = {part.casefold() for part in ignored_directories}
    for candidate in target.rglob("*"):
        if not candidate.is_file() or not _is_supported_source(candidate, extensions):
            continue
        if any(
            part.casefold() in ignored
            for part in candidate.relative_to(target).parts[:-1]
        ):
            continue
        files.append(candidate)
    return sorted(files, key=lambda item: item.as_posix())


def _is_supported_source(path: Path, extensions: frozenset[str]) -> bool:
    if path.name.casefold().endswith((".min.js", ".min.mjs")):
        return False
    normalized = {extension.casefold() for extension in extensions}
    return path.suffix in extensions or path.suffix.casefold() in normalized


def read_code_file(file_path: Path, *, root: Path) -> CodeFile:
    """Read UTF-8 source while preserving a path useful in generated cards."""
    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Source file is not valid UTF-8: {file_path}") from exc
    return CodeFile(
        path=file_path,
        relative_path=file_path.relative_to(root).as_posix(),
        text=text,
    )
