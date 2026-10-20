import os
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(TOOLS))

from parsers.enet_parser import EnetParser
from parsers.kicad_parser import KiCadNetlistParser, parse_sexpression
from parsers.netlist_parser import detect_netlist_format, get_parser
from parsers.path_security import ProductPathError, resolve_product_path


class KiCadParserTests(unittest.TestCase):
    def test_nested_multi_net_multi_node_fixture(self):
        parser = KiCadNetlistParser()
        data = parser.parse(FIXTURES / "kicad_multi.net")

        self.assertEqual({"UART_TX", "LED_STATUS"}, set(data["nets"]))
        self.assertEqual(2, len(data["nets"]["UART_TX"]))
        self.assertEqual(2, len(data["nets"]["LED_STATUS"]))
        self.assertEqual("UART_TX", data["components"]["U1"]["pins"]["10"]["net"])
        self.assertEqual("PA9/USART1_TX", data["components"]["U1"]["pins"]["10"]["name"])
        self.assertEqual("STM32F103C8T6", parser.find_mcu(data)["device"])
        self.assertTrue(any(item["net"] == "UART_TX" for item in parser.extract_interfaces(data)))

    def test_tokenizer_preserves_nested_and_escaped_strings(self):
        expression = parse_sexpression('(root (child "a \\"quoted\\" value") (nested (x 1)))')
        self.assertEqual("root", expression[0])
        self.assertEqual('a "quoted" value', expression[1][1])
        self.assertEqual(["nested", ["x", "1"]], expression[2])

    def test_empty_connectivity_is_an_explicit_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "empty.net"
            path.write_text(
                '(export (components (comp (ref "U1") (value "STM32F103"))) (nets (net (code "1") (name "EMPTY"))))',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "nets but no connected nodes"):
                KiCadNetlistParser().parse(path)


class EnetParserTests(unittest.TestCase):
    def test_interfaces_use_normalized_components(self):
        parser = EnetParser()
        data = parser.parse(FIXTURES / "connected.enet")

        self.assertEqual("J1", data["components"]["J1"]["designator"])
        self.assertEqual({"U1", "J1"}, {node["component"] for node in data["nets"]["DEBUG_TX"]})
        interfaces = parser.extract_interfaces(data)
        debug = next(item for item in interfaces if item["net"] == "DEBUG_TX")
        self.assertEqual("UART", debug["type"])
        self.assertEqual("调试串口发送", debug["usage"])


class FormatDetectionTests(unittest.TestCase):
    def test_all_public_formats_are_detected_and_registered(self):
        expected = {
            "connected.enet": "enet",
            "kicad_multi.net": "kicad",
            "eagle.sch": "eagle",
            "cadence.xml": "cadence",
            "cadence.txt": "cadence",
            "altium.NET": "altium",
        }
        for name, format_type in expected.items():
            with self.subTest(name=name):
                detected = detect_netlist_format(FIXTURES / name)
                self.assertEqual(format_type, detected)
                parser = get_parser(detected)
                self.assertIsNotNone(parser)
                parsed = parser.parse(FIXTURES / name)
                self.assertTrue(parsed["components"])
                self.assertTrue(parsed["nets"])

        # OrCAD is an explicit alias of the Protel parser even though content
        # detection cannot reliably distinguish it from Altium.
        self.assertIsNotNone(get_parser("orcad"))

    def test_unknown_input_has_diagnostic(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "board.bin"
            path.write_text("not an EDA netlist", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unable to detect EDA format.*extension"):
                detect_netlist_format(path)


class ProductPathTests(unittest.TestCase):
    def test_valid_relative_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "Hardware" / "board.net"
            artifact.parent.mkdir()
            artifact.write_text("fixture", encoding="utf-8")
            self.assertEqual(artifact.resolve(), resolve_product_path(root, "Hardware/board.net", kind="file"))

    def test_absolute_and_parent_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inside = root / "inside.txt"
            inside.write_text("fixture", encoding="utf-8")
            with self.assertRaisesRegex(ProductPathError, "absolute"):
                resolve_product_path(root, str(inside))
            with self.assertRaisesRegex(ProductPathError, "parent traversal"):
                resolve_product_path(root, "sub/../inside.txt")
            with self.assertRaisesRegex(ProductPathError, "absolute"):
                resolve_product_path(root, r"C:\\outside.txt")

    @unittest.skipIf(os.name == "nt", "symlink creation may require Windows developer mode")
    def test_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as product, tempfile.TemporaryDirectory() as outside:
            root = Path(product)
            secret = Path(outside) / "secret.net"
            secret.write_text("secret", encoding="utf-8")
            (root / "linked.net").symlink_to(secret)
            with self.assertRaisesRegex(ProductPathError, "escapes product_root"):
                resolve_product_path(root, "linked.net", kind="file")


if __name__ == "__main__":
    unittest.main()
