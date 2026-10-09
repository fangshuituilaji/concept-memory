"""Shared safety guards for Tree-sitter parser lifetimes."""

from __future__ import annotations

import gc
from contextlib import contextmanager
from threading import RLock

_TREE_SITTER_GC_LOCK = RLock()
_TREE_SITTER_GC_DEPTH = 0
_TREE_SITTER_GC_THRESHOLDS: tuple[int, int, int] | None = None
_TREE_SITTER_GC_COLLECT_ON_EXIT = False


@contextmanager
def defer_gc_for_tree_sitter(*, collect_on_exit: bool = False):
    """Defer cyclic GC until live Tree-sitter node wrappers have been released.

    Nested and concurrent parser calls share one process-wide deferral window;
    only the outermost caller restores the GC threshold.
    """

    global _TREE_SITTER_GC_DEPTH
    global _TREE_SITTER_GC_THRESHOLDS
    global _TREE_SITTER_GC_COLLECT_ON_EXIT

    with _TREE_SITTER_GC_LOCK:
        if _TREE_SITTER_GC_DEPTH == 0:
            _TREE_SITTER_GC_THRESHOLDS = gc.get_threshold() if gc.isenabled() else None
            _TREE_SITTER_GC_COLLECT_ON_EXIT = False
            if _TREE_SITTER_GC_THRESHOLDS is not None:
                gc.set_threshold(
                    max(_TREE_SITTER_GC_THRESHOLDS[0], 1_000_000_000),
                    *_TREE_SITTER_GC_THRESHOLDS[1:],
                )
        _TREE_SITTER_GC_DEPTH += 1
        _TREE_SITTER_GC_COLLECT_ON_EXIT |= collect_on_exit
    try:
        yield
    finally:
        with _TREE_SITTER_GC_LOCK:
            _TREE_SITTER_GC_DEPTH -= 1
            if _TREE_SITTER_GC_DEPTH == 0:
                thresholds = _TREE_SITTER_GC_THRESHOLDS
                collect = _TREE_SITTER_GC_COLLECT_ON_EXIT
                try:
                    if thresholds is not None:
                        if collect:
                            gc.collect()
                        gc.set_threshold(*thresholds)
                finally:
                    _TREE_SITTER_GC_THRESHOLDS = None
                    _TREE_SITTER_GC_COLLECT_ON_EXIT = False
