from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

from memory_system.cache import CacheKey, ConceptCache
from memory_system.incremental import FileStateRecord, FileStateStore, incremental_scan
from memory_system.storage import ConceptStore
from memory_system.synthesis import ConceptDraft, ConceptSynthesisConfig
import memory_system.incremental as incremental_module
import memory_system.pipeline as pipeline_module


def test_old_file_state_schema_migrates_idempotently(tmp_path):
    database = tmp_path / "old.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE file_state (path TEXT PRIMARY KEY, mtime REAL NOT NULL, "
        "size INTEGER NOT NULL, source_digest TEXT NOT NULL, "
        "scanned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    connection.execute(
        "INSERT INTO file_state(path,mtime,size,source_digest) VALUES ('old.py',1,2,'digest')"
    )
    connection.commit()
    connection.close()

    states = FileStateStore(database)
    assert states.load()["old.py"].analysis_signature is None
    states.commit_scan(
        [FileStateRecord("old.py", 1, 2, "digest", "parser-signature")],
        {"old.py"},
    )
    assert states.load()["old.py"].analysis_signature == "parser-signature"
    assert states.load()["old.py"].analysis_signature == "parser-signature"


def test_parser_and_prompt_signature_changes_reanalyze_unchanged_file(tmp_path):
    class CountingSynthesizer:
        generated_by = "qwen-flash"

        def __init__(self):
            self.calls = []

        def synthesize(self, facts):
            self.calls.append(facts.file.relative_path)
            return [ConceptDraft("重试逻辑", "再次尝试", "错误时重试", evidence=("retry",))]

    root = tmp_path / "project"
    root.mkdir()
    source = root / "sample.py"
    source.write_text("def retry():\n    return True\n", encoding="utf-8")
    database = root / ".concept-memory" / "concepts.sqlite"
    cache_file = tmp_path / "drafts.json"
    store = ConceptStore(str(database))
    store.open()
    synthesizer = CountingSynthesizer()

    def scan(parser_signature, prompt_version):
        config = ConceptSynthesisConfig(prompt_version=prompt_version)
        with patch.object(incremental_module, "parser_signature_for_path", return_value=parser_signature), \
             patch.object(pipeline_module, "parser_signature_for_path", return_value=parser_signature), \
             ConceptCache(cache_file) as cache:
            return incremental_scan(
                root,
                store=store,
                database_path=database,
                cache=cache,
                synthesizer=synthesizer,
                config=config,
                max_workers=1,
            )

    try:
        first = scan("grammar-v1", "prompt-v1")
        assert first.changed_files == ("sample.py",)
        second = scan("grammar-v2", "prompt-v1")
        assert second.changed_files == ("sample.py",)
        third = scan("grammar-v2", "prompt-v2")
        assert third.changed_files == ("sample.py",)
        fourth = scan("grammar-v2", "prompt-v2")
        assert fourth.changed_files == ()
        assert fourth.skipped_files == 1
        assert synthesizer.calls == ["sample.py", "sample.py", "sample.py"]
        assert FileStateStore(database).load()["sample.py"].analysis_signature
    finally:
        store.close()


def test_cache_keys_separate_languages_and_mark_legacy_keys(tmp_path):
    shared = dict(source_digest="same-content", model_name="qwen-flash")
    python_key = CacheKey(**shared, analysis_signature="python:ast-v1")
    typescript_key = CacheKey(**shared, analysis_signature="typescript:grammar-v1")
    assert python_key.digest != typescript_key.digest
    restored = CacheKey.from_dict({
        "source_digest": "old",
        "model_name": "qwen-flash",
        "prompt_version": "concept-synthesis-v1",
    })
    assert restored.analysis_signature == "legacy"
