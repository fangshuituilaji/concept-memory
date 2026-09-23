from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memory_system.cli import _load_dotenv, main


class CliSmokeTests(unittest.TestCase):
    def test_load_dotenv_adds_values_without_overriding_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dotenv = Path(directory) / ".env"
            dotenv.write_text(
                "FROM_DOTENV=loaded\nQUOTED=\"quoted value\"\nEXISTING=from-file\n",
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"EXISTING": "from-environment"}, clear=False):
                _load_dotenv(dotenv)
                self.assertEqual(os.environ["FROM_DOTENV"], "loaded")
                self.assertEqual(os.environ["QUOTED"], "quoted value")
                self.assertEqual(os.environ["EXISTING"], "from-environment")

    def test_memory_concepts_legacy_entrypoint_still_outputs_cards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.py"
            source.write_text("def greet(name):\n    return name\n", encoding="utf-8")
            with patch("sys.stdout") as stdout:
                exit_code = main([str(source), "--offline"])

            self.assertEqual(exit_code, 0)
            payload = "".join(call.args[0] for call in stdout.write.call_args_list)
            cards = json.loads(payload)
            self.assertTrue(cards)
            self.assertEqual(cards[0]["location"]["file_path"], "sample.py")


if __name__ == "__main__":
    unittest.main()
