from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from memory_system import mcp_server
from memory_system.extractor import TreeSitterSourceAnalyzer
from memory_system.incremental import FileStateStore, incremental_scan
from memory_system.models import ConceptCard, ConceptKind, SourceLocation
from memory_system.readers import CodeFile
from memory_system.security import SecurityPolicy
from memory_system.storage import ConceptStore
from memory_system.synthesis import ConceptDraft, ConceptSynthesisConfig
from memory_system.parsers.base import SourceParseError


def test_parser_error_reports_language_path_and_one_based_line():
    path = Path("src/Broken.ts")
    code_file = CodeFile(
        path=path,
        relative_path=path.as_posix(),
        text="function valid() {}\nfunction broken( {\n",
    )
    with pytest.raises(SourceParseError, match=r"src/Broken\.ts as typescript: line 2"):
        TreeSitterSourceAnalyzer().analyze(code_file)


def test_failed_multilanguage_rescan_keeps_previous_cards_state_and_edges(tmp_path):
    class ScriptedSynthesizer:
        generated_by = "qwen-flash"
        config = ConceptSynthesisConfig()

        def synthesize(self, facts):
            return [ConceptDraft("原有概念", "定义", "背景", evidence=("keep",))]

    root = tmp_path / "project"
    root.mkdir()
    (root / "legacy.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    database = root / ".concept-memory" / "concepts.sqlite"
    store = ConceptStore(str(database))
    store.open()
    try:
        first = incremental_scan(
            root, store=store, database_path=database, synthesizer=ScriptedSynthesizer()
        )
        previous_cards = store.all_cards()
        previous_ids = {card.id for card in previous_cards}
        previous_signature = FileStateStore(database).load()["legacy.py"].analysis_signature
        assert previous_signature
        assert {card.location.file_path for card in first.cards} == {"legacy.py"}

        (root / "Broken.ts").write_text("function broken( {\n", encoding="utf-8")
        with pytest.raises(SourceParseError):
            incremental_scan(
                root, store=store, database_path=database, synthesizer=ScriptedSynthesizer()
            )

        assert {card.id for card in store.all_cards()} == previous_ids
        states = FileStateStore(database).load()
        assert set(states) == {"legacy.py"}
        assert states["legacy.py"].analysis_signature == previous_signature
        assert "Broken.ts" not in store.stored_file_paths()
    finally:
        store.close()


def test_scan_worker_publishes_error_instead_of_done_on_failure():
    with patch.object(mcp_server, "_store", object()), \
         patch.object(mcp_server, "_cache", object()), \
         patch.object(mcp_server, "_database_path", "memory.sqlite"), \
         patch.object(mcp_server, "incremental_scan", side_effect=ValueError("parse failed")), \
         patch.object(mcp_server, "set_init_state") as set_state:
        mcp_server._scan_worker("/temporary/project")

    assert set_state.call_args.args[0] == "error"
