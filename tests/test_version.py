from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

import memory_system

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
INIT_PATH = REPO_ROOT / "src" / "memory_system" / "__init__.py"


class VersionConsistencyTests(unittest.TestCase):
    """版本号口径：pyproject.toml 是唯一版本源，源码与 MCP 握手都必须与它一致。"""

    def test_package_version_matches_pyproject(self) -> None:
        data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(memory_system.__version__, data["project"]["version"])

    def test_version_declared_as_literal_in_source(self) -> None:
        """离线包靠 PYTHONPATH 加载源码，读不到安装元数据，所以必须是字面量。"""

        text = INIT_PATH.read_text(encoding="utf-8")
        self.assertIn('__version__ = "%s"' % memory_system.__version__, text)

    def test_version_looks_like_semver(self) -> None:
        parts = memory_system.__version__.split(".")
        self.assertEqual(len(parts), 3, memory_system.__version__)
        self.assertTrue(all(part.isdigit() for part in parts), memory_system.__version__)

    def test_mcp_server_reports_the_same_version(self) -> None:
        try:
            import mcp.server.lowlevel  # noqa: F401
        except ImportError:
            self.skipTest("mcp 未安装，跳过 MCP 版本一致性检查")

        from memory_system.mcp_server import build_server

        options = build_server().create_initialization_options()
        self.assertEqual(options.server_name, "concept-memory")
        self.assertEqual(options.server_version, memory_system.__version__)


if __name__ == "__main__":
    unittest.main()
