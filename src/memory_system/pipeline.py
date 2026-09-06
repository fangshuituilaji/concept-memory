"""Public Phase-one semantic concept pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .cache import CacheKey, ConceptCache
from .encoder import FileConceptEncoder
from .extractor import SourceFacts, TreeSitterSourceAnalyzer
from .models import ConceptCard
from .readers import DEFAULT_EXTENSIONS, discover_code_files, read_code_file
from .security import JsonlAuditRecorder, SecurityPolicy, SourceSendingPolicy
from .synthesis import (
    ConceptDraft,
    ConceptSynthesisConfig,
    ConceptSynthesizer,
    OfflineConceptSynthesizer,
    create_default_synthesizer,
)


@dataclass(frozen=True, slots=True)
class _PreparedSynthesizer:
    """A per-file draft provider used for cache hits and security fallbacks."""

    drafts: tuple[ConceptDraft, ...]
    config: ConceptSynthesisConfig
    generated_by: str

    def synthesize(self, facts: SourceFacts) -> list[ConceptDraft]:
        return list(self.drafts)


def analyze_path(
    path: str | Path,
    *,
    synthesizer: ConceptSynthesizer | None = None,
    config: ConceptSynthesisConfig | None = None,
    extensions: frozenset[str] = DEFAULT_EXTENSIONS,
    cache: ConceptCache | None = None,
    cache_path: str | Path | None = None,
    security_policy: SecurityPolicy | None = None,
    audit_recorder: JsonlAuditRecorder | None = None,
    source_sending_policy: SourceSendingPolicy | str | bool | None = None,
) -> list[ConceptCard]:
    """Analyze each source file into a small set of file-level concepts.

    Source is always read locally.  When an online synthesizer is selected,
    ``security_policy`` can deny sensitive files before the model receives them;
    those files use the deterministic offline synthesizer instead.  ``cache``
    and ``cache_path`` enable source/configuration-keyed draft reuse.
    """

    target = Path(path).expanduser().resolve()
    root = target if target.is_dir() else target.parent
    resolved_config = config or ConceptSynthesisConfig()
    resolved_synthesizer = synthesizer or create_default_synthesizer(resolved_config)
    analyzer = TreeSitterSourceAnalyzer()
    owns_cache = cache is None and cache_path is not None
    active_cache = (
        cache
        if cache is not None
        else (ConceptCache(cache_path) if cache_path is not None else None)
    )
    resolved_policy = security_policy or SecurityPolicy(
        root=root,
        source_sending_policy=(
            "offline"
            if isinstance(resolved_synthesizer, OfflineConceptSynthesizer)
            else "online"
        ),
    )
    discovered_files = discover_code_files(target, extensions=extensions)
    if active_cache is not None and target.is_dir():
        active_cache.prune_missing(
            {file_path.resolve() for file_path in discovered_files}, root=root
        )
    cards: list[ConceptCard] = []
    try:
        for file_path in discovered_files:
            code_file = read_code_file(file_path, root=root)
            facts = analyzer.analyze(code_file)
            file_synthesizer = resolved_synthesizer
            decision = resolved_policy.can_send(
                code_file.path,
                mode=source_sending_policy,
            )
            if audit_recorder is not None:
                audit_recorder.record(
                    files=[code_file.relative_path],
                    decision=decision,
                    model=resolved_config.model,
                    config=resolved_config.to_dict(),
                )
            if not decision.allowed and not isinstance(
                resolved_synthesizer, OfflineConceptSynthesizer
            ):
                file_synthesizer = OfflineConceptSynthesizer(resolved_config)
            effective_config = getattr(file_synthesizer, "config", resolved_config)
            generated_by = getattr(
                file_synthesizer,
                "generated_by",
                effective_config.model,
            )
            effective_model = (
                "offline-fallback"
                if generated_by == "offline-fallback"
                else str(generated_by or effective_config.model)
            )
            drafts: list[ConceptDraft] | None = None
            cache_key = CacheKey(
                source_digest=facts.source_digest,
                model_name=effective_model,
                model_version=effective_config.model_version,
                prompt_version=effective_config.prompt_version,
                generation_config=effective_config.to_dict(),
                file_path=code_file.path,
            )
            if active_cache is not None:
                cached = active_cache.get(cache_key)
                if cached is not None:
                    drafts = _drafts_from_json(cached)

            if drafts is None:
                drafts = file_synthesizer.synthesize(facts)
                if active_cache is not None:
                    active_cache.put(
                        cache_key,
                        [_draft_to_json(draft) for draft in drafts],
                        file_path=code_file.path,
                    )

            prepared = _PreparedSynthesizer(
                drafts=tuple(drafts),
                config=effective_config,
                generated_by=effective_model,
            )
            cards.extend(FileConceptEncoder(prepared, resolved_config).encode(facts))
    finally:
        if owns_cache:
            # ConceptCache is JSON-backed and does not hold a resource, but this
            # branch documents ownership and keeps future backends replaceable.
            pass
    return cards


def _draft_to_json(draft: ConceptDraft) -> dict[str, Any]:
    return {
        "name": draft.name,
        "definition": draft.definition,
        "background": draft.background,
        "background_concepts": list(draft.background_concepts),
        "evidence": list(draft.evidence),
    }


def _drafts_from_json(value: Any) -> list[ConceptDraft]:
    if not isinstance(value, list):
        raise ValueError("cached concepts must be a list")
    drafts: list[ConceptDraft] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("cached concept must be an object")
        drafts.append(
            ConceptDraft(
                name=str(item.get("name", "")),
                definition=str(item.get("definition", "")),
                background=str(item.get("background", "")),
                background_concepts=tuple(
                    str(entry) for entry in item.get("background_concepts", [])
                ),
                evidence=tuple(str(entry) for entry in item.get("evidence", [])),
            )
        )
    return drafts


def cards_to_json(cards: Iterable[ConceptCard], *, indent: int = 2) -> str:
    """Serialize cards as a deterministic JSON array."""
    payload = [card.to_dict() for card in cards]
    return json.dumps(payload, ensure_ascii=False, indent=indent, sort_keys=True) + "\n"


def write_cards_json(cards: Iterable[ConceptCard], output_path: str | Path) -> None:
    """Write concept cards to a UTF-8 JSON file."""
    Path(output_path).write_text(cards_to_json(cards), encoding="utf-8")
