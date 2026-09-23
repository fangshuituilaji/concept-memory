"""Tests for the readiness gate: search waits for an in-flight scan."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

import memory_system.mcp_server as mcp
from memory_system.models import ConceptCard, ConceptKind, SourceLocation
from memory_system.storage import ConceptStore


def _card(name: str) -> ConceptCard:
    location = SourceLocation(
        file_path="a.py", module="a", qualified_name=name, start_line=1, end_line=2
    )
    return ConceptCard.create(
        name=name,
        kind=ConceptKind.CONCEPT,
        definition=name + "的定义",
        background="",
        background_concepts=(),
        location=location,
        source_excerpt="x = 1\n",
    )


class _PassthroughRetriever:
    """Test stand-in: recall via FTS, keep store order (no model calls)."""

    def search(self, store, query, limit):
        return store.search_any(query, limit=limit)


class ReadinessGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database = str(Path(self._tmp.name) / "concepts.sqlite")
        self._saved = (mcp._store, mcp._cache, mcp._database_path,
                       mcp._project_root, mcp._scan_thread, mcp._retriever,
                       mcp._SCAN_WAIT_TIMEOUT_SECONDS)
        mcp._store = ConceptStore(self.database)
        mcp._store.open()
        self.cards = [_card("语法解析"), _card("结果组织")]
        mcp._store.upsert_cards(self.cards)
        mcp._database_path = self.database
        mcp._retriever = _PassthroughRetriever()
        mcp._scan_thread = None

    def tearDown(self) -> None:
        thread = mcp._scan_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        if mcp._store is not None:
            mcp._store.close()
        (mcp._store, mcp._cache, mcp._database_path,
         mcp._project_root, mcp._scan_thread, mcp._retriever,
         mcp._SCAN_WAIT_TIMEOUT_SECONDS) = self._saved
        self._tmp.cleanup()

    def test_search_waits_until_scan_thread_finishes(self) -> None:
        release = threading.Event()
        scan_done = threading.Event()

        def _linger() -> None:
            release.wait(timeout=10)
            scan_done.set()

        mcp._scan_thread = threading.Thread(target=_linger, daemon=True)
        mcp._scan_thread.start()
        observed: dict = {}

        def _search() -> None:
            payload = mcp.search_concepts("语法解析")
            observed["scan_done_when_answered"] = scan_done.is_set()
            observed["payload"] = payload

        worker = threading.Thread(target=_search)
        worker.start()
        time.sleep(0.3)
        self.assertNotIn("payload", observed)  # gated while the scan lives
        release.set()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(observed["scan_done_when_answered"])
        self.assertEqual(
            [item["card_id"] for item in observed["payload"]["results"]],
            [self.cards[0].id],
        )

    def test_card_fetch_passes_the_same_gate(self) -> None:
        mcp._SCAN_WAIT_TIMEOUT_SECONDS = 0.2
        release = threading.Event()
        mcp._scan_thread = threading.Thread(
            target=release.wait, kwargs={"timeout": 10}, daemon=True
        )
        mcp._scan_thread.start()
        try:
            payload = mcp.search_concepts(card_ids=[self.cards[0].id])
            self.assertIn("still building", payload["error"])
            self.assertNotIn("cards", payload)
        finally:
            release.set()
            mcp._scan_thread.join(timeout=5)
        payload = mcp.search_concepts(card_ids=[self.cards[0].id])
        self.assertEqual([card["id"] for card in payload["cards"]], [self.cards[0].id])

    def test_empty_index_reports_scan_first_error(self) -> None:
        empty_path = str(Path(self._tmp.name) / "empty.sqlite")
        empty = ConceptStore(empty_path)
        empty.open()
        previous_store = mcp._store
        mcp._store = empty
        mcp._database_path = empty_path
        try:
            payload = mcp.search_concepts("任何查询")
            self.assertIn("scan_codebase", payload["error"])
            self.assertNotIn("results", payload)
        finally:
            mcp._store = previous_store
            mcp._database_path = self.database
            empty.close()


if __name__ == "__main__":
    unittest.main()
