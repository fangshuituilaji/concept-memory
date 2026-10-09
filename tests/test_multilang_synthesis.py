from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from memory_system.extractor import TreeSitterSourceAnalyzer
from memory_system.pipeline import analyze_path
from memory_system.readers import CodeFile
from memory_system.synthesis import ConceptSynthesisConfig


SAMPLES = (
    ("sample.py", "python", "def greet(name):\n    return name\n"),
    ("notes.md", "markdown", "# Notes\nBody\n"),
    ("sample.ts", "typescript", "function greet(name: string) { return name; }\n"),
    ("sample.tsx", "tsx", "const App = () => <main />;\n"),
    ("sample.js", "javascript", "function greet(name) { return name; }\n"),
    ("sample.jsx", "javascript", "export default function App() { return <main />; }\n"),
    ("Sample.java", "java", "class Sample { void run() {} }\n"),
    ("sample.go", "go", "package sample\nfunc run() {}\n"),
    ("sample.rs", "rust", "fn run() {}\n"),
    ("sample.c", "c", "int run(void) { return 0; }\n"),
    ("sample.cpp", "cpp", "namespace sample { int run() { return 0; } }\n"),
    ("Sample.cs", "csharp", "class Sample { void Run() {} }\n"),
)


def test_source_facts_use_the_real_language_fence_for_every_enabled_parser():
    analyzer = TreeSitterSourceAnalyzer()
    for filename, language_id, source in SAMPLES:
        code_file = CodeFile(
            path=Path(filename), relative_path=filename, text=source
        )
        facts = analyzer.analyze(code_file)
        prompt = facts.to_prompt_text()
        assert facts.language_id == language_id
        assert f"```{language_id if language_id != 'javascript' else 'javascript'}" in prompt
        if language_id not in {"python", "markdown"}:
            assert "```python" not in prompt


def test_qwen_receives_tsx_source_and_cards_record_its_language():
    response = SimpleNamespace(
        output=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {"concepts": [{
                                "name": "页面组件",
                                "definition": "渲染主页面",
                                "background": "App 组件返回页面结构。",
                                "evidence": ["App"],
                            }]},
                            ensure_ascii=False,
                        )
                    )
                )
            ]
        )
    )
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "App.tsx"
        source.write_text("export const App = () => <main />;\n", encoding="utf-8")
        with patch("memory_system.credentials.get_api_key", return_value="unit-test-key"), \
             patch("dashscope.Generation.call", return_value=response) as call:
                cards = analyze_path(
                    source,
                    config=ConceptSynthesisConfig(),
                    extensions=frozenset({".tsx"}),
                )

    assert len(cards) == 1
    assert cards[0].generated_by == "qwen-flash"
    assert cards[0].metadata["language_id"] == "tsx"
    assert cards[0].metadata["validation_status"] == "validated"
    assert call.call_args.kwargs["model"] == "qwen-flash"
    prompt = call.call_args.kwargs["messages"][1]["content"]
    assert "```tsx" in prompt
    assert "```python" not in prompt


def test_multilanguage_prompt_version_invalidates_old_synthesis_drafts():
    assert ConceptSynthesisConfig().prompt_version == "concept-synthesis-v2-multilang"
