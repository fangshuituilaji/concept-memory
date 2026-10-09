"""Compatibility exports for the semantic synthesis API."""

from .synthesis import (
    ConceptDraft,
    ConceptSynthesisConfig,
    ConceptSynthesizer,
    DashScopeQwenSynthesizer,
    create_default_synthesizer,
)

ModelConfig = ConceptSynthesisConfig
OpenAICompatibleEnricher = DashScopeQwenSynthesizer
__all__ = [
    "ConceptDraft",
    "ConceptSynthesisConfig",
    "ConceptSynthesizer",
    "DashScopeQwenSynthesizer",
    "ModelConfig",
    "OpenAICompatibleEnricher",
    "create_default_synthesizer",
]
