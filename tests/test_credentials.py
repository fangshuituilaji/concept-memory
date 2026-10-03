"""First-run credential setup in isolated user profiles and real HTTP/MCP flows."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from memory_system import credentials as creds
from memory_system import mcp_server as mcp
from memory_system import web_server as web


TEST_KEY = "sk-test-credential-never-a-real-key"


class IsolatedProfile:
    def setUp(self):
        self.profile = tempfile.TemporaryDirectory()
        self.addCleanup(self.profile.cleanup)
        environment = dict(os.environ)
        environment.pop(creds.API_KEY_ENV, None)
        environment["CONCEPT_MEMORY_CONFIG_DIR"] = self.profile.name
        self.env = patch.dict(os.environ, environment, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)


class CredentialTests(IsolatedProfile, unittest.TestCase):
    def test_empty_profile_does_not_read_project_dotenv(self):
        Path(self.profile.name, ".env").write_text("DASHSCOPE_API_KEY=sk-hidden", encoding="utf-8")
        self.assertEqual(creds.get_api_key(), "")
        probe = subprocess.run(
            [sys.executable, "-c", "from memory_system import web_server; "
             "from memory_system.credentials import get_api_key; assert get_api_key() == ''"],
            cwd=self.profile.name, env=dict(os.environ), capture_output=True, timeout=15,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr.decode())

    def test_saved_key_is_available_after_process_restart(self):
        creds.save_api_key(TEST_KEY)
        self.assertEqual(creds.get_api_key(), TEST_KEY)
        probe = subprocess.run(
            [sys.executable, "-c", "from memory_system.credentials import get_api_key; "
             "assert get_api_key() == 'sk-test-credential-never-a-real-key'"],
            env=dict(os.environ), capture_output=True, timeout=15,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr.decode())
        if os.name == "nt":
            self.assertNotIn(TEST_KEY, creds.credentials_path().read_text())
        else:
            self.assertEqual(creds.credentials_path().stat().st_mode & 0o777, 0o600)

    def test_environment_has_priority_and_custom_names_do_not_use_saved_key(self):
        creds.save_api_key(TEST_KEY)
        with patch.dict(os.environ, {creds.API_KEY_ENV: "sk-environment"}):
            self.assertEqual(creds.get_api_key(), "sk-environment")
        self.assertEqual(creds.get_api_key("CONCEPT_TEST_CUSTOM_KEY"), "")

    def test_corrupt_credential_file_returns_unconfigured(self):
        for contents in ["{", "null", "[]", '{}', '{"storage":"wrong","value":"eA=="}']:
            with self.subTest(contents=contents):
                creds.credentials_path().write_text(contents, encoding="utf-8")
                self.assertEqual(creds.get_api_key(), "")

    def test_malformed_input_is_rejected(self):
        for value in [None, 123, "", "a b", "a\nb", "a" * 513]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                creds.validate_api_key(value)

    def test_provider_errors_never_expose_the_submitted_key(self):
        with patch("dashscope.Generation.call", side_effect=RuntimeError(TEST_KEY)):
            with self.assertRaises(ValueError) as result:
                creds.verify_api_key(TEST_KEY)
        self.assertNotIn(TEST_KEY, str(result.exception))
        self.assertFalse(creds.credentials_path().exists())


class CredentialHTTPTests(IsolatedProfile, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.project = tempfile.TemporaryDirectory()
        self.addCleanup(self.project.cleanup)
        self.saved_globals = {name: getattr(mcp, name) for name in
                              ("_store", "_cache", "_project_root", "_database_path",
                               "_web_server", "_web_url", "_scan_thread", "_retriever")}
        self.saved_state = web.get_init_state()
        for name in self.saved_globals:
            setattr(mcp, name, None if name not in ("_project_root", "_database_path", "_web_url") else "")
        web.set_init_state("idle")
        self.addCleanup(self.restore)
        self.initial = mcp.scan_codebase(self.project.name, open_browser=False)
        self.url = self.initial["url"]

    def restore(self):
        if mcp._scan_thread:
            mcp._scan_thread.join(timeout=5)
        if mcp._web_server:
            mcp._web_server.shutdown()
            mcp._web_server.server_close()
        if mcp._store:
            mcp._store.close()
        for name, value in self.saved_globals.items():
            setattr(mcp, name, value)
        with web._init_lock:
            web._init_state.clear()
            web._init_state.update(self.saved_state)

    def request(self, path, payload=None, **headers):
        data = None if payload is None else json.dumps(payload).encode()
        request = Request(self.url + path, data=data, headers={"Content-Type": "application/json", **headers})
        try:
            response = urlopen(request, timeout=20)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.read().decode()

    def test_no_key_does_not_scan_and_both_search_modes_explain_setup(self):
        self.assertEqual(self.initial["status"], "needs_api_key")
        self.assertIsNone(mcp._scan_thread)
        self.assertEqual(mcp._store.all_cards(), [])
        for arguments in [{"query": "hello"}, {"card_ids": ["missing"]}]:
            self.assertIn(self.url, mcp.search_concepts(**arguments)["error"])
        self.assertEqual(json.loads(self.request("/api/credential-status")[1]), {"configured": False})

    def test_invalid_key_can_be_retried_without_starting_scan(self):
        with patch("webbrowser.open"), patch("dashscope.Generation.call", return_value=SimpleNamespace(status_code=401)):
            status, body = self.request("/api/api-key", {"api_key": TEST_KEY})
        self.assertEqual(status, 400)
        self.assertNotIn(TEST_KEY, body)
        self.assertFalse(creds.credentials_path().exists())
        self.assertIsNone(mcp._scan_thread)
        self.assertEqual(web.get_init_state()["phase"], "needs_api_key")

    def test_save_success_starts_shared_scan_and_returns_only_status(self):
        result = SimpleNamespace(cards=[], pruned_cards=0, changed_files=[], skipped_files=0)
        with patch("dashscope.Generation.call", return_value=SimpleNamespace(status_code=200)), \
             patch.object(mcp, "incremental_scan", return_value=result) as scan:
            status, body = self.request("/api/api-key", {"api_key": TEST_KEY})
            self.assertEqual(status, 200, body)
            mcp._scan_thread.join(timeout=5)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "scanning"})
        scan.assert_called_once()
        self.assertEqual(web.get_init_state()["phase"], "done")
        self.assertEqual(creds.get_api_key(), TEST_KEY)
        for endpoint in ["/api/credential-status", "/api/init-state", "/api/concepts"]:
            self.assertNotIn(TEST_KEY, self.request(endpoint)[1])

    def test_other_websites_cannot_submit_keys(self):
        with patch("dashscope.Generation.call") as provider:
            status, _ = self.request("/api/api-key", {"api_key": TEST_KEY}, Origin="https://example.com")
        self.assertEqual(status, 403)
        provider.assert_not_called()
        self.assertFalse(creds.credentials_path().exists())

    def test_blank_key_is_rejected_before_provider_request(self):
        with patch("dashscope.Generation.call") as provider:
            status, _ = self.request("/api/api-key", {"api_key": " "})
        self.assertEqual(status, 400)
        provider.assert_not_called()

    def test_configured_key_starts_without_prompt(self):
        creds.save_api_key(TEST_KEY)
        result = SimpleNamespace(cards=[], pruned_cards=0, changed_files=[], skipped_files=0)
        with patch.object(mcp, "incremental_scan", return_value=result):
            response = mcp.scan_codebase(self.project.name, open_browser=False)
            mcp._scan_thread.join(timeout=5)
        self.assertEqual(response["status"], "scanning")

    def test_second_server_uses_a_different_port(self):
        second = web.start_web_server(mcp._database_path, port=mcp._web_server.server_address[1])
        try:
            self.assertNotEqual(second.server_address[1], mcp._web_server.server_address[1])
        finally:
            second.shutdown()
            second.server_close()


class ModuleEntrySetupTests(unittest.TestCase):
    def test_stdio_module_entry_continues_after_web_key_submission(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        # Run the actual -m entry with only the external provider and scan work
        # replaced, preserving HTTP, stdio, module identity, storage and gates.
        bootstrap = """
