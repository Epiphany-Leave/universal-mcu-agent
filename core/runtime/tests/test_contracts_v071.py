"""Focused v0.7.1 routing, schema, capability, platform and path contracts."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

CORE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CORE))

from runtime.capability_gate import CapabilityGate
from runtime.config_loader import load_verification_profiles, update_config
from runtime.engine import RuntimeEngine
from runtime.intent_manager import IntentManager
from runtime.platforms.esp_idf import EspIdfAdapter
from runtime.project_layout import project_paths
from runtime.work_package_factory import WorkPackageFactory


class IntentAndFactoryContractTests(unittest.TestCase):
    def test_chinese_and_english_action_priority(self):
        cases = {
            "调试并实现这个编译失败修复": "debug",
            "debug and implement this build error fix": "debug",
            "审查代码并实现建议": "review",
            "review and implement this change": "review",
            "提取数据手册后实现驱动": "evidence",
            "extract datasheet evidence then implement": "evidence",
            "实现摄像头驱动": "implementation",
            "implement a camera driver": "implementation",
        }
        manager = IntentManager()
        for request, expected in cases.items():
            with self.subTest(request=request):
                self.assertEqual(expected, manager.classify(request)["type"])
        generic_sensor = manager.classify("implement a temperature sensor driver")
        self.assertEqual(["project"], generic_sensor["scope"])
        self.assertEqual("IMPLEMENTATION", WorkPackageFactory().create(generic_sensor)["type"])

    def test_action_routes_and_evidence_roles(self):
        manager = IntentManager()
        factory = WorkPackageFactory()
        debug = factory.create(manager.classify("debug and implement build failure"))
        self.assertEqual("VALIDATION", debug["type"])
        self.assertEqual("build-debug", debug["tasks"][0]["handler"])
        review = factory.create(manager.classify("review and implement"))
        self.assertEqual("VALIDATION", review["type"])
        self.assertEqual("code-qa", review["tasks"][0]["handler"])
        self.assertEqual("read_only", review["tasks"][0]["execution_mode"])
        implementation = factory.create(manager.classify("implement feature"))
        self.assertEqual("firmware-development", implementation["tasks"][0]["handler"])
        self.assertEqual([], implementation["tasks"][0]["outputs"])

        evidence = factory.create(manager.classify("提取需求、数据手册和原理图证据"))
        by_handler = {task["handler"]: task for task in evidence["tasks"]}
        self.assertEqual(
            ["requirements.structured", "requirements.acceptance_tests"],
            by_handler["requirements-analysis"]["outputs"],
        )
        self.assertEqual(["device.project.capabilities"], by_handler["datasheet-extraction"]["outputs"])
        self.assertEqual(["hardware.peripheral_connections"], by_handler["schematic-analysis"]["outputs"])
        fallback = factory.create(manager.classify("resolve generic evidence"))
        self.assertEqual("evidence-resolution", fallback["tasks"][0]["handler"])

    def test_camera_and_all_factory_packages_validate(self):
        schema = json.loads((CORE / "schemas" / "work-package.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        manager = IntentManager()
        factory = WorkPackageFactory()
        for request in (
            "实现摄像头驱动", "创建UI", "写QMC5883驱动", "debug build error",
            "review code", "提取需求和原理图", "implement generic feature",
        ):
            package = factory.create(manager.classify(request))
            self.assertEqual([], list(validator.iter_errors(package)), request)
        camera = factory.create(manager.classify("implement camera driver"))
        self.assertEqual("CAMERA_DRIVER", camera["type"])

    def test_input_role_schema_accepts_string_or_scoped_object(self):
        schema = json.loads((CORE / "schemas" / "task.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        task = WorkPackageFactory().create(IntentManager().classify("implement feature"))["tasks"][0]
        for roles in (["requirements.primary"], [{"role": "device.camera.datasheet", "scope": "product"}]):
            candidate = dict(task, input_roles=roles)
            self.assertTrue(validator.is_valid(candidate))
        self.assertFalse(validator.is_valid(dict(task, input_roles=[{"role": "x"}])))


class CapabilityContractTests(unittest.TestCase):
    @staticmethod
    def _package(states: list[str]) -> dict:
        tasks = []
        for index, state in enumerate(states):
            tasks.append({
                "id": f"t{index}", "state": state,
                "requirements": [{
                    "capability": "board.x", "facts": ["pin"],
                    "gate": "REQUIRED", "on_missing": "BLOCKED_HARDWARE",
                }],
            })
        return {"mode": "prototype", "state": "READY", "tasks": tasks}

    def test_regate_only_changes_ready_or_blocked(self):
        states = ["READY", "BLOCKED_HARDWARE", "IMPLEMENTING", "VALIDATING", "DONE", "FAILED", "SKIPPED"]
        result = CapabilityGate().evaluate(self._package(states), [])
        actual = [task["state"] for task in result["tasks"]]
        self.assertEqual("BLOCKED_HARDWARE", actual[0])
        self.assertEqual("BLOCKED_HARDWARE", actual[1])
        self.assertEqual(states[2:], actual[2:])
        self.assertEqual(len(states), result["summary"]["total"])
        self.assertEqual("IN_PROGRESS", result["state"])
        confirmed = [{
            "schema_version": "2.0", "id": "board.x", "state": "AVAILABLE",
            "facts": {"pin": {"state": "CONFIRMED", "value": "PA1", "evidence_ids": ["test"]}},
        }]
        progressed = CapabilityGate().evaluate(self._package(["READY", "DONE"]), confirmed)
        self.assertEqual("IN_PROGRESS", progressed["state"])

    def test_invalid_ear_expiry_is_fail_closed(self):
        capability = [{
            "schema_version": "2.0", "id": "board.x", "state": "UNKNOWN", "facts": {
                "pin": {"state": "ASSUMED", "value": 1, "evidence_ids": [], "ear_id": "EAR-1"}
            },
        }]
        assumption = [{
            "schema_version": "2.0", "id": "EAR-1", "mode": "prototype",
            "state": "APPROVED", "task_ids": ["t0"], "scope": "board.x",
            "assumption": "temporary pin", "risks": ["wrong pin"],
            "validation_method": "inspect board", "owner": "test",
            "expires_at": "not-a-time", "rollback": "remove binding", "approved_by": "owner",
        }]
        task = CapabilityGate().evaluate(self._package(["READY"]), capability, assumption)["tasks"][0]
        self.assertEqual("BLOCKED_HARDWARE", task["state"])

    def test_invalid_confirmed_capability_cannot_unlock_a_task(self):
        invalid = [{
            "schema_version": "2.0", "id": "board.x", "state": "BLOCKED",
            "facts": {"pin": {"state": "CONFIRMED", "value": None, "evidence_ids": []}},
        }]
        with self.assertRaises(ValueError):
            CapabilityGate().evaluate(self._package(["READY"]), invalid)

    def test_capability_precedence_config_store_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            config.write_text('hardware:\n  camera: "config"\n', encoding="utf-8")
            store = root / "capabilities.json"
            store.write_text(json.dumps({"capabilities": [{
                "id": "board.camera", "facts": {
                    "identity": {"state": "CONFIRMED", "value": "store", "evidence_ids": ["store"]}
                },
            }]}), encoding="utf-8")
            explicit = [{
                "id": "board.camera", "facts": {
                    "identity": {"state": "CONFIRMED", "value": "explicit", "evidence_ids": ["call"]}
                },
            }]
            result = RuntimeEngine().plan(
                "implement camera driver", config_path=str(config), capability_store=str(store), capabilities=explicit,
            )
            capability = next(item for item in result["capabilities"] if item["id"] == "board.camera")
            self.assertEqual("explicit", capability["facts"]["identity"]["value"])


class PlatformConfigAndPathContractTests(unittest.TestCase):
    def test_platform_detection_creates_root_capability_or_blocks_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main").mkdir()
            (root / "CMakeLists.txt").write_text(
                "include($ENV{IDF_PATH}/tools/cmake/project.cmake)\nproject(test)\n", encoding="utf-8"
            )
            (root / "main" / "CMakeLists.txt").write_text("idf_component_register()\n", encoding="utf-8")
            detected = RuntimeEngine().plan(
                "implement feature", platform="esp-idf", project_root=str(root),
            )
            platform_cap = next(item for item in detected["capabilities"] if item["id"] == "platform.project")
            self.assertEqual(str(root.resolve()), platform_cap["facts"]["root"]["value"])
            self.assertEqual("READY", detected["work_packages"][0]["tasks"][0]["state"])

            failed = RuntimeEngine().plan(
                "implement feature", platform="esp-idf", project_root=str(root / "missing"),
            )
            self.assertEqual("BLOCKED_CONFIG", failed["platform_detection"]["status"])
            self.assertEqual("BLOCKED_CONFIG", failed["work_packages"][0]["tasks"][0]["state"])

    def test_structured_profiles_and_inline_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_text(
                'mcu:\n  chip: "old"  # keep this\nverification:\n  profiles:\n    build:\n'
                '      argv: ["idf.py", "build"]\n      timeout: 30\n',
                encoding="utf-8",
            )
            update_config(config, {"mcu.chip": "new"})
            text = config.read_text(encoding="utf-8")
            self.assertIn('chip: "new"  # keep this', text)
            self.assertEqual(["idf.py", "build"], load_verification_profiles(config)["build"]["argv"])
            config.write_text("hardware: []\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_verification_profiles(config)

    def test_layout_rejects_absolute_parent_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for value in ("/outside", "../outside", r"C:\\outside"):
                config = root / "config.yaml"
                config.write_text(f'directories:\n  software: "{value}"\n', encoding="utf-8")
                with self.subTest(value=value), self.assertRaises(ValueError):
                    project_paths(root, "demo", config)
            outside = root / "outside"
            outside.mkdir()
            try:
                (root / "link").symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                if getattr(exc, "winerror", None) == 1314:
                    self.skipTest("Windows symlink privilege is unavailable")
                raise
            config = root / "config.yaml"
            config.write_text('directories:\n  software: "link"\n', encoding="utf-8")
            # Make the symlink escape a distinct product root.
            product = root / "product"
            product.mkdir()
            (product / "link").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                project_paths(product, "demo", config)

    def test_windows_paths_convert_only_under_wsl(self):
        source = r"D:\work\main.c"
        self.assertEqual(source, EspIdfAdapter._normalize_windows_path(source, is_wsl=False))
        self.assertEqual("/mnt/d/work/main.c", EspIdfAdapter._normalize_windows_path(source, is_wsl=True))
        self.assertFalse(EspIdfAdapter._is_wsl(system="Windows", release="11"))
        self.assertTrue(EspIdfAdapter._is_wsl(system="Linux", release="microsoft-standard-WSL2"))


if __name__ == "__main__":
    unittest.main()
