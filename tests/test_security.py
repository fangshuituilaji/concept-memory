from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from memory_system.security import (
    JsonlAuditRecorder,
    SecurityBoundaryError,
    SecurityPolicy,
    SourceSendingPolicy,
)


class SecurityPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
        (self.root / ".env").write_text("TOKEN=not-for-network\n", encoding="utf-8")
        (self.root / "cert.pem").write_text("certificate\n", encoding="utf-8")
        (self.root / "private.key").write_text("private key\n", encoding="utf-8")
        (self.root / "chain.crt").write_text("certificate chain\n", encoding="utf-8")
        (self.root / "credentials").mkdir()
        (self.root / "credentials" / "service.txt").write_text("credential\n", encoding="utf-8")
        (self.root / "config").mkdir()
        (self.root / "config" / "settings.json").write_text("{}\n", encoding="utf-8")
        (self.root / "passwords.txt").write_text("password\n", encoding="utf-8")
        (self.root / "tokens.txt").write_text("token\n", encoding="utf-8")
        self.policy = SecurityPolicy(self.root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_ordinary_source_is_allowed_online_by_default(self) -> None:
        decision = self.policy.can_send(self.root / "src" / "app.py")

        self.assertTrue(decision)
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.can_send)
        self.assertEqual(decision.reason, "ordinary_source")
        self.assertFalse(decision.sensitive)

    def test_common_sensitive_paths_are_denied_online(self) -> None:
        cases = (
            self.root / ".env",
            self.root / "cert.pem",
            self.root / "private.key",
            self.root / "chain.crt",
            self.root / "credentials" / "service.txt",
            self.root / "config" / "settings.json",
            self.root / "passwords.txt",
            self.root / "tokens.txt",
        )

        for path in cases:
            with self.subTest(path=path):
                decision = self.policy.can_send(path)
                self.assertFalse(decision.allowed)
                self.assertTrue(decision.sensitive)
                self.assertIn("allowlist", decision.reason)

    def test_sensitive_allowlist_explicitly_allows_file_and_directory(self) -> None:
        file_policy = SecurityPolicy(self.root, allowlist=[".env"])
        directory_policy = SecurityPolicy(self.root, allowlist=["credentials"])

        file_decision = file_policy.can_send(self.root / ".env")
        directory_decision = directory_policy.can_send(
            self.root / "credentials" / "service.txt"
        )

        self.assertTrue(file_decision.allowed)
        self.assertEqual(file_decision.reason, "explicit_allowlist")
        self.assertTrue(file_decision.allowlisted)
        self.assertTrue(directory_decision.allowed)
        self.assertTrue(directory_decision.allowlisted)

    def test_user_declared_sensitive_path_is_denied_until_allowlisted(self) -> None:
        custom = self.root / "internal_notes.txt"
        custom.write_text("private local notes\n", encoding="utf-8")
        policy = SecurityPolicy(self.root, sensitive_paths=[custom])

        denied = policy.can_send(custom)
        allowed = SecurityPolicy(self.root, sensitive_paths=[custom], allowlist=[custom]).can_send(
            custom
        )

        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "user_sensitive_path_requires_allowlist")
        self.assertTrue(allowed.allowed)

    def test_offline_mode_keeps_local_read_available_but_never_sends(self) -> None:
        policy = SecurityPolicy(self.root, source_sending_policy=SourceSendingPolicy.OFFLINE)

        local = policy.can_read(self.root / ".env")
        send = policy.can_send(self.root / ".env")

        self.assertTrue(local.allowed)
        self.assertTrue(local.can_read)
        self.assertEqual(local.reason, "offline_local_read")
        self.assertFalse(send.allowed)
        self.assertFalse(send.can_send)
        self.assertEqual(send.reason, "offline_mode")

    def test_realpath_normalization_and_symlink_escape_are_explicit(self) -> None:
        outside = Path(self.temp_dir.name).parent / f"outside-{self.root.name}"
        outside.mkdir()
        try:
            target = outside / "secret.py"
            target.write_text("print('outside')\n", encoding="utf-8")
            link = self.root / "src" / "linked.py"
            try:
                link.symlink_to(target)
            except OSError:
                # Windows without developer mode / admin rights cannot create symlinks.
                self.skipTest("symlink creation not permitted on this platform")

            self.assertEqual(self.policy.normalize_realpath(link), target.resolve())
            self.assertTrue(self.policy.is_symlink_escape(link))
            self.assertFalse(self.policy.is_path_within_root(link))
            decision = self.policy.can_send(link)
            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason, "symlink_escape")
            self.assertTrue(decision.symlink_escape)
            with self.assertRaises(SecurityBoundaryError):
                self.policy.ensure_within_root(link)
        finally:
            target.unlink(missing_ok=True)
            outside.rmdir()

    def test_parent_traversal_is_rejected_even_without_symlink(self) -> None:
        outside = self.root.parent / "not-inside-memory-system-security.txt"
        outside.write_text("outside\n", encoding="utf-8")
        try:
            decision = self.policy.can_read(self.root / "src" / ".." / ".." / outside.name)
            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason, "path_outside_root")
            self.assertFalse(decision.symlink_escape)
        finally:
            outside.unlink(missing_ok=True)

    def test_missing_path_has_a_reason(self) -> None:
        decision = self.policy.can_read(self.root / "src" / "missing.py")

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "path_not_found")


