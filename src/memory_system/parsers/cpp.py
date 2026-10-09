"""C++ language source extraction."""

from __future__ import annotations

from .c_family import analyze as _analyze
from ..readers import CodeFile


def analyze(code_file: CodeFile):
    return _analyze(code_file, "cpp")
