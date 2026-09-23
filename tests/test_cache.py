from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from memory_system.cache import CacheKey, ConceptCache


class CacheKeyTests(unittest.TestCase):
    def _key(self, **changes: object) -> CacheKey:
        values: dict[str, object] = {
            "source_digest": "digest-1",
            "model_name": "qwen-flash",
            "model_version": "2026-01",
            "prompt_version": "phase1-v1",
            "generation_config": {"temperature": 0, "top_p": 0.8},
        }
        values.update(changes)
        return CacheKey(**values)  # type: ignore[arg-type]

    def test_key_is_stable_and_contains_all_generation_inputs(self) -> None:
        first = self._key(
            generation_config={"top_p": 0.8, "temperature": 0},
        )
        second = self._key(
            generation_config={"temperature": 0, "top_p": 0.8},
        )

        self.assertEqual(first, second)
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(
            set(first.payload()),
            {
                "source_digest",
                "model_name",
                "model_version",
                "prompt_version",
                "generation_config",
            },
        )
        self.assertNotEqual(first, self._key(source_digest="digest-2"))
        self.assertNotEqual(first, self._key(model_name="other-model"))
        self.assertNotEqual(first, self._key(model_version="2026-02"))
        self.assertNotEqual(first, self._key(prompt_version="phase1-v2"))
        self.assertNotEqual(
            first,
            self._key(generation_config={"temperature": 0.2, "top_p": 0.8}),
        )

    def test_key_never_serializes_credential_or_source_fields(self) -> None:
        key = self._key(
            generation_config={
                "temperature": 0,
                "api_key": "do-not-write",
                "source_text": "do-not-write",
            }
        )

        serialized = json.dumps(key.to_dict(), ensure_ascii=False)
        self.assertNotIn("do-not-write", serialized)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("source_text", serialized)


class ConceptCacheTests(unittest.TestCase):
    def test_hit_and_miss_counters(self) -> None:
        key = CacheKey(
            source_digest="digest-1",
            model_name="offline",
            model_version="1",
            prompt_version="phase1-v1",
            generation_config={"temperature": 0},
        )
        with ConceptCache(":memory:") as cache:
            self.assertIsNone(cache.get(key))
            self.assertEqual(cache.misses, 1)
            cache.put(key, [{"name": "AST", "definition": "syntax tree"}])
            self.assertEqual(cache.get(key), [{"name": "AST", "definition": "syntax tree"}])
            self.assertEqual(cache.hits, 1)
            self.assertEqual(cache.stats["hit_rate"], 0.5)

    def test_configuration_change_is_a_miss(self) -> None:
        key = CacheKey(
            source_digest="digest-1",
            model_name="offline",
            model_version="1",
            prompt_version="phase1-v1",
            generation_config={"temperature": 0},
        )
        changed = CacheKey(
            source_digest="digest-1",
            model_name="offline",
            model_version="1",
            prompt_version="phase1-v1",
            generation_config={"temperature": 0.1},
        )
        with ConceptCache(":memory:") as cache:
            cache.put(key, ["old configuration"])
            self.assertIsNone(cache.get(changed))
            self.assertEqual(cache.misses, 1)

    def test_persistence_and_old_versions_survive_active_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "concept-cache.json"
            file_path = Path(directory) / "sample.py"
            first = CacheKey(
                source_digest="digest-1",
                model_name="qwen-flash",
                model_version="2026-01",
                prompt_version="phase1-v1",
                generation_config={"temperature": 0},
                file_path=str(file_path),
            )
            second = CacheKey(
                source_digest="digest-2",
                model_name="qwen-flash",
                model_version="2026-01",
                prompt_version="phase1-v1",
                generation_config={"temperature": 0},
                file_path=str(file_path),
            )
            with ConceptCache(cache_path) as cache:
                cache.put(first, [{"name": "old"}])
                cache.put(second, [{"name": "active"}])

            with ConceptCache(cache_path) as restored:
                self.assertEqual(restored.get(first), [{"name": "old"}])
                self.assertEqual(restored.get(second), [{"name": "active"}])
                self.assertGreaterEqual(restored.hits, 2)

    def test_invalidate_file_deletes_active_version_but_keeps_old_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "concept-cache.json"
            file_path = Path(directory) / "deleted.py"
            old_key = CacheKey(
                source_digest="old-digest",
                model_name="offline",
                model_version="1",
                prompt_version="phase1-v1",
                generation_config={},
                file_path=str(file_path),
            )
            active_key = CacheKey(
                source_digest="active-digest",
                model_name="offline",
                model_version="1",
                prompt_version="phase1-v2",
                generation_config={},
                file_path=str(file_path),
            )
            with ConceptCache(cache_path) as cache:
                cache.put(old_key, [{"name": "old"}])
                cache.put(active_key, [{"name": "active"}])
                self.assertTrue(cache.invalidate_file(file_path))
                self.assertIsNone(cache.get(active_key))
                self.assertEqual(cache.get(old_key), [{"name": "old"}])
                self.assertFalse(cache.invalidate_file(file_path))

            with ConceptCache(cache_path) as restored:
                self.assertIsNone(restored.get(active_key))
                self.assertEqual(restored.get(old_key), [{"name": "old"}])

    def test_cached_result_drops_source_and_api_key_fields(self) -> None:
        key = CacheKey(
            source_digest="digest-1",
            model_name="offline",
            model_version="1",
            prompt_version="phase1-v1",
            generation_config={},
        )
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "concept-cache.json"
            with ConceptCache(cache_path) as cache:
                cache.put(
                    key,
                    {
                        "name": "AST",
                        "definition": "derived concept",
                        "source_excerpt": "print('private source')",
                        "api_key": "private-api-key",
                    },
                )
            raw_cache = cache_path.read_text(encoding="utf-8")
            self.assertNotIn("private source", raw_cache)
            self.assertNotIn("private-api-key", raw_cache)
            with ConceptCache(cache_path) as cache:
                self.assertEqual(cache.get(key), {"name": "AST", "definition": "derived concept"})


if __name__ == "__main__":
    unittest.main()
