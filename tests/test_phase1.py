from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from memory_system import (
    ConceptCard,
    ConceptKind,
    ConceptStore,
    ConceptSynthesisConfig,
    SourceLocation,
    analyze_path,
)
from memory_system.pipeline import cards_to_json
from memory_system.synthesis import ConceptDraft, OfflineConceptSynthesizer


class StaticSynthesizer:
    generated_by = "test-synthesizer"

    def synthesize(self, facts):
        return [
            ConceptDraft(
                name="语法解析",
                definition="将源码转换为结构化语法单元。",
                background="先解析源码，再递归遍历语法节点，为后续处理建立结构基础。",
                background_concepts=("AST",),
                evidence=("Catalog.find",),
            ),
            ConceptDraft(
                name="结果组织",
                definition="将处理结果组织成稳定的数据结构。",
                background="相关函数负责整理中间结果，并将结果交给调用方继续使用。",
                evidence=("sort_values",),
            ),
        ]


class PhaseOneTests(unittest.TestCase):
    def test_file_is_summarized_into_small_semantic_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.py"
            source.write_text(
                '''
class Catalog:
    """Stores catalog entries."""

    def find(self, values, target):
        return next((value for value in values if value == target), None)

def sort_values(values):
    return sorted(values)
''',
                encoding="utf-8",
            )
            cards = analyze_path(
                source,
                synthesizer=StaticSynthesizer(),
                config=ConceptSynthesisConfig(target_concepts=3, max_concepts=9),
            )

        self.assertEqual(len(cards), 2)
        self.assertTrue(all(card.kind is ConceptKind.CONCEPT for card in cards))
        self.assertTrue(all(card.background for card in cards))
        self.assertEqual(cards[0].metadata["evidence_symbols"], ["Catalog.find"])
        self.assertEqual(cards[0].metadata["validation_status"], "validated")
        self.assertEqual(cards[0].metadata["model_name"], "test-synthesizer")
        self.assertEqual(cards[0].location.start_line, 5)
        self.assertEqual(cards[0].location.end_line, 6)

    def test_unanchored_evidence_is_retained_and_marked(self) -> None:
        source = Path("src/memory_system/models.py").resolve()
        class UnanchoredSynthesizer:
            generated_by = "test-synthesizer"
            def synthesize(self, facts):
                return [
                    ConceptDraft(
                        name="未锚定概念",
                        definition="一个无法由符号证据完全支持的概念。",
                        background="该概念用于验证证据状态。",
                        evidence=("Missing.symbol",),
                    )
                ]

        cards = analyze_path(
            source,
            synthesizer=UnanchoredSynthesizer(),
            config=ConceptSynthesisConfig(),
        )
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].metadata["validation_status"], "unanchored")
        self.assertEqual(cards[0].metadata["unanchored_evidence"], ["Missing.symbol"])

    def test_offline_fallback_stays_within_file_limit(self) -> None:
        source = Path("src/memory_system/extractor.py").resolve()
        cards = analyze_path(
            source,
            synthesizer=OfflineConceptSynthesizer(
                ConceptSynthesisConfig(target_concepts=3, max_concepts=9)
            ),
        )
        self.assertGreaterEqual(len(cards), 2)
        self.assertLessEqual(len(cards), 9)
        self.assertTrue(all(card.kind is ConceptKind.CONCEPT for card in cards))
        self.assertTrue(all(len(card.name) <= 80 for card in cards))

    def test_json_round_trip(self) -> None:
        location = SourceLocation(
            file_path="sample.py",
            module="sample",
            qualified_name="file:sample#demo",
            start_line=1,
            end_line=2,
        )
        card = ConceptCard.create(
            name="语法解析",
            kind=ConceptKind.CONCEPT,
            definition="将源码转换为结构化语法单元。",
            background="先解析源码，再递归遍历语法节点。",
            background_concepts=["AST", "AST"],
            location=location,
            source_excerpt="def demo(): pass",
        )
        restored = ConceptCard.from_dict(json.loads(cards_to_json([card]))[0])
        self.assertEqual(restored, card)
        self.assertEqual(restored.background_concepts, ("AST",))

    def test_sqlite_search_includes_long_background(self) -> None:
        card = ConceptCard.create(
            name="语法解析",
            kind=ConceptKind.CONCEPT,
            definition="将源码转换为结构化语法单元。",
            background="通过抽象语法树建立后续概念提取所需的结构基础。",
            location=SourceLocation(
                file_path="extractor.py",
                module="extractor",
                qualified_name="file:extractor",
                start_line=1,
                end_line=10,
            ),
            source_excerpt="ast.parse(source)",
        )
        with ConceptStore(":memory:") as store:
            store.upsert_cards([card])
            results = store.search("抽象语法树")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].card.id, card.id)
        self.assertIn("概念背景", results[0].explanation)
