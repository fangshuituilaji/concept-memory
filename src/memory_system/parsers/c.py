"""C language source extraction."""

from __future__ import annotations

from .c_family import analyze as _analyze
from .c_family import analyze_header
from ..readers import CodeFile


def analyze(code_file: CodeFile):
    return _analyze(code_file, "c")


__all__ = ["analyze", "analyze_header"]
