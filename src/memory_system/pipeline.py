"""Public Phase-one semantic concept pipeline."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .cache import CacheKey, ConceptCache
from .encoder import FileConceptEncoder
from .extractor import SourceFacts, TreeSitterSourceAnalyzer
from .models import ConceptCard
from .readers import DEFAULT_EXTENSIONS, CodeFile, discover_code_files, read_code_file
from .security import JsonlAuditRecorder, SecurityPolicy, SourceSendingPolicy
from .synthesis import (
    ConceptDraft,
    ConceptSynthesisConfig,
    ConceptSynthesizer,
    OfflineConceptSynthesizer,
    create_default_synthesizer,
)


# Per-file model-call retry budget before the whole scan reports an error.
SYNTHESIS_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class _PreparedSynthesizer:
    """A per-file draft provider used for cache hits and security fallbacks."""

    drafts: tuple[ConceptDraft, ...]
    config: ConceptSynthesisConfig
    generated_by: str

    def synthesize(self, facts: SourceFacts) -> list[ConceptDraft]:
        return list(self.drafts)


@dataclass
class _StagedFile:
    """One discovered file staged between local analysis and draft synthesis."""

    code_file: CodeFile
    facts: SourceFacts
    synthesizer: ConceptSynthesizer
    effective_config: ConceptSynthesisConfig
    effective_model: str
    cache_key: CacheKey
    drafts: list[ConceptDraft] | None = field(default=None)


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
    max_workers: int = 6,
    progress_callback: Callable[[int, int, str], None] | None = None,
    files: Iterable[Path] | None = None,
) -> list[ConceptCard]:
    """Analyze each source file into a small set of file-level concepts.

    Source is always read locally.  When an online synthesizer is selected,
    ``security_policy`` can deny sensitive files before the model receives them;
    those files use the deterministic offline synthesizer instead.  ``cache``
    and ``cache_path`` enable source/configuration-keyed draft reuse.  Draft
    synthesis issues one model call per file, so the calls run on a thread pool
    of ``max_workers``; ``progress_callback(done, total, relative_path)`` fires
    as each file's drafts resolve.  ``files`` restricts the run to an explicit
    file set (incremental rescans); discovery and cache pruning are skipped in
    that mode because the caller already knows the full file list.
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
    if files is None:
        discovered_files = discover_code_files(target, extensions=extensions)
        if active_cache is not None and target.is_dir():
            active_cache.prune_missing(
                {file_path.resolve() for file_path in discovered_files}, root=root
            )
    else:
        # Incremental mode: the caller partitioned the file set and owns
        # file-level cleanup, so analysis touches exactly these files.
        discovered_files = sorted(
            {Path(file_path) for file_path in files}, key=lambda item: item.as_posix()
        )
    cards: list[ConceptCard] = []
    staged: list[_StagedFile] = []
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
            cache_key = CacheKey(
                source_digest=facts.source_digest,
                model_name=effective_model,
                model_version=effective_config.model_version,
                prompt_version=effective_config.prompt_version,
                generation_config=effective_config.to_dict(),
                file_path=code_file.path,
            )
            drafts: list[ConceptDraft] | None = None
            if active_cache is not None:
                cached = active_cache.get(cache_key)
                if cached is not None:
                    drafts = _drafts_from_json(cached)
            staged.append(
                _StagedFile(
                    code_file=code_file,
                    facts=facts,
                    synthesizer=file_synthesizer,
                    effective_config=effective_config,
                    effective_model=effective_model,
                    cache_key=cache_key,
                    drafts=drafts,
                )
            )

        done = sum(1 for item in staged if item.drafts is not None)
        if progress_callback is not None:
            progress_callback(done, len(staged), "")
        # One model call per file; qwen-flash live quota (30k RPM) sits far
        # above this pool size, and cache writes stay on this main thread.
        outstanding = [item for item in staged if item.drafts is None]

        def _synthesize_with_retries(item: _StagedFile) -> list[ConceptDraft]:
            delay = 2.0
            for attempt in range(SYNTHESIS_ATTEMPTS):
                try:
                    return item.synthesizer.synthesize(item.facts)
                except Exception:
                    if attempt == SYNTHESIS_ATTEMPTS - 1:
                        raise
                    time.sleep(delay * (attempt + 1))
            raise AssertionError("unreachable")

        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
            future_to_item = {
                pool.submit(_synthesize_with_retries, item): item
                for item in outstanding
            }
            for future in as_completed(future_to_item):
                item = future_to_item[future]
                drafts = future.result()
                item.drafts = drafts
                if active_cache is not None:
                    active_cache.put(
                        item.cache_key,
                        [_draft_to_json(draft) for draft in drafts],
                        file_path=item.code_file.path,
                    )
                done += 1
                if progress_callback is not None:
                    progress_callback(done, len(staged), item.code_file.relative_path)

        for item in staged:
            assert item.drafts is not None
            prepared = _PreparedSynthesizer(
                drafts=tuple(item.drafts),
                config=item.effective_config,
                generated_by=item.effective_model,
            )
            cards.extend(FileConceptEncoder(prepared, item.effective_config).encode(item.facts))
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
