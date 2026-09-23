"""Tests for Qwen-Flash driven retrieval and OR-mode candidate recall."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from memory_system.models import ConceptCard, ConceptKind, SourceLocation
from memory_system.retrieval import QwenFlashRetriever, RetrievalConfig
from memory_system.storage import ConceptStore


def _card(name: str, definition: str) -> ConceptCard:
    location = SourceLocation(
        file_path="a.py", module="a", qualified_name=name, start_line=1, end_line=2
    )
    return ConceptCard.create(
        name=name,
        kind=ConceptKind.CONCEPT,
        definition=definition,
        background=definition,
        location=location,
        source_excerpt="x = 1\n",
    )


class SearchAnyRecallTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = ConceptStore(str(Path(self._tmp.name) / "concepts.sqlite"))
        self.store.open()
        self.store.upsert_cards(
            [
                _card("概念网络可视化", "力导向图展示概念网络。"),
                _card("全文检索", "FTS5 多字段匹配。"),
                _card("传播激活", "沿使用边扩散。"),
            ]
        )

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_multi_term_query_recalls_any_match(self) -> None:
        # AND-mode search() returns nothing here; search_any() must recall.
        self.assertEqual(self.store.search("web 页面 网络可视化 节点"), [])
        results = self.store.search_any("web 页面 网络可视化 节点", limit=10)
        self.assertEqual([r.card.name for r in results], ["概念网络可视化"])

    def test_like_fallback_recalls_single_cjk_term(self) -> None:
        results = self.store.search_any("可视化", limit=10)
        self.assertEqual([r.card.name for r in results], ["概念网络可视化"])


class _ScriptedRetriever(QwenFlashRetriever):
    def __init__(self, responses, config=None):
        super().__init__(config)
        self._responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def _call(self, messages):
        self.calls.append(messages)
        return self._responses.pop(0)


def _ids(*cards: ConceptCard) -> list[str]:
    return [card.id for card in cards]


class QwenFlashRetrieverTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _store_with(self, *cards: ConceptCard) -> ConceptStore:
        store = ConceptStore(str(Path(self._tmp.name) / "concepts.sqlite"))
        store.open()
        store.upsert_cards(list(cards))
        return store

    def test_search_selects_from_full_catalog_in_one_call(self) -> None:
        fts, web = _card("全文检索", "FTS 匹配。"), _card("概念网络可视化", "力导向图。")
        store = self._store_with(fts, web)
        try:
            catalog_order = store.all_cards()
            retriever = _ScriptedRetriever(
                ['{"ids": [%s]}' % ", ".join('"%s"' % i for i in _ids(web, fts))]
            )
            results = retriever.search(store, "web 页面 可视化", limit=10)
        finally:
            store.close()
        self.assertEqual([r.card.name for r in results], ["概念网络可视化", "全文检索"])
        # The whole catalog reached the model in a single call.
        self.assertEqual(len(retriever.calls), 1)
        sent_catalog = retriever.calls[0][1]["content"]
        self.assertIn("概念网络可视化", sent_catalog)
        self.assertIn("全文检索", sent_catalog)
        self.assertIn(catalog_order[0].id, sent_catalog)

    def test_large_catalog_is_ranked_in_batches(self) -> None:
        first, second = _card("甲", "第一个。"), _card("乙", "第二个。")
        config = RetrievalConfig(rank_batch_size=1, shortlist_per_batch=10)
        store = self._store_with(first, second)
        try:
            retriever = _ScriptedRetriever(
                [
                    '{"ids": ["%s"]}' % first.id,
                    '{"ids": ["%s"]}' % second.id,
                    '{"ids": [%s]}' % ", ".join('"%s"' % i for i in _ids(second, first)),
                ],
                config=config,
            )
            results = retriever.search(store, "查询", limit=10)
        finally:
            store.close()
        self.assertEqual([r.card.name for r in results], ["乙", "甲"])
        # Two batch calls plus one final call over the shortlist.
        self.assertEqual(len(retriever.calls), 3)

    def test_rank_appends_cards_missed_by_the_model(self) -> None:
        first, second = _card("甲", "第一个。"), _card("乙", "第二个。")
        retriever = _ScriptedRetriever(['{"ids": ["%s"]}' % second.id])
        ranked = retriever.rank("查询", [first, second])
        self.assertEqual([card.name for card in ranked], ["乙", "甲"])

    def test_rank_fails_loudly_on_garbage(self) -> None:
        retriever = _ScriptedRetriever(["not json at all"])
        with self.assertRaises(RuntimeError):
            retriever.rank("查询", [_card("甲", "第一个。")])

    def test_missing_api_key_fails_without_offline_fallback(self) -> None:
        retriever = QwenFlashRetriever()
        store = self._store_with(_card("甲", "第一个。"))
        try:
            with mock.patch.dict(os.environ, clear=True):
                os.environ.pop("DASHSCOPE_API_KEY", None)
                with self.assertRaises(RuntimeError):
                    retriever.search(store, "任意查询", limit=5)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
