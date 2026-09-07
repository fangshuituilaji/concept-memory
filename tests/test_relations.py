"""Tests for relation discovery, spreading activation, and MCP wiring."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import memory_system.mcp_server as mcp
from memory_system.activation import SpreadingActivationSearch, store_relations
from memory_system.models import ConceptCard, ConceptKind, SourceLocation
from memory_system.relations import (
    ConceptRelation,
    _parse_relations,
    cooccurrence_relations,
)
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


def _store_cards(database: str, cards: list[ConceptCard]) -> ConceptStore:
    store = ConceptStore(database)
    store.open()
    store.upsert_cards(cards)
    return store


class ParseRelationsTests(unittest.TestCase):
    def test_validates_ids_types_and_deduplicates(self) -> None:
        payload = {
            "relations": [
                {"source": "a", "target": "b", "type": "enables",
                 "confidence": 0.9, "explanation": "ok"},
                {"source": "a", "target": "ghost", "type": "enables"},
                {"source": "a", "target": "b", "type": "invents-type"},
                {"source": "b", "target": "a", "type": "enables"},
                {"source": "a", "target": "a", "type": "enables"},
                {"source": "a", "target": "b", "type": "related_to"},
            ]
        }
        relations = _parse_relations(json.dumps(payload), {"a", "b"})
        self.assertEqual(
            [(r.source_id, r.target_id, r.relation_type) for r in relations],
            [("a", "b", "enables"), ("b", "a", "enables")],
        )

    def test_tolerates_markdown_fence_and_clamps_confidence(self) -> None:
        text = '```json\n{"relations": [{"source": "a", "target": "b", "type": "constrains", "confidence": 7}]}\n```'
        relations = _parse_relations(text, {"a", "b"})
        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0].confidence, 1.0)


class SpreadingActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database = str(Path(self._tmp.name) / "concepts.sqlite")
        cards = [
            _card("语法解析", "把源码变成语法树。", background_concepts=("AST",)),
            _card("结果组织", "整理处理结果。", background_concepts=("语法解析",)),
            _card("日志输出", "把信息写到日志。"),
        ]
        self.store = _store_cards(self.database, cards)
        store_relations(
            self.database,
            [
                ConceptRelation(
                    source_id=cards[0].id, target_id=cards[1].id,
                    relation_type="enables", confidence=1.0, explanation="",
                ),
                ConceptRelation(
                    source_id=cards[1].id, target_id=cards[2].id,
                    relation_type="related_to", confidence=0.5, explanation="",
                ),
            ],
        )

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_seed_scores_spread_with_decay_hop_and_path(self) -> None:
        cards = {c.name: c for c in self.store.all_cards()}
        searcher = SpreadingActivationSearch(self.database)
        try:
            results = searcher.search_from_seeds({cards["语法解析"].id: 1.0})
        finally:
            searcher.close()
        self.assertEqual([r.card["name"] for r in results], ["语法解析", "结果组织", "日志输出"])
        self.assertEqual(results[0].hop, 0)
        self.assertEqual(results[1].hop, 1)
        self.assertAlmostEqual(results[1].activation, 0.5)
        self.assertEqual(results[2].hop, 2)
        self.assertEqual(results[2].path, ["语法解析", "结果组织", "日志输出"])

    def test_query_seeds_by_name_first(self) -> None:
        searcher = SpreadingActivationSearch(self.database)
        try:
            results = searcher.search("语法解析")
        finally:
            searcher.close()
        self.assertEqual(results[0].card["name"], "语法解析")
        self.assertAlmostEqual(results[0].activation, 1.0)

    def test_loads_graph_tolerantly_when_relations_missing(self) -> None:
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


class CooccurrenceRelationsTests(unittest.TestCase):
    def test_links_cards_that_cite_each_other(self) -> None:
        a = _card("语法解析", "解析源码。")
        b = _card("结果组织", "整理结果。", background_concepts=("语法解析",))
        c = _card("日志输出", "写日志。")
        relations = cooccurrence_relations([card.to_dict() for card in (a, b, c)])
        self.assertEqual(len(relations), 1)
        edge = relations[0]
        pair = {edge.source_id, edge.target_id}
        self.assertEqual(pair, {a.id, b.id})
        self.assertEqual(edge.relation_type, "related_to")

    def test_no_self_edges_on_shared_names(self) -> None:
        a = _card("语法解析", "解析源码。", background_concepts=("语法解析",))
        self.assertEqual(cooccurrence_relations([a.to_dict()]), [])


class McpSearchWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database = str(Path(self._tmp.name) / "concepts.sqlite")
        self._saved = (mcp._store, mcp._cache, mcp._database_path,
                       mcp._project_root, mcp._scan_thread)
        self.cards = [
            _card("语法解析", "把源码变成语法树。", background_concepts=("AST",)),
            _card("结果组织", "整理处理结果。"),
            _card("日志输出", "把信息写到日志。"),
        ]
        mcp._store = _store_cards(self.database, self.cards)
        mcp._database_path = self.database

    def tearDown(self) -> None:
        if mcp._store is not None:
            mcp._store.close()
        (mcp._store, mcp._cache, mcp._database_path,
         mcp._project_root, mcp._scan_thread) = self._saved
        self._tmp.cleanup()

    def test_search_appends_spread_related_cards(self) -> None:
        store_relations(
            self.database,
            [
                ConceptRelation(
                    source_id=self.cards[0].id, target_id=self.cards[1].id,
                    relation_type="enables", confidence=1.0, explanation="",
                ),
                ConceptRelation(
                    source_id=self.cards[1].id, target_id=self.cards[2].id,
                    relation_type="related_to", confidence=1.0, explanation="",
                ),
            ],
        )
        payload = mcp.search_concepts("语法解析")
        self.assertEqual(payload["results"][0]["name"], "语法解析")
        related = payload["related"]
        self.assertEqual([item["name"] for item in related], ["结果组织", "日志输出"])
        self.assertEqual(related[0]["hop"], 1)
        self.assertEqual(related[0]["via"], "语法解析 → 结果组织")
        self.assertEqual(related[1]["hop"], 2)

    def test_search_without_relations_stays_flat(self) -> None:
        payload = mcp.search_concepts("语法解析")
        self.assertEqual(payload["results"][0]["name"], "语法解析")
        self.assertNotIn("related", payload)

    def test_uninitialized_server_raises_clear_error(self) -> None:
        saved_store, saved_path = mcp._store, mcp._database_path
        mcp._store, mcp._database_path = None, ""
        try:
            with self.assertRaises(RuntimeError):
                mcp.search_concepts("任意")
        finally:
            mcp._store, mcp._database_path = saved_store, saved_path


if __name__ == "__main__":
    unittest.main()
