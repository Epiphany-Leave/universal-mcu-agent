import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[3]
SERVER_PATH = REPOSITORY / "mcp-server" / "mcu_server.py"


class McpPathBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # mcu_server supplies a no-op registration shim when the optional mcp
        # package is absent, so boundary behavior remains testable either way.
        spec = importlib.util.spec_from_file_location("mcu_server_path_test", SERVER_PATH)
        cls.server = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cls.server)

    def test_parse_ioc_rejects_absolute_and_parent_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ioc = root / "board.ioc"
            ioc.write_text("Mcu.CPN=STM32F103C8T6\n", encoding="utf-8")

            absolute = json.loads(self.server.parse_ioc(str(root), str(ioc)))
            traversal = json.loads(self.server.parse_ioc(str(root), "sub/../board.ioc"))
            self.assertIn("absolute artifact paths are forbidden", absolute["error"])
            self.assertIn("parent traversal is forbidden", traversal["error"])

    def test_parse_ioc_accepts_confined_relative_path_without_mcp_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ioc = root / "board.ioc"
            ioc.write_text("Mcu.CPN=STM32F103C8T6\nMcu.Package=LQFP48\n", encoding="utf-8")
            result = json.loads(self.server.parse_ioc(str(root), "board.ioc"))
            self.assertNotIn("error", result)
            self.assertEqual("STM32F103C8T6", result["mcu"])


if __name__ == "__main__":
    unittest.main()
