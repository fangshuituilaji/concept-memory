"""Incremental rescan behaviour: file-state skip, card reuse, edge survival."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from memory_system.activation import record_usage
from memory_system.cache import ConceptCache
from memory_system.incremental import (
    FileStateRecord,
    FileStateStore,
    incremental_scan,
)
from memory_system.storage import ConceptStore
from memory_system.synthesis import ConceptDraft


class ScriptedSynthesizer:
    """Counts per-file synthesis calls; the proxy for qwen-flash usage."""

    generated_by = "test-synthesizer"

    def __init__(self, drafts_by_file: dict[str, list[ConceptDraft]] | None = None):
        self._drafts_by_file = drafts_by_file or {}
        self.calls: list[str] = []

    def synthesize(self, facts) -> list[ConceptDraft]:
        relative = facts.file.relative_path
        self.calls.append(relative)
        return list(self._drafts_by_file.get(relative, ()))


def scripted(drafts_by_file: dict[str, list[dict]]) -> ScriptedSynthesizer:
    return ScriptedSynthesizer(
        {
            path: [
                ConceptDraft(
                    name=draft["name"],
                    definition=draft.get("definition", "定义。"),
                    background=draft.get("background", "背景。"),
                    evidence=tuple(draft.get("evidence", ())),
                )
                for draft in drafts
            ]
            for path, drafts in drafts_by_file.items()
        }
    )


ALPHA_TWO_CONCEPTS = [
    {
        "name": "重试逻辑",
        "definition": "对失败请求按退避策略重试。",
        "evidence": ("alpha.retry",),
    },
    {
        "name": "结果缓存",
        "definition": "缓存已计算的结果以复用。",
        "evidence": ("alpha.cache",),
    },
]

ALPHA_SOURCE = (
    "class alpha:\n"
    "    def retry(self, n):\n"
    "        return n + 1\n"
    "\n"
    "    def cache(self, key):\n"
    "        return key\n"
)


class IncrementalScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / ".concept-memory" / "concepts.sqlite"
        self.store = ConceptStore(self.db_path)
        self.store.open()
        self.cache = ConceptCache(":memory:")

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _write_alpha(self, text: str | None = None) -> Path:
        target = self.root / "alpha.py"
        target.write_text(text if text is not None else ALPHA_SOURCE, encoding="utf-8")
        return target

    def _write_beta(self) -> Path:
        target = self.root / "beta.py"
        target.write_text("def beta():\n    return 2\n", encoding="utf-8")
        return target

    def _scan(self, drafts_by_file: dict[str, list[dict]]) -> tuple:
        synthesizer = scripted(drafts_by_file)
        result = incremental_scan(
            self.root,
            store=self.store,
            database_path=str(self.db_path),
            cache=self.cache,
            synthesizer=synthesizer,
        )
        return result, synthesizer

    def _touch(self, path: Path, *, nanoseconds: int = 1_000_000_000) -> None:
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + nanoseconds))

    def test_rescan_processes_only_the_changed_file(self) -> None:
        self._write_alpha()
        self._write_beta()
        beta_concepts = [{"name": "辅助逻辑"}]
        result1, first = self._scan(
            {"alpha.py": ALPHA_TWO_CONCEPTS, "beta.py": beta_concepts}
        )
        self.assertEqual(sorted(first.calls), ["alpha.py", "beta.py"])
        self.assertEqual(len(result1.cards), 3)

        alpha = self._write_alpha(ALPHA_SOURCE.replace("return n + 1", "return n + 2"))
        self._touch(alpha)
        result2, second = self._scan(
            {"alpha.py": ALPHA_TWO_CONCEPTS, "beta.py": beta_concepts}
        )
        self.assertEqual(second.calls, ["alpha.py"])
        self.assertEqual(result2.changed_files, ("alpha.py",))
        self.assertEqual(result2.skipped_files, 1)

        beta_ids_before = {
            card.id for card in result1.cards if card.location.file_path == "beta.py"
        }
        beta_ids_after = {
            card.id for card in result2.cards if card.location.file_path == "beta.py"
        }
        self.assertEqual(beta_ids_before, beta_ids_after)

    def test_stat_only_touch_with_identical_content_is_skipped(self) -> None:
        alpha = self._write_alpha()
        self._scan({"alpha.py": ALPHA_TWO_CONCEPTS})
        self._touch(alpha, nanoseconds=5_000_000_000)

        result, second = self._scan({"alpha.py": ALPHA_TWO_CONCEPTS})
        self.assertEqual(second.calls, [])
        self.assertEqual(result.skipped_files, 1)

        states = FileStateStore(str(self.db_path)).load()
        recorded = states["alpha.py"]
        self.assertEqual(recorded.source_digest, result.cards[0].source_digest)
        self.assertGreater(recorded.mtime, 0)

    def test_deleted_file_has_cards_and_state_pruned(self) -> None:
        self._write_alpha()
        beta = self._write_beta()
        beta_concepts = [{"name": "辅助逻辑"}]
        result1, _ = self._scan(
            {"alpha.py": ALPHA_TWO_CONCEPTS, "beta.py": beta_concepts}
        )
        beta_card_id = next(
            card.id for card in result1.cards if card.location.file_path == "beta.py"
        )
        beta.unlink()

        result2, second = self._scan(
            {"alpha.py": ALPHA_TWO_CONCEPTS, "beta.py": beta_concepts}
        )
        self.assertEqual(second.calls, [])
        self.assertEqual(result2.pruned_cards, 1)
        self.assertIsNone(self.store.get(beta_card_id))
        states = FileStateStore(str(self.db_path)).load()
        self.assertNotIn("beta.py", states)

    def test_state_row_without_cards_is_reanalyzed(self) -> None:
        # Simulate an interrupted first scan: state recorded, cards missing.
        alpha = self._write_alpha()
        stat = alpha.stat()
        FileStateStore(str(self.db_path)).commit_scan(
            [FileStateRecord("alpha.py", stat.st_mtime, stat.st_size, "0" * 64)],
            {"alpha.py"},
        )
        result, synthesizer = self._scan({"alpha.py": ALPHA_TWO_CONCEPTS})
        self.assertEqual(synthesizer.calls, ["alpha.py"])
        self.assertEqual(len(result.cards), 2)

    def test_small_edit_keeps_card_ids_and_usage_edges(self) -> None:
        self._write_alpha()
        result1, _ = self._scan({"alpha.py": ALPHA_TWO_CONCEPTS})
        by_name = {card.name: card for card in result1.cards}
        record_usage(str(self.db_path), [by_name["重试逻辑"].id, by_name["结果缓存"].id])
        record_usage(str(self.db_path), [by_name["重试逻辑"].id, by_name["结果缓存"].id])
        self.assertEqual(
            self._edge_count(by_name["重试逻辑"].id, by_name["结果缓存"].id), 2
        )

        alpha = self._write_alpha(
            "# just a comment\n" + ALPHA_SOURCE.replace("return n + 1", "return n + 1  # tuned")
        )
        self._touch(alpha)
        result2, second = self._scan({"alpha.py": ALPHA_TWO_CONCEPTS})
        self.assertEqual(second.calls, ["alpha.py"])
        ids_before = {card.name: card.id for card in result1.cards}
        ids_after = {card.name: card.id for card in result2.cards}
        self.assertEqual(ids_before, ids_after)

        edge = self._edge_count(by_name["重试逻辑"].id, by_name["结果缓存"].id)
        self.assertEqual(edge, 2)

        retry_card = self.store.get(by_name["重试逻辑"].id)
        assert retry_card is not None
        # derived_from records the fresh content-addressed ID the edited
        # content would have produced; the stored ID itself is inherited.
        self.assertRegex(retry_card.metadata["derived_from"], r"^[0-9a-f]{24}$")
        self.assertNotEqual(retry_card.metadata["derived_from"], retry_card.id)
        self.assertNotEqual(retry_card.source_digest, by_name["重试逻辑"].source_digest)

    def test_removed_concept_is_pruned_with_its_edges(self) -> None:
        self._write_alpha()
        result1, _ = self._scan({"alpha.py": ALPHA_TWO_CONCEPTS})
        by_name = {card.name: card for card in result1.cards}
        record_usage(str(self.db_path), [by_name["重试逻辑"].id, by_name["结果缓存"].id])

        alpha = self._write_alpha(
            "class alpha:\n    def retry(self, n):\n        return n + 1\n"
        )
        self._touch(alpha)
        result2, _ = self._scan(
            {"alpha.py": [draft for draft in ALPHA_TWO_CONCEPTS if draft["name"] != "结果缓存"]}
        )
        self.assertEqual(len(result2.cards), 1)
        self.assertEqual(result2.pruned_cards, 1)
        self.assertEqual(result2.cards[0].id, by_name["重试逻辑"].id)
        self.assertIsNone(self.store.get(by_name["结果缓存"].id))
        self.assertEqual(self._edge_counts(), {})

    def test_renamed_file_keeps_card_ids_and_edges(self) -> None:
        self._write_alpha()
        result1, _ = self._scan({"alpha.py": ALPHA_TWO_CONCEPTS})
        by_name = {card.name: card for card in result1.cards}
        record_usage(str(self.db_path), [by_name["重试逻辑"].id, by_name["结果缓存"].id])

        gamma = self.root / "gamma.py"
        (self.root / "alpha.py").replace(gamma)
        result2, second = self._scan({"gamma.py": ALPHA_TWO_CONCEPTS})
        # Content-addressed cache: identical bytes under the new name hit the
        # cached drafts, so no synthesis call happens; the assertions below
        # still require the ids and usage edges to carry over.
        self.assertEqual(second.calls, [])
        self.assertEqual(result2.renamed_files, ("alpha.py -> gamma.py",))
        self.assertEqual(
            {card.id for card in result2.cards}, {card.id for card in result1.cards}
        )
        self.assertTrue(all(card.location.file_path == "gamma.py" for card in result2.cards))
        self.assertEqual(len(self._edge_counts()), 1)
        states = FileStateStore(str(self.db_path)).load()
        self.assertIn("gamma.py", states)
        self.assertNotIn("alpha.py", states)

    def test_similar_rename_with_shared_evidence_inherits_id(self) -> None:
        self._write_alpha()
        result1, _ = self._scan({"alpha.py": ALPHA_TWO_CONCEPTS})
        retry_id = next(card.id for card in result1.cards if card.name == "重试逻辑")

        alpha = self._write_alpha(ALPHA_SOURCE.replace("return n + 1", "return n + 2"))
        self._touch(alpha)
        result2, _ = self._scan(
            {
                "alpha.py": [
                    {"name": "重试逻辑处理", "definition": "对失败请求按退避策略重试。", "evidence": ("alpha.retry",)},
                    ALPHA_TWO_CONCEPTS[1],
                ]
            }
        )
        renamed = next(card for card in result2.cards if card.name == "重试逻辑处理")
        self.assertEqual(renamed.id, retry_id)

    def test_similar_name_without_shared_evidence_gets_new_id(self) -> None:
        self._write_alpha()
        result1, _ = self._scan({"alpha.py": ALPHA_TWO_CONCEPTS})
        retry_id = next(card.id for card in result1.cards if card.name == "重试逻辑")

        alpha = self._write_alpha(ALPHA_SOURCE.replace("return n + 1", "return n + 2"))
        self._touch(alpha)
        result2, _ = self._scan(
            {
                "alpha.py": [
                    {"name": "重试逻辑处理", "definition": "另一个概念。", "evidence": ("alpha.cache",)},
                    ALPHA_TWO_CONCEPTS[1],
                ]
            }
        )
        renamed = next(card for card in result2.cards if card.name == "重试逻辑处理")
        self.assertNotEqual(renamed.id, retry_id)
        self.assertIsNone(self.store.get(retry_id))

    def _edge_counts(self) -> dict[tuple[str, str], int]:
        connection = sqlite3.connect(str(self.db_path))
        try:
            rows = connection.execute(
                "SELECT source_id, target_id, count FROM concept_usage_edges"
            ).fetchall()
        finally:
            connection.close()
        return {(source, target): count for source, target, count in rows}

    def _edge_count(self, first: str, second: str) -> int:
        return self._edge_counts().get(tuple(sorted((first, second))), 0)


if __name__ == "__main__":
    unittest.main()
