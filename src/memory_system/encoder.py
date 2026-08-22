"""Turn model-generated file concepts into anchored concept cards."""

from __future__ import annotations

from dataclasses import dataclass

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
        evidence = _resolve_evidence(draft.evidence, facts.symbols)
        if evidence:
            start_line = min(symbol.start_line for symbol in evidence)
            end_line = max(symbol.end_line for symbol in evidence)
            qualified_name = "file:" + facts.module + "#" + ",".join(
                symbol.qualified_name for symbol in evidence[:5]
            )
        else:
            start_line, end_line = 1, max(1, len(facts.file.text.splitlines()))
            qualified_name = f"file:{facts.module}"
        location = SourceLocation(
            file_path=facts.file.relative_path,
            module=facts.module,
            qualified_name=qualified_name,
            start_line=start_line,
            end_line=end_line,
        )
        evidence_locations = [
            {
                "qualified_name": symbol.qualified_name,
                "kind": symbol.kind,
                "start_line": symbol.start_line,
                "end_line": symbol.end_line,
            }
            for symbol in evidence
        ]
        return ConceptCard.create(
            name=draft.name,
            kind=ConceptKind.CONCEPT,
            definition=draft.definition,
            background=draft.background,
            background_concepts=draft.background_concepts,
            location=location,
            source_excerpt=_evidence_excerpt(facts, evidence),
            source_digest=facts.source_digest,
            metadata={
                "concept_index": index,
                "file_concept_count": count,
                "evidence_symbols": [symbol.qualified_name for symbol in evidence],
                "evidence_locations": evidence_locations,
            },
            generated_by=getattr(
                self.synthesizer,
                "generated_by",
                "qwen-flash" if self.synthesizer.__class__.__name__.startswith("DashScope") else "offline-fallback",
            ),
        )


def _resolve_evidence(
    requested: tuple[str, ...], symbols: tuple[SymbolFact, ...]
) -> list[SymbolFact]:
    if not requested:
        return []
    resolved: list[SymbolFact] = []
    for query in requested:
        query = query.strip()
        for symbol in symbols:
            if query in {symbol.name, symbol.qualified_name} or symbol.qualified_name.endswith(
                "." + query
            ):
                if symbol not in resolved:
                    resolved.append(symbol)
                break
    return resolved


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
