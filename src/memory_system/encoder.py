"""Turn model-generated file concepts into anchored concept cards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .extractor import SourceFacts, SymbolFact
from .models import ConceptCard, ConceptKind, SourceLocation
from .synthesis import ConceptDraft, ConceptSynthesisConfig, ConceptSynthesizer


@dataclass(frozen=True, slots=True)
class FileConceptEncoder:
    """Anchor a small semantic concept set to verified source facts."""

    synthesizer: ConceptSynthesizer
    config: ConceptSynthesisConfig

    def encode(self, facts: SourceFacts) -> list[ConceptCard]:
        drafts = self.synthesizer.synthesize(facts)
        if len(drafts) > self.config.max_concepts:
            drafts = drafts[: self.config.max_concepts]
        cards: list[ConceptCard] = []
        for index, draft in enumerate(drafts, start=1):
            cards.append(self._card_from_draft(facts, draft, index, len(drafts)))
        return cards

    def _card_from_draft(
        self,
        facts: SourceFacts,
        draft: ConceptDraft,
        index: int,
        count: int,
    ) -> ConceptCard:
        evidence, unanchored = _resolve_evidence(draft.evidence, facts.symbols)
        if evidence:
            start_line = min(symbol.start_line for symbol in evidence)
            end_line = max(symbol.end_line for symbol in evidence)
            qualified_name = "file:" + facts.module + "#" + ",".join(
                symbol.qualified_name for symbol in evidence[:5]
            )
        else:
            start_line, end_line = 1, max(1, len(facts.file.text.splitlines()))
            qualified_name = f"file:{facts.module}"

        if not draft.evidence:
            validation_status = "unanchored"
        elif unanchored and evidence:
            validation_status = "partially_anchored"
        elif unanchored:
            validation_status = "unanchored"
        else:
            validation_status = "validated"

        location = SourceLocation(
            file_path=facts.file.relative_path,
            module=facts.module,
            qualified_name=qualified_name,
            start_line=start_line,
            end_line=end_line,
        )
        evidence_locations = [
            {
                "file": facts.file.relative_path,
                "qualified_name": symbol.qualified_name,
                "kind": symbol.kind,
                "start_line": symbol.start_line,
                "end_line": symbol.end_line,
            }
            for symbol in evidence
        ]
        metadata: dict[str, Any] = {
            "concept_index": index,
            "file_concept_count": count,
            "evidence_symbols": [symbol.qualified_name for symbol in evidence],
            "evidence_locations": evidence_locations,
            "unanchored_evidence": list(unanchored),
            "validation_status": validation_status,
            "source_language": facts.file.path.suffix.lstrip(".") or "unknown",
            "source_facts": "local-parser",
        }
        metadata.update(_synthesis_metadata(self.synthesizer, self.config))
        return ConceptCard.create(
            name=draft.name,
            kind=ConceptKind.CONCEPT,
            definition=draft.definition,
            background=draft.background,
            background_concepts=draft.background_concepts,
            location=location,
            source_excerpt=_evidence_excerpt(facts, evidence),
            source_digest=facts.source_digest,
            metadata=metadata,
            generated_by=getattr(
                self.synthesizer,
                "generated_by",
                "qwen-flash"
                if self.synthesizer.__class__.__name__.startswith("DashScope")
                else "offline-fallback",
            ),
        )


def _synthesis_metadata(
    synthesizer: ConceptSynthesizer,
    config: ConceptSynthesisConfig,
) -> dict[str, Any]:
    synth_config = getattr(synthesizer, "config", config)
    generation_config = getattr(synth_config, "generation_config", {})
    generated_by = str(getattr(synthesizer, "generated_by", "") or "")
    model_name = generated_by or getattr(synth_config, "model", "offline-fallback")
    return {
        "model_name": model_name,
        "model_version": getattr(synth_config, "model_version", None),
        "prompt_version": getattr(synth_config, "prompt_version", "concept-synthesis-v1"),
        "generation_config": dict(generation_config or {}),
    }


def _resolve_evidence(
    requested: tuple[str, ...], symbols: tuple[SymbolFact, ...]
) -> tuple[list[SymbolFact], tuple[str, ...]]:
    """Resolve model evidence while retaining every unanchored request."""

    if not requested:
        return [], ()
    resolved: list[SymbolFact] = []
    missing: list[str] = []
    for raw_query in requested:
        query = raw_query.strip()
        if not query:
            continue
        matched = False
        for symbol in symbols:
            if query in {symbol.name, symbol.qualified_name} or symbol.qualified_name.endswith(
                "." + query
            ):
                if symbol not in resolved:
                    resolved.append(symbol)
                matched = True
                break
        if not matched and query not in missing:
            missing.append(query)
    return resolved, tuple(missing)


def _evidence_excerpt(facts: SourceFacts, evidence: list[SymbolFact]) -> str:
    lines = facts.file.text.splitlines()
    if not evidence:
        return "\n".join(lines[: min(len(lines), 80)]).strip()
    snippets: list[str] = []
    for symbol in evidence[:8]:
        start = max(symbol.start_line - 1, 0)
        end = min(symbol.end_line, len(lines))
        snippets.append("\n".join(lines[start:end]).strip())
    return "\n\n".join(snippets)[:12_000]
