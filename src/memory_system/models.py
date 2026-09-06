"""Data models for semantic, explainable concept cards."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import Any, Mapping


class ConceptKind(StrEnum):
    """Concept categories emitted by the phase-one encoder."""

    CONCEPT = "concept"
    # Kept for reading cards produced by the first prototype.
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    ALGORITHM = "algorithm"


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """A stable, human-readable location in the analyzed source file."""

    file_path: str
    module: str | None
    qualified_name: str
    start_line: int
    end_line: int
    start_column: int = 0
    end_column: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceLocation":
        return cls(
            file_path=str(data["file_path"]),
            module=data.get("module"),
            qualified_name=str(data["qualified_name"]),
            start_line=int(data["start_line"]),
            end_line=int(data["end_line"]),
            start_column=int(data.get("start_column", 0)),
            end_column=int(data.get("end_column", 0)),
        )


@dataclass(frozen=True, slots=True)
class ConceptCard:
    """One higher-level concept synthesized from a whole code file."""

    id: str
    name: str
    kind: ConceptKind
    definition: str
    background: str
    background_concepts: tuple[str, ...]
    location: SourceLocation
    source_excerpt: str
    source_digest: str
    metadata: dict[str, Any] = field(default_factory=dict)
    generated_by: str = "offline-fallback"

    @classmethod
    def create(
        cls,
        *,
        name: str,
        kind: ConceptKind,
        definition: str,
        background: str = "",
        background_concepts: tuple[str, ...] | list[str] = (),
        location: SourceLocation,
        source_excerpt: str,
        metadata: Mapping[str, Any] | None = None,
        generated_by: str = "offline-fallback",
        source_digest: str | None = None,
    ) -> "ConceptCard":
        digest = source_digest or sha256(source_excerpt.encode("utf-8")).hexdigest()
        stable_key = "|".join(
            (
                location.file_path,
                location.qualified_name,
                kind.value,
                name,
                digest,
            )
        )
        card_id = sha256(stable_key.encode("utf-8")).hexdigest()[:24]
        return cls(
            id=card_id,
            name=name.strip(),
            kind=kind,
            definition=definition.strip(),
            background=background.strip(),
            background_concepts=tuple(
                dict.fromkeys(
                    item.strip() for item in background_concepts if item and item.strip()
                )
            ),
            location=location,
            source_excerpt=source_excerpt,
            source_digest=digest,
            metadata=dict(metadata or {}),
            generated_by=generated_by,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        data["background_concepts"] = list(self.background_concepts)
        data["location"] = self.location.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConceptCard":
        # ``background`` defaults to the old definition field so prototype
        # JSON files remain readable after the semantic-card redesign.
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            kind=ConceptKind(str(data["kind"])),
            definition=str(data["definition"]),
            background=str(data.get("background", data.get("definition", ""))),
            background_concepts=tuple(
                str(item) for item in data.get("background_concepts", [])
            ),
            location=SourceLocation.from_dict(data["location"]),
            source_excerpt=str(data.get("source_excerpt", "")),
            source_digest=str(data.get("source_digest", "")),
            metadata=dict(data.get("metadata", {})),
            generated_by=str(data.get("generated_by", "offline-fallback")),
        )
