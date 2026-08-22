"""Public phase-one semantic concept pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .encoder import FileConceptEncoder
from .extractor import PythonSourceAnalyzer
from .models import ConceptCard
from .readers import DEFAULT_EXTENSIONS, discover_code_files, read_code_file
from .synthesis import (
    ConceptSynthesisConfig,
    ConceptSynthesizer,
    create_default_synthesizer,
)


def analyze_path(
    path: str | Path,
    *,
    synthesizer: ConceptSynthesizer | None = None,
    config: ConceptSynthesisConfig | None = None,
    extensions: frozenset[str] = DEFAULT_EXTENSIONS,
) -> list[ConceptCard]:
    """Analyze each source file into a small set of file-level concepts."""
    target = Path(path).expanduser().resolve()
    root = target if target.is_dir() else target.parent
    resolved_config = config or ConceptSynthesisConfig()
    resolved_synthesizer = synthesizer or create_default_synthesizer(resolved_config)
    analyzer = PythonSourceAnalyzer()
    encoder = FileConceptEncoder(resolved_synthesizer, resolved_config)
    cards: list[ConceptCard] = []
    for file_path in discover_code_files(target, extensions=extensions):
        code_file = read_code_file(file_path, root=root)
        cards.extend(encoder.encode(analyzer.analyze(code_file)))
    return cards


def cards_to_json(cards: Iterable[ConceptCard], *, indent: int = 2) -> str:
    """Serialize cards as a deterministic JSON array."""
    payload = [card.to_dict() for card in cards]
    return json.dumps(payload, ensure_ascii=False, indent=indent, sort_keys=True) + "\n"


def write_cards_json(cards: Iterable[ConceptCard], output_path: str | Path) -> None:
    """Write concept cards to a UTF-8 JSON file."""
    Path(output_path).write_text(cards_to_json(cards), encoding="utf-8")
