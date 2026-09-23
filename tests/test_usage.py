"""Tests for real-usage edges and spreading activation retrieval.

Edges are only created by real usage (multi-card card-mode ``search_concepts``
fetches); a scan never invents edges.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import memory_system.mcp_server as mcp
from memory_system.activation import (
    SpreadingActivationSearch,
    record_usage,
)
from memory_system.models import ConceptCard, ConceptKind, SourceLocation
from memory_system.storage import ConceptStore


def _card(name: str, definition: str, background: str = "",
          background_concepts: tuple[str, ...] = ()) -> ConceptCard:
    location = SourceLocation(
        file_path="a.py", module="a", qualified_name=name, start_line=1, end_line=2
    )
    return ConceptCard.create(
        name=name,
        kind=ConceptKind.CONCEPT,
        definition=definition,
        background=background,
        background_concepts=background_concepts,
        location=location,
        source_excerpt="x = 1\n",
    )


def _usage_counts(database: str) -> dict[tuple[str, str], int]:
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            "SELECT source_id, target_id, count FROM concept_usage_edges"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        connection.close()
    return {(row[0], row[1]): row[2] for row in rows}


class RecordUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database = str(Path(self._tmp.name) / "concepts.sqlite")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_counts_each_pair_once_per_call_and_accumulates(self) -> None:
        record_usage(self.database, ["a", "b", "c"])
        edges = _usage_counts(self.database)
        self.assertEqual(
            sorted(edges), [("a", "b"), ("a", "c"), ("b", "c")]
        )
        self.assertEqual(set(edges.values()), {1})
        record_usage(self.database, ["b", "a"])
        self.assertEqual(_usage_counts(self.database)[("a", "b")], 2)

    def test_deduplicates_ids_and_ignores_small_fetches(self) -> None:
        record_usage(self.database, ["a"])
        record_usage(self.database, ["a", "a", "b"])
        edges = _usage_counts(self.database)
        self.assertEqual(edges, {("a", "b"): 1})


class UsageSpreadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database = str(Path(self._tmp.name) / "concepts.sqlite")
        self.cards = [
            _card("语法解析", "把源码变成语法树。"),
            _card("结果组织", "整理处理结果。"),
            _card("日志输出", "把信息写到日志。"),
        ]
        self.store = ConceptStore(self.database)
        self.store.open()
        self.store.upsert_cards(self.cards)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_usage_edges_drive_spreading_activation(self) -> None:
        record_usage(self.database, [self.cards[0].id, self.cards[1].id])
        record_usage(self.database, [self.cards[1].id, self.cards[2].id])
        searcher = SpreadingActivationSearch(self.database)
        try:
            results = searcher.search_from_seeds({self.cards[0].id: 1.0})
        finally:
            searcher.close()
        self.assertEqual(
            [r.card["name"] for r in results], ["语法解析", "结果组织", "日志输出"]
        )
        self.assertEqual(results[0].hop, 0)
        self.assertEqual(results[1].hop, 1)
        # one co-use -> edge confidence 0.5; spread = 1.0 * decay 0.5 * 0.5
        self.assertAlmostEqual(results[1].activation, 0.25)
        self.assertEqual(results[2].hop, 2)
        self.assertEqual(results[2].path, ["语法解析", "结果组织", "日志输出"])

    def test_repeated_co_use_strengthens_edges(self) -> None:
        pair = [self.cards[0].id, self.cards[1].id]
        record_usage(self.database, pair)
        record_usage(self.database, pair)
        searcher = SpreadingActivationSearch(self.database)
        try:
            results = searcher.search_from_seeds({self.cards[0].id: 1.0})
        finally:
            searcher.close()
        # two co-uses -> edge confidence 2/3; spread = 1.0 * decay 0.5 * 2/3
        self.assertAlmostEqual(results[1].activation, 1 / 3, places=3)

    def test_no_usage_yields_only_seeds(self) -> None:
        searcher = SpreadingActivationSearch(self.database)
        try:
            results = searcher.search("语法解析")
        finally:
            searcher.close()
        self.assertEqual([r.card["name"] for r in results], ["语法解析"])

    def test_loads_graph_tolerantly_when_usage_table_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "bare.sqlite")
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE concept_cards (id TEXT, card_json TEXT)")
            connection.commit()
            connection.close()
            searcher = SpreadingActivationSearch(database)
            try:
                self.assertEqual(searcher.search("任何词"), [])
            finally:
                searcher.close()


class _PassthroughRetriever:
    """Test stand-in: recall via FTS, keep store order (no model calls)."""

    def search(self, store, query, limit):
        return store.search_any(query, limit=limit)


class McpUsageWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database = str(Path(self._tmp.name) / "concepts.sqlite")
        self._saved = (mcp._store, mcp._cache, mcp._database_path,
                       mcp._project_root, mcp._scan_thread, mcp._retriever)
        self.cards = [
            _card("语法解析", "把源码变成语法树。"),
            _card("结果组织", "整理处理结果。"),
            _card("日志输出", "把信息写到日志。"),
        ]
        mcp._store = ConceptStore(self.database)
        mcp._store.open()
        mcp._store.upsert_cards(self.cards)
        mcp._database_path = self.database
        mcp._retriever = _PassthroughRetriever()

    def tearDown(self) -> None:
        if mcp._store is not None:
            mcp._store.close()
        (mcp._store, mcp._cache, mcp._database_path,
         mcp._project_root, mcp._scan_thread, mcp._retriever) = self._saved
        self._tmp.cleanup()

    def test_multi_card_fetch_records_usage_and_feeds_search(self) -> None:
        payload = mcp.search_concepts(card_ids=[self.cards[0].id, self.cards[1].id])
        self.assertEqual(
            [card["name"] for card in payload["cards"]], ["语法解析", "结果组织"]
        )
        self.assertEqual(
            _usage_counts(self.database),
            {tuple(sorted((self.cards[0].id, self.cards[1].id))): 1},
        )

        mcp.search_concepts(card_ids=[self.cards[1].id, self.cards[2].id])
        search = mcp.search_concepts("语法解析")
        self.assertEqual([r["name"] for r in search["results"]], ["语法解析"])
        related = search["related"]
        self.assertEqual([item["name"] for item in related], ["结果组织", "日志输出"])
        self.assertEqual(related[0]["file_path"], "a.py")
        self.assertEqual(related[0]["hop"], 1)
        self.assertEqual(related[0]["via"], "语法解析 → 结果组织")
        self.assertEqual(related[1]["hop"], 2)

    def test_single_card_fetch_records_nothing(self) -> None:
        payload = mcp.search_concepts(card_ids=self.cards[0].id)
        self.assertEqual(payload["cards"][0]["name"], "语法解析")
        self.assertEqual(_usage_counts(self.database), {})
        search = mcp.search_concepts("语法解析")
        self.assertNotIn("related", search)

    def test_unknown_cards_reported_without_usage_writes(self) -> None:
        payload = mcp.search_concepts(card_ids=["ghost-a", "ghost-b"])
        self.assertIn("error", payload)
        self.assertEqual(_usage_counts(self.database), {})

    def test_partial_hit_returns_found_cards_and_marks_missing(self) -> None:
        payload = mcp.search_concepts(card_ids=[self.cards[0].id, "ghost"])
        self.assertEqual(len(payload["cards"]), 1)
        self.assertEqual(payload["not_found"], ["ghost"])
        self.assertEqual(_usage_counts(self.database), {})

    def test_json_string_card_ids_are_accepted(self) -> None:
        import json

        payload = mcp.search_concepts(
            card_ids=json.dumps([self.cards[0].id, self.cards[1].id])
        )
        self.assertEqual(len(payload["cards"]), 2)
        self.assertEqual(len(_usage_counts(self.database)), 1)

    def test_query_and_card_ids_are_mutually_exclusive(self) -> None:
        self.assertIn("error", mcp.search_concepts())
        self.assertIn(
            "error", mcp.search_concepts(query="语法解析", card_ids=[self.cards[0].id])
        )
        self.assertEqual(_usage_counts(self.database), {})

    def test_query_results_stay_thin(self) -> None:
        long_card = _card(
            "长定义概念", "首行定义" + "很" * 200 + "。\n第二行不应该出现。"
        )
        mcp._store.upsert_cards([long_card])
        search = mcp.search_concepts("长定义概念")
        self.assertEqual(len(search["results"]), 1)
        summary = search["results"][0]
        self.assertNotIn("source_excerpt", summary)
        self.assertNotIn("definition", summary)
        preview = summary["definition_preview"]
        self.assertTrue(preview.startswith("首行定义"))
        self.assertLessEqual(len(preview), 121)
        self.assertTrue(preview.endswith("…"))
        self.assertIn("card_ids", search["next_step"])


if __name__ == "__main__":
    unittest.main()
