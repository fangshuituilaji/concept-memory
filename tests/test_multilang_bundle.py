from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from deploy import build_offline_bundle


EXPECTED_PARSER_PACKAGES = {
    "tree-sitter",
    "tree-sitter-c",
    "tree-sitter-c-sharp",
    "tree-sitter-cpp",
    "tree-sitter-go",
    "tree-sitter-java",
    "tree-sitter-javascript",
    "tree-sitter-rust",
    "tree-sitter-typescript",
}


def test_offline_builder_reads_every_hashed_parser_requirement():
    locked = build_offline_bundle.parser_requirement_names()
    assert set(locked) == EXPECTED_PARSER_PACKAGES
    assert EXPECTED_PARSER_PACKAGES <= set(build_offline_bundle.IMPORT_NAMES)


def test_packaged_runtime_selfcheck_parses_every_supported_language():
    project_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_root / "src")
    checked = subprocess.run(
        [sys.executable, "-c", build_offline_bundle.PARSER_SELFCHECK_CODE],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "Python" in checked.stdout
    assert "TSX" in checked.stdout
    assert "C++" in checked.stdout
    assert "C#" in checked.stdout
