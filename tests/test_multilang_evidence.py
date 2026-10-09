from __future__ import annotations

from memory_system.encoder import _resolve_evidence
from memory_system.extractor import SymbolFact


def _symbol(name: str, qualified_name: str, line: int) -> SymbolFact:
    return SymbolFact(name, "method", qualified_name, line, line + 1, "signature", None)


def test_exact_overload_evidence_keeps_all_source_locations():
    symbols = (
        _symbol("get", "Store.get", 10),
        _symbol("get", "Store.get", 20),
    )
    resolved, missing = _resolve_evidence(("Store.get",), symbols)
    assert [item.start_line for item in resolved] == [10, 20]
    assert missing == ()


def test_short_name_resolves_all_matches_only_inside_one_qualified_group():
    overloads = (
        _symbol("get", "Store.get", 10),
        _symbol("get", "Store.get", 20),
    )
    resolved, missing = _resolve_evidence(("get",), overloads)
    assert [item.start_line for item in resolved] == [10, 20]
    assert missing == ()

    ambiguous = (*overloads, _symbol("get", "Cache.get", 30))
    resolved, missing = _resolve_evidence(("get",), ambiguous)
    assert resolved == []
    assert missing == ("get",)