import runpy
from types import SimpleNamespace
import dashscope
import memory_system.incremental as incremental
import webbrowser
webbrowser.open = lambda *a, **k: False
dashscope.Generation.call = lambda **k: SimpleNamespace(status_code=200)
incremental.incremental_scan = lambda *a, **k: SimpleNamespace(cards=[], pruned_cards=0, changed_files=[], skipped_files=0)
runpy.run_module('memory_system.mcp_server', run_name='__main__', alter_sys=True)
"""
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary, "project")
            project.mkdir()
            environment = dict(os.environ)
            environment.pop(creds.API_KEY_ENV, None)
            environment["CONCEPT_MEMORY_CONFIG_DIR"] = str(Path(temporary, "profile"))

            async def check():
                parameters = StdioServerParameters(command=sys.executable, args=["-c", bootstrap], env=environment)
                async with stdio_client(parameters) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        initial = await session.call_tool("scan_codebase", {"path": str(project)})
                        setup = json.loads(initial.content[0].text)
                        self.assertEqual(setup["status"], "needs_api_key")
                        def submit():
                            request = Request(setup["url"] + "/api/api-key", data=json.dumps({"api_key": TEST_KEY}).encode(), headers={"Content-Type": "application/json"})
                            with urlopen(request, timeout=15) as response:
                                self.assertEqual(response.status, 200)
                        await asyncio.to_thread(submit)
                        response = await session.call_tool("search_concepts", {"query": "hello"})
                        payload = json.loads(response.content[0].text)
                        self.assertIn("index is empty", payload["error"])
                        self.assertNotIn("setup is required", payload["error"])

            asyncio.run(asyncio.wait_for(check(), timeout=30))


if __name__ == "__main__":
    unittest.main()
