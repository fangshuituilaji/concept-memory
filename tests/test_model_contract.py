from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from memory_system.incremental import FileStateStore, incremental_scan
from memory_system.models import ConceptCard, ConceptKind, SourceLocation
from memory_system.retrieval import QwenFlashRetriever
from memory_system.security import SecurityPolicy
from memory_system.storage import ConceptStore
from memory_system.synthesis import (
    ConceptDraft,
    ConceptSynthesisConfig,
    DashScopeQwenSynthesizer,
    create_default_synthesizer,
)


def _response(content: str):
    return SimpleNamespace(
        output=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )
    )


def _card(name: str, definition: str) -> ConceptCard:
    return ConceptCard.create(
        name=name,
        kind=ConceptKind.CONCEPT,
        definition=definition,
        background=definition,
        location=SourceLocation(
            file_path="unrelated.py", module="unrelated", qualified_name=name,
            start_line=1, end_line=1,
        ),
        source_excerpt="value = 1\n",
    )


class ModelContractTests(unittest.TestCase):
    def test_invalid_qwen_json_fails_without_synthesizing_or_committing_cards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            (root / "sample.py").write_text(
                "def process(value):\n    return value\n", encoding="utf-8"
            )
            database = root / ".concept-memory" / "concepts.sqlite"
            store = ConceptStore(str(database))
            store.open()
            bad_response = _response("not valid JSON")
            try:
                with patch("memory_system.credentials.get_api_key", return_value="unit-test-key"), \
                     patch("dashscope.Generation.call", return_value=bad_response) as call, \
                     patch("memory_system.pipeline.time.sleep"):
                    with self.assertRaisesRegex(ValueError, "JSON|json|response"):
                        incremental_scan(
                            root, store=store, database_path=database, max_workers=1
                        )
                self.assertEqual(call.call_count, 3)
                self.assertTrue(all(c.kwargs["model"] == "qwen-flash" for c in call.call_args_list))
                self.assertEqual(store.all_cards(), [])
                self.assertNotIn("sample.py", FileStateStore(database).load())
            finally:
                store.close()

    def test_default_synthesizer_is_qwen_even_without_a_key(self):
        with patch("memory_system.credentials.get_api_key", return_value=""):
            synthesizer = create_default_synthesizer()
        self.assertIsInstance(synthesizer, DashScopeQwenSynthesizer)
        self.assertEqual(synthesizer.generated_by, "qwen-flash")

    def test_denied_source_is_never_sent_to_any_synthesizer(self):
        class RecordingSynthesizer:
            generated_by = "qwen-flash"
            config = ConceptSynthesisConfig()

            def __init__(self):
                self.calls = 0

            def synthesize(self, facts):
                self.calls += 1
                return [ConceptDraft("概念", "定义", "背景")]

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "private.py"
            source.write_text("def private_value():\n    return 1\n", encoding="utf-8")
            synthesizer = RecordingSynthesizer()
            policy = SecurityPolicy(directory, sensitive_paths=[source])
            with self.assertRaisesRegex(PermissionError, "private.py"):
                from memory_system.pipeline import analyze_path

                analyze_path(
                    source,
                    synthesizer=synthesizer,
                    security_policy=policy,
                    source_sending_policy="online",
                )
        self.assertEqual(synthesizer.calls, 0)

    def test_query_sends_full_catalog_to_qwen_without_local_prefilter(self):
        card = _card("rare internal concept", "definition with no query terms")
        with tempfile.TemporaryDirectory() as directory:
            store = ConceptStore(str(Path(directory) / "concepts.sqlite"))
            store.open()
            store.upsert_cards([card])
            response = _response(json.dumps({"ids": [card.id]}))
            try:
                with patch("memory_system.credentials.get_api_key", return_value="unit-test-key"), \
                     patch("dashscope.Generation.call", return_value=response) as call:
                    results = QwenFlashRetriever().search(store, "unmatched query", limit=5)
                self.assertEqual([item.card.id for item in results], [card.id])
                self.assertEqual(call.call_count, 1)
                self.assertEqual(call.call_args.kwargs["model"], "qwen-flash")
                self.assertIn(card.id, call.call_args.kwargs["messages"][1]["content"])
                self.assertIn(card.name, call.call_args.kwargs["messages"][1]["content"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
