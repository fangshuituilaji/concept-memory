from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from memory_system import mcp_server
from memory_system.retrieval import QwenFlashRetriever


FILES = {
    "mixed/python_entry.py": ("def python_entry():\n    return 1\n", "python_entry", "Python入口"),
    "mixed/notes.md": ("# Overview\nMemory notes.\n", "mixed.notes · Overview", "文档索引"),
    "mixed/api.ts": ("export function ts_entry() { return 1; }\n", "ts_entry", "TS入口"),
    "mixed/App.tsx": ("export const TsxApp = () => <main />;\n", "TsxApp", "TSX组件"),
    "mixed/api.js": ("export function js_entry() { return 1; }\n", "js_entry", "JS入口"),
    "mixed/App.jsx": ("export default function JsxApp() { return <main />; }\n", "JsxApp", "JSX组件"),
    "mixed/Store.java": ("class JavaStore { void read() {} }\n", "JavaStore", "Java类"),
    "mixed/main.go": ("package demo\nfunc go_entry() {}\n", "demo.go_entry", "Go入口"),
    "mixed/lib.rs": ("pub fn rust_entry() {}\n", "rust_entry", "Rust入口"),
    "mixed/native.c": ("int c_entry(void) { return 1; }\n", "c_entry", "C入口"),
    "mixed/header.h": (
        "template<class T> class HeaderStore { public: T get(); };\n",
        "HeaderStore", "头文件类型",
    ),
    "mixed/engine.cpp": ("namespace engine { class CppStore {}; }\n", "engine::CppStore", "C++类型"),
    "mixed/Store.cs": ("namespace Demo; public class CsStore { public int Run() => 1; }\n", "Demo.CsStore", "CSharp类型"),
}


def _response(content: str):
    return SimpleNamespace(
        output=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )
    )


class MultiLanguageMcpTests(unittest.TestCase):
    def test_mixed_project_generates_searches_fetches_and_records_real_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            (root / "mixed").mkdir(parents=True)
            for relative, (source, _, _) in FILES.items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding="utf-8")

            retrieval_ids: list[str] = []
            generation_calls = []
            retrieval_calls = []

            def fake_generation_call(**kwargs):
                messages = kwargs["messages"]
                system = messages[0]["content"]
                if "代码库概念编码器" in system:
                    user = messages[1]["content"]
                    relative = user.splitlines()[0].removeprefix("文件: ")
                    evidence, name = FILES[relative][1:]
                    generation_calls.append((kwargs["model"], relative, user))
                    return _response(json.dumps({
                        "concepts": [{
                            "name": name,
                            "definition": f"{name}的语义职责",
                            "background": f"{name}依据对应源码实现。",
                            "evidence": [evidence],
                        }]
                    }, ensure_ascii=False))
                retrieval_calls.append(kwargs["messages"][1]["content"])
                return _response(json.dumps({"ids": retrieval_ids[:1]}))

            fake_web_server = SimpleNamespace(
                server_address=("127.0.0.1", 8765), database_path=None, scan_root=None
            )
            with patch.object(mcp_server, "_scan_thread", None), \
                 patch.object(mcp_server, "_web_server", None), \
                 patch.object(mcp_server, "_web_url", ""), \
                 patch.object(mcp_server, "_store", None), \
                 patch.object(mcp_server, "_cache", None), \
                 patch.object(mcp_server, "_database_path", None), \
                 patch.object(mcp_server, "_project_root", None), \
                 patch.object(mcp_server, "get_api_key", return_value="unit-test-key"), \
                 patch("memory_system.credentials.get_api_key", return_value="unit-test-key"), \
                 patch.object(mcp_server, "start_web_server", return_value=fake_web_server), \
                 patch("dashscope.Generation.call", side_effect=fake_generation_call) as provider:
                try:
                    started = mcp_server.scan_codebase(
                        str(root), open_browser=False, from_agent=False
                    )
                    self.assertEqual(started["status"], "scanning")
                    mcp_server._wait_for_scan()
                    state = mcp_server.get_init_state()
                    self.assertEqual(state["phase"], "done")

                    store = mcp_server._get_store()
                    cards = store.all_cards()
                    self.assertEqual(len(cards), len(FILES))
                    by_file = {card.location.file_path: card for card in cards}
                    for relative, (source, evidence, _) in FILES.items():
                        card = by_file[relative]
                        self.assertEqual(card.generated_by, "qwen-flash")
                        self.assertEqual(card.metadata["validation_status"], "validated")
                        self.assertTrue(card.source_excerpt)
                        self.assertIn(evidence, card.metadata["evidence_symbols"])
                    self.assertEqual(len(generation_calls), len(FILES))
                    self.assertTrue(all(call[0] == "qwen-flash" for call in generation_calls))
                    self.assertIn("```tsx", next(c[2] for c in generation_calls if c[1] == "mixed/App.tsx"))
                    self.assertIn("```java", next(c[2] for c in generation_calls if c[1] == "mixed/Store.java"))

                    first, second = by_file["mixed/App.tsx"], by_file["mixed/Store.java"]
                    def usage_edges():
                        database = sqlite3.connect(mcp_server._database_path)
                        try:
                            return database.execute(
                                "SELECT source_id,target_id,count FROM concept_usage_edges"
                            ).fetchall()
                        except sqlite3.OperationalError:
                            return []
                        finally:
                            database.close()

                    self.assertEqual(usage_edges(), [])
                    retrieval_ids[:] = [first.id]
                    with patch.object(mcp_server, "_get_retriever", return_value=QwenFlashRetriever()):
                        query = mcp_server.search_concepts(
                            "semantic query with no literal file names", limit=1
                        )
                        self.assertEqual([item["card_id"] for item in query["results"]], [first.id])
                        self.assertNotIn("related", query)
                        self.assertEqual(usage_edges(), [])
                        fetched = mcp_server.search_concepts(card_ids=[first.id, second.id])

                    self.assertEqual(len(retrieval_calls), 1)
                    self.assertEqual(provider.call_args_list[-1].kwargs["model"], "qwen-flash")
                    self.assertTrue(all(card.id in retrieval_calls[0] for card in cards))
                    self.assertEqual([card["id"] for card in fetched["cards"]], [first.id, second.id])
                    self.assertIn("export const TsxApp", next(c["source_excerpt"] for c in fetched["cards"] if c["id"] == first.id))
                    self.assertIn("class JavaStore", next(c["source_excerpt"] for c in fetched["cards"] if c["id"] == second.id))
                    rows = usage_edges()
                    self.assertEqual(rows, [(*sorted((first.id, second.id)), 1)])

                finally:
                    if mcp_server._store is not None:
                        mcp_server._store.close()


if __name__ == "__main__":
    unittest.main()