class JsonlAuditRecorderTests(unittest.TestCase):
    def test_records_only_operational_metadata_and_omits_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            recorder = JsonlAuditRecorder(audit_path, run_id="run-123")
            policy = SecurityPolicy(directory)
            source = Path(directory) / "app.py"
            source.write_text("API_KEY = 'source must not be logged'\n", encoding="utf-8")
            decision = policy.can_send(source)
            payload = recorder.record(
                files=[source],
                decision=decision,
                model="qwen-flash",
                config={
                    "temperature": 0,
                    "api_key": "super-secret-api-key",
                    "prompt": "source and secret prompt text",
                    "generation_config": {"max_tokens": 100},
                },
            )

            lines = audit_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            restored = json.loads(lines[0])
            self.assertEqual(restored, payload)
            self.assertEqual(restored["run_id"], "run-123")
            self.assertEqual(restored["model"], "qwen-flash")
            self.assertEqual(restored["files"], [str(source)])
            self.assertEqual(restored["decision"], "allow")
            self.assertNotIn("super-secret-api-key", lines[0])
            self.assertNotIn("source and secret prompt text", lines[0])
            self.assertNotIn("source must not be logged", lines[0])
            self.assertEqual(restored["config"], {"temperature": 0, "generation_config": {"max_tokens": 100}})

    def test_recorder_can_record_multiple_decisions_without_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            recorder = JsonlAuditRecorder(audit_path, run_id="run-456")
            policy = SecurityPolicy(directory)
            normal = Path(directory) / "main.py"
            sensitive = Path(directory) / ".env"
            normal.write_text("pass\n", encoding="utf-8")
            sensitive.write_text("TOKEN=x\n", encoding="utf-8")
            payload = recorder.record(
                decisions=[policy.can_send(normal), policy.can_send(sensitive)],
                model="offline-test-model",
                config={"source_text": "must not appear", "target_concepts": 3},
            )

            restored = json.loads(audit_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["decision"], "deny")
            self.assertEqual(restored["decision"], "deny")
            self.assertEqual(
                restored["files"],
                [str(policy.normalize_realpath(normal)), str(policy.normalize_realpath(sensitive))],
            )
            self.assertEqual(restored["reasons"], ["ordinary_source", "sensitive_path_requires_allowlist"])
            self.assertEqual(restored["config"], {"target_concepts": 3})
            self.assertNotIn("must not appear", audit_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
