"""Tests for the red/green MCP button and search-hit node highlighting."""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.request
from pathlib import Path

import memory_system.mcp_server as mcp
import memory_system.web_server as web
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


def _reset_web_state() -> None:
    with web._connection_lock:
        web._connection["last_seen"] = None
    with web._search_lock:
        web._search_event.update({"seq": 0, "query": "", "direct": [], "related": []})


class ConnectionStateTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_web_state()
        self._saved = (mcp._store, mcp._database_path)
        mcp._store = None
        mcp._database_path = ""

    def tearDown(self) -> None:
        (mcp._store, mcp._database_path) = self._saved
        _reset_web_state()

    def test_starts_disconnected_before_any_agent_call(self) -> None:
        state = web.get_connection_state(now=1000.0)
        self.assertFalse(state["connected"])
        self.assertIsNone(state["idle_seconds"])

    def test_agent_call_turns_green_then_expires(self) -> None:
        web.mark_agent_seen(now=1000.0)
        self.assertTrue(web.get_connection_state(now=1100.0)["connected"])
        self.assertEqual(web.get_connection_state(now=1100.0)["idle_seconds"], 100.0)
        self.assertFalse(
            web.get_connection_state(now=1000.0 + web.AGENT_IDLE_TIMEOUT_SECONDS + 1)["connected"]
        )

    def test_mcp_tools_mark_agent_seen_even_when_uninitialized(self) -> None:
        # the call itself fails, but the agent contact still lights the button
        callers = (
            lambda: mcp.search_concepts("whatever"),
            lambda: mcp.search_concepts(card_ids="whatever"),
        )
        for caller in callers:
            _reset_web_state()
            with self.assertRaises(RuntimeError):
                caller()
            self.assertTrue(web.get_connection_state()["connected"])
        # an invalid call never reaches the store but still lights the button
        _reset_web_state()
        self.assertIn("error", mcp.search_concepts())
        self.assertTrue(web.get_connection_state()["connected"])


class SearchEventTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_web_state()

    def tearDown(self) -> None:
        _reset_web_state()

    def test_record_increments_sequence_and_stores_hits(self) -> None:
        self.assertEqual(web.record_search_event("q", ["a"], ["b"]), 1)
        self.assertEqual(web.record_search_event("q2", ["c"], []), 2)
        event = web.get_search_event(0)
        self.assertTrue(event["fresh"])
        self.assertEqual(event["seq"], 2)
        self.assertEqual(event["direct"], ["c"])
        self.assertEqual(event["related"], [])

    def test_since_filters_stale_events(self) -> None:
        web.record_search_event("q", ["a"], [])
        self.assertFalse(web.get_search_event(1)["fresh"])
        self.assertEqual(web.get_search_event(1), {"seq": 1, "fresh": False})


class ConnectionEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_web_state()
        self._tmp = tempfile.TemporaryDirectory()
        self.server = web.start_web_server(
            str(Path(self._tmp.name) / "concepts.sqlite"), port=8940
        )
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        _reset_web_state()
        self._tmp.cleanup()

    def _get_json(self, path: str) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_connection_endpoint_reflects_state(self) -> None:
        self.assertFalse(self._get_json("/api/connection")["connected"])
        web.mark_agent_seen()
        self.assertTrue(self._get_json("/api/connection")["connected"])

    def test_search_events_endpoint_serves_latest_event(self) -> None:
        self.assertFalse(self._get_json("/api/search-events?since=0")["fresh"])
        web.record_search_event("查询", ["a", "b"], ["c"])
        event = self._get_json("/api/search-events?since=0")
        self.assertTrue(event["fresh"])
        self.assertEqual(event["query"], "查询")
        self.assertEqual(event["direct"], ["a", "b"])
        self.assertEqual(event["related"], ["c"])
        self.assertFalse(self._get_json("/api/search-events?since=1")["fresh"])


class McpWebWiringTests(unittest.TestCase):
    """search_concepts must publish its hits for the page to turn blue."""

    def setUp(self) -> None:
        _reset_web_state()
        self._tmp = tempfile.TemporaryDirectory()
        self.database = str(Path(self._tmp.name) / "concepts.sqlite")
        self._saved = (mcp._store, mcp._cache, mcp._database_path,
                       mcp._project_root, mcp._scan_thread, mcp._retriever)
        self.cards = [_card("语法解析"), _card("结果组织"), _card("日志输出")]
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
        _reset_web_state()
        self._tmp.cleanup()

    def test_search_publishes_hits_and_marks_agent_seen(self) -> None:
        payload = mcp.search_concepts("语法解析")
        hit_ids = [card["card_id"] for card in payload["results"]]
        self.assertEqual(hit_ids, [self.cards[0].id])
        event = web.get_search_event(0)
        self.assertTrue(event["fresh"])
        self.assertEqual(event["direct"], hit_ids)
        self.assertEqual(event["related"], [])
        self.assertTrue(web.get_connection_state()["connected"])

    def test_usage_edges_reach_the_related_payload(self) -> None:
        from memory_system.activation import record_usage

        record_usage(self.database, [self.cards[0].id, self.cards[1].id])
        payload = mcp.search_concepts("语法解析")
        related_ids = [item["card_id"] for item in payload.get("related", [])]
        self.assertIn(self.cards[1].id, related_ids)
        event = web.get_search_event(0)
        self.assertEqual(event["direct"], [self.cards[0].id])
        self.assertEqual(event["related"], related_ids)


if __name__ == "__main__":
    unittest.main()
