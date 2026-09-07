"""Tests for Qwen-Flash driven retrieval and OR-mode candidate recall."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from memory_system.models import ConceptCard, ConceptKind, SourceLocation
from memory_system.retrieval import QwenFlashRetriever
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
    def __init__(self, responses):
        super().__init__()
        self._responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def _call(self, messages):
        self.calls.append(messages)
        return self._responses.pop(0)


class QwenFlashRetrieverTests(unittest.TestCase):
    def test_search_expands_recalls_and_reranks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ConceptStore(str(Path(tmp) / "concepts.sqlite"))
            store.open()
            cards = [
                _card("全文检索", "FTS5 匹配。"),
                _card("概念网络可视化", "力导向图。"),
            ]
            store.upsert_cards(cards)
            retriever = _ScriptedRetriever(
                [
                    '{"keywords": ["检索"]}',
                    '{"order": [1, 0]}',
                ]
            )
            results = retriever.search(store, "web 页面 可视化", limit=10)
            store.close()
        self.assertEqual([r.card.name for r in results], ["概念网络可视化", "全文检索"])

    def test_missing_api_key_fails_without_offline_fallback(self) -> None:
        retriever = QwenFlashRetriever()
        with tempfile.TemporaryDirectory() as tmp:
            store = ConceptStore(str(Path(tmp) / "concepts.sqlite"))
            store.open()
            try:
                with mock.patch.dict(os.environ, clear=True):
                    os.environ.pop("DASHSCOPE_API_KEY", None)
                    with self.assertRaises(RuntimeError):
                        retriever.search(store, "任意查询", limit=5)
            finally:
                store.close()

    def test_rerank_appends_candidates_missed_by_the_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ConceptStore(str(Path(tmp) / "concepts.sqlite"))
            store.open()
            cards = [_card("甲", "第一个。"), _card("乙", "第二个。")]
            store.upsert_cards(cards)
            candidates = store.search_any("甲 乙", limit=10)
            retriever = _ScriptedRetriever(['{"order": [1]}'])
            ranked = retriever.rerank("查询", candidates)
            store.close()
        self.assertEqual([r.card.name for r in ranked], ["乙", "甲"])


if __name__ == "__main__":
    unittest.main()
