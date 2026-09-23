"""Local code-file discovery and safe source reading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


# Phase 1 acceptance is Python-first. TS/JS can be enabled explicitly with
# ``extensions=`` after installing the optional tree-sitter extra. Markdown
# documents join by default because agents keep project knowledge in them.
DEFAULT_EXTENSIONS = frozenset({".py", ".md"})
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
        "node_modules",
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
        if target.suffix not in extensions:
            raise ValueError(f"Unsupported source extension: {target.suffix or '<none>'}")
        return [target]

    files: list[Path] = []
    for candidate in target.rglob("*"):
        if not candidate.is_file() or candidate.suffix not in extensions:
            continue
        if any(part in ignored_directories for part in candidate.relative_to(target).parts[:-1]):
            continue
        files.append(candidate)
    return sorted(files, key=lambda item: item.as_posix())


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
