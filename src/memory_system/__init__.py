"""Local file-level semantic concept encoding API."""

# 唯一版本源是 pyproject.toml 的 [project] version；这里必须与它一致，
# 由 tests/test_version.py 与 deploy/release.py 双重校验。
# 离线包通过 PYTHONPATH 加载源码、没有安装元数据可读，所以这里保留字面量。
__version__ = "0.1.0"

from .enrichment import (
    ConceptDraft,
    ConceptSynthesisConfig,
    DashScopeQwenSynthesizer,
    ModelConfig,
    NoOpEnricher,
    OfflineConceptSynthesizer,
    OpenAICompatibleEnricher,
    create_default_synthesizer,
)
from .models import ConceptCard, ConceptKind, SourceLocation
from .incremental import FileStateRecord, FileStateStore, IncrementalScanResult, incremental_scan
from .pipeline import analyze_path, cards_to_json, write_cards_json
from .storage import ConceptSearchResult, ConceptStore
from .cache import CacheKey, ConceptCache
from .security import (
    JsonlAuditRecorder,
    SecurityBoundaryError,
    SecurityDecision,
    SecurityPolicy,
    SourceSendingPolicy,
)

__all__ = [
    "__version__",
    "ConceptCard",
    "ConceptDraft",
    "ConceptKind",
    "ConceptSearchResult",
    "ConceptStore",
    "ConceptSynthesisConfig",
    "CacheKey",
    "ConceptCache",
    "FileStateRecord",
    "FileStateStore",
    "IncrementalScanResult",
    "JsonlAuditRecorder",
    "SecurityBoundaryError",
    "SecurityDecision",
    "SecurityPolicy",
    "SourceSendingPolicy",
    "DashScopeQwenSynthesizer",
    "ModelConfig",
    "NoOpEnricher",
    "OfflineConceptSynthesizer",
    "OpenAICompatibleEnricher",
    "SourceLocation",
    "analyze_path",
    "cards_to_json",
    "create_default_synthesizer",
    "incremental_scan",
    "write_cards_json",
]
