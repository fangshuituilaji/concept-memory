"""Compatibility exports for the semantic synthesis API."""

from .synthesis import (
    ConceptDraft,
    ConceptSynthesisConfig,
    ConceptSynthesizer,
    DashScopeQwenSynthesizer,
    OfflineConceptSynthesizer,
    create_default_synthesizer,
)

ModelConfig = ConceptSynthesisConfig
OpenAICompatibleEnricher = DashScopeQwenSynthesizer
NoOpEnricher = OfflineConceptSynthesizer

__all__ = [
    "ConceptDraft",
    "ConceptSynthesisConfig",
    "ConceptSynthesizer",
    "DashScopeQwenSynthesizer",
    "ModelConfig",
    "NoOpEnricher",
    "OfflineConceptSynthesizer",
    "OpenAICompatibleEnricher",
    "create_default_synthesizer",
]
