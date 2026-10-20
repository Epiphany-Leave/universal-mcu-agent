import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


CORE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CORE))

from runtime.artifact_resolver import ArtifactManifestError, ArtifactResolver, write_manifest
from runtime.esp_dev_skill import EspDevSkillRouter
from runtime.memory import ProjectMemoryStore, ProjectMemoryError
from runtime.session_bootstrap import SessionBootstrap, SessionBootstrapError
from runtime.engine import RuntimeEngine
from runtime.platforms.esp_idf import EspIdfAdapter
from runtime.project_layout import create_project_layout
from runtime.review_budget import ReviewBudget
from runtime.task_executor import TaskExecutionError, TaskExecutor
from runtime.transition_engine import TransitionEngine, TransitionError


UNKNOWN_LCD = [{
    "schema_version": "2.0",
    "id": "lcd.display",
    "state": "UNKNOWN",
    "facts": {"panel.identity": {"state": "UNKNOWN", "value": None, "evidence_ids": []}},
}]


class RuntimeConformanceTests(unittest.TestCase):
    def test_session_bootstrap_records_host_handshake(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".mcu-agent" / "runtime").mkdir(parents=True)
            (root / ".mcu-agent" / "runtime" / "cli.py").write_text("# runtime", encoding="utf-8")
            (root / "CLAUDE.md").write_text("runtime entry", encoding="utf-8")
            result = SessionBootstrap().start(root, agent="claude-code", mode="prototype")
            self.assertEqual("ACCEPTED", result["handshake"])
            self.assertTrue(Path(result["record_path"]).is_file())
            with self.assertRaises(SessionBootstrapError):
                SessionBootstrap().start(root, agent="codex")

    def test_project_memory_is_deduplicated_approved_and_promoted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ProjectMemoryStore(root)
            record = store.capture(
                summary="ESP-IDF component was missing from the build graph",
                problem="Build passed but compile_commands omitted lcd_driver",
                solution="Validate compile_commands inclusion after idf.py build",
                source_task="platform_component",
                platform="esp32",
                confidence="verified",
                tags=["esp-idf", "component"],
            )
            duplicate = store.capture(
                summary="ESP-IDF component was missing from the build graph",
                problem="Build passed but compile_commands omitted lcd_driver",
                solution="Validate compile_commands inclusion after idf.py build",
                source_task="platform_component",
                platform="esp32",
                confidence="verified",
            )
            self.assertEqual(record["memory_id"], duplicate["memory_id"])
            self.assertTrue(store.search("lcd_driver build", platform="esp32"))
            with self.assertRaises(ProjectMemoryError):
                store.promote(record["memory_id"], target="platform", reviewer="reviewer")
            approved = store.approve(record["memory_id"], reviewer="reviewer", notes="reusable build lesson")
            self.assertEqual("APPROVED_PROJECT", approved["status"])
            promoted = store.promote(record["memory_id"], target="platform", reviewer="reviewer")
            self.assertEqual("PROMOTED", promoted["status"])
            self.assertTrue(Path(promoted["promoted_path"]).is_file())

    def test_optional_esp_skill_routes_without_becoming_a_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "esp-dev-skill"
            (skill / "repos" / "esp-idf" / "recipes").mkdir(parents=True)
            (skill / "repos" / "esp-idf" / "resources").mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nversion: 1.0.0\n---\n", encoding="utf-8")
            (skill / "repos" / "esp-idf" / "SKILL.md").write_text(
                "---\nversion: 1.0.0\nlicense: Apache-2.0\n---\n", encoding="utf-8"
            )
            (skill / "repos" / "esp-idf" / "recipes" / "i2c_master.md").write_text("i2c", encoding="utf-8")
            result = EspDevSkillRouter(skill).route("帮我写 QMC5883 I2C 驱动", platform="esp-idf")
            self.assertTrue(result["available"])
            self.assertEqual("esp-idf", result["repository"])
            self.assertTrue(any(item["kind"] == "recipe" for item in result["references"]))

            (skill / "repos" / "arduino-esp32" / "recipes").mkdir(parents=True)
            (skill / "repos" / "arduino-esp32" / "SKILL.md").write_text("---\nversion: 1\n---\n", encoding="utf-8")
            (skill / "repos" / "arduino-esp32" / "recipes" / "i2c_master_slave.md").write_text("i2c", encoding="utf-8")
            arduino = EspDevSkillRouter(skill).route("I2C sensor", platform="arduino-esp32")
            self.assertEqual("arduino-esp32", arduino["repository"])
            self.assertTrue(any(item["path"].endswith("i2c_master_slave.md") for item in arduino["references"]))

    def test_task_context_can_use_optional_esp_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "esp-dev-skill"
            (skill / "repos" / "esp-idf").mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nversion: 1\n---\n", encoding="utf-8")
            (skill / "repos" / "esp-idf" / "SKILL.md").write_text("---\nversion: 1\n---\n", encoding="utf-8")
            task = {"id": "t", "state": "READY", "allowed_paths": ["components"]}
            context = TaskExecutor().prepare(
                task,
                request="write an ESP-IDF driver",
                mode="prototype",
                project_root=root / "project",
                product_root=root,
                skill_root=skill,
                platform="esp-idf",
            )
            self.assertTrue(context["esp_dev_skill"]["available"])
            self.assertEqual("esp-idf", context["esp_dev_skill"]["repository"])

    def test_lcd_unknown_does_not_block_ui_mock(self):
        result = RuntimeEngine().plan("帮我做一个简单UI", mode="prototype", capabilities=UNKNOWN_LCD)
        tasks = {task["id"]: task["state"] for task in result["work_packages"][0]["tasks"]}
        self.assertEqual("READY", tasks["ui_architecture"])
        self.assertEqual("READY", tasks["mock_display_backend"])
        self.assertEqual("BLOCKED_HARDWARE", tasks["lcd_binding"])
        self.assertEqual("BLOCKED_HARDWARE", tasks["display_hardware_test"])

    def test_repeated_evidence_review_returns_previous_conclusion(self):
        budget = ReviewBudget()
        artifacts = [{"id": "hardware", "identity": "board-a", "revision": 1, "sha256": "a" * 64}]
        conclusion = {"status": "WAITING_EXTERNAL", "debt_ids": ["LCD-ID-01"]}
        first = budget.request("hardware-evidence", artifacts)
        recorded = budget.request("hardware-evidence", artifacts, previous_conclusion=conclusion)
        second = budget.request("hardware-evidence", artifacts)
        self.assertEqual("REVIEW_ALLOWED", first["action"])
        self.assertEqual("CONCLUSION_RECORDED", recorded["action"])
        self.assertEqual("RETURN_EXISTING", second["action"])
        self.assertEqual(conclusion, second["conclusion"])
        self.assertEqual(1, second["rounds"])

    def test_review_budget_closes_after_two_distinct_rounds(self):
        budget = ReviewBudget()
        for revision in (1, 2):
            result = budget.request("hardware-evidence", [{"id": "hardware", "revision": revision, "sha256": str(revision) * 64}])
            self.assertEqual("REVIEW_ALLOWED", result["action"])
        exhausted = budget.request("hardware-evidence", [{"id": "hardware", "revision": 3, "sha256": "3" * 64}])
        self.assertEqual("CLOSE_WITH_DEBT", exhausted["action"])
        self.assertEqual("PASS_WITH_DEBT", exhausted["result"])

    def test_qmc5883_driver_keeps_portable_tasks_ready(self):
        result = RuntimeEngine().plan("帮我写QMC5883磁力计驱动，可以测量平面角度")
        tasks = {task["id"]: task["state"] for task in result["work_packages"][0]["tasks"]}
        self.assertEqual("READY", tasks["portable_driver_api"])
        self.assertEqual("READY", tasks["register_driver"])
        self.assertEqual("READY", tasks["fake_i2c_test"])
        self.assertEqual("READY", tasks["platform_component"])
        self.assertEqual("BLOCKED_HARDWARE", tasks["board_binding"])
        self.assertEqual("BLOCKED_HARDWARE", tasks["angle_calibration"])

    def test_approved_ear_is_task_scoped_and_never_unlocks_production(self):
        capabilities = [{
            "schema_version": "2.0",
            "id": "lcd.display",
            "state": "UNKNOWN",
            "facts": {"panel.identity": {"state": "ASSUMED", "value": "mock-panel", "evidence_ids": [], "ear_id": "EAR-LCD-1"}},
        }]
        assumptions = [{
            "schema_version": "2.0", "id": "EAR-LCD-1", "mode": "prototype",
            "state": "APPROVED", "task_ids": ["lcd_binding"], "scope": "lcd.display",
            "assumption": "mock panel for prototype", "risks": ["not physical hardware"],
            "validation_method": "replace with target test", "owner": "test",
            "expires_at": "2099-01-01T00:00:00Z", "rollback": "remove mock binding",
            "approved_by": "owner",
        }]
        prototype = RuntimeEngine().plan("创建UI", mode="prototype", capabilities=capabilities, assumptions=assumptions)
        prototype_tasks = {task["id"]: task["state"] for task in prototype["work_packages"][0]["tasks"]}
        self.assertEqual("READY", prototype_tasks["lcd_binding"])
        self.assertEqual("BLOCKED_HARDWARE", prototype_tasks["display_hardware_test"])
        production = RuntimeEngine().plan("创建UI", mode="production", capabilities=capabilities, assumptions=assumptions)
        production_tasks = {task["id"]: task["state"] for task in production["work_packages"][0]["tasks"]}
        self.assertEqual("BLOCKED_HARDWARE", production_tasks["lcd_binding"])

    def test_transition_engine_rejects_invalid_task_jump(self):
        engine = TransitionEngine()
        with self.assertRaises(TransitionError):
            engine.transition("task", {"state": "READY"}, "DONE")
        record = engine.transition("task", {"state": "READY"}, "IMPLEMENTING")
        self.assertEqual("IMPLEMENTING", record["state"])

    def test_task_executor_bridges_model_edit_and_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            project = product / "software" / "pet-robot"
            component = project / "components"
            component.mkdir(parents=True)
            source = component / "qmc5883.c"
            source.write_text("void qmc5883_init(void) {}\n", encoding="utf-8")
            task = {
                "id": "portable_driver_api",
                "title": "Implement portable API",
                "state": "READY",
                "requirements": [],
                "allowed_paths": ["components"],
                "verification_tier": "host",
                "outputs": [],
            }
            executor = TaskExecutor()
            context = executor.prepare(
                task,
                request="写QMC5883驱动",
                mode="production",
                project_root=project,
                product_root=product,
            )
            result = executor.execute(
                task,
                context=context,
                model_runner=lambda received: {
                    "status": "IMPLEMENTED",
                    "summary": "portable API added",
                    "changed_files": ["components/qmc5883.c"],
                },
                verifier=lambda _task, _model: {"passed": True, "command": "host-test"},
            )
            self.assertEqual("DONE", result["state"])
            self.assertEqual("host-test", result["execution"]["verification"]["command"])
            self.assertTrue(result["execution"].get("memory_id"))

            outside = product / "hardware" / "notes.txt"
            outside.parent.mkdir()
            outside.write_text("do not modify", encoding="utf-8")
            with self.assertRaises(TaskExecutionError):
                executor.execute(
                    task,
                    context=context,
                    model_runner=lambda _received: {"changed_files": ["../../hardware/notes.txt"]},
                )

    def test_new_project_layout_is_software_project_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = create_project_layout(root, "Pet Robot")
            self.assertEqual(root.resolve() / "software" / "pet-robot", paths["project_root"])
            self.assertTrue((root / "hardware").is_dir())
            self.assertTrue((root / "software" / "pet-robot" / "components").is_dir())
            self.assertFalse((root / "firmware").exists())
            self.assertFalse((root / "drivers").exists())

    def test_manifest_rejects_multiple_current_artifacts_for_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / ".mcu-agent" / "artifact-manifest.yaml"
            manifest.parent.mkdir()
            manifest.write_text(json.dumps({
                "schema_version": "2.0",
                "revision": 1,
                "artifacts": [
                    {"id": "prd-1", "scope": "product", "role": "requirements.primary", "path": "a.md", "status": "CURRENT"},
                    {"id": "prd-2", "scope": "product", "role": "requirements.primary", "path": "b.md", "status": "CURRENT"},
                ],
            }), encoding="utf-8")
            with self.assertRaises(ArtifactManifestError):
                ArtifactResolver(root)

    def test_task_output_registers_and_supersedes_canonical_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = root / ".mcu-agent"
            agent.mkdir()
            old = root / "old.json"
            new = root / "new.json"
            old.write_text("old", encoding="utf-8")
            new.write_text("new", encoding="utf-8")
            manifest_path = agent / "artifact-manifest.yaml"
            write_manifest(manifest_path, {
                "schema_version": "2.0",
                "revision": 1,
                "artifacts": [{
                    "id": "source-old", "scope": "project", "role": "source.qmc5883", "logical_name": "qmc",
                    "path": "old.json", "media_type": "application/json", "identity": "old", "revision": 1,
                    "sha256": "0" * 64, "producer": "legacy", "dependencies": [], "supersedes": [],
                    "evidence_class": "GENERATED_CONFIG", "status": "CURRENT", "accepted_by": None, "accepted_at": None,
                }],
            })
            resolver = ArtifactResolver(root)
            registered = resolver.register_generated({
                "id": "source-new", "role": "source.qmc5883", "logical_name": "qmc",
                "path": "new.json", "media_type": "application/json", "evidence_class": "GENERATED_CONFIG",
            }, producer="task:portable_driver_api")
            self.assertEqual("source-old", registered["supersedes"][0])
            self.assertEqual("new.json", resolver.resolve("source.qmc5883", scope="project")["path"])

    def test_esp_idf_build_pass_fails_when_component_not_compiled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main").mkdir()
            component = root / "components" / "lcd_driver"
            component.mkdir(parents=True)
            (root / "CMakeLists.txt").write_text(
                "include($ENV{IDF_PATH}/tools/cmake/project.cmake)\nproject(test)\n", encoding="utf-8"
            )
            (root / "sdkconfig").write_text('CONFIG_IDF_TARGET="esp32s3"\n', encoding="utf-8")
            (root / "main" / "CMakeLists.txt").write_text(
                'idf_component_register(SRCS "main.c" INCLUDE_DIRS ".")\n', encoding="utf-8"
            )
            (component / "CMakeLists.txt").write_text(
                'idf_component_register(SRCS "lcd.c" INCLUDE_DIRS ".")\n', encoding="utf-8"
            )
            (component / "lcd.c").write_text("void lcd_init(void) {}\n", encoding="utf-8")
            compile_commands = root / "compile_commands.json"
            compile_commands.write_text("[]", encoding="utf-8")
            result = EspIdfAdapter().validate(root, compile_commands=compile_commands, build_succeeded=True)
            self.assertEqual("FAIL", result["status"])
            self.assertFalse(result["components"][0]["included"])


if __name__ == "__main__":
    unittest.main()


class BuildVerifierTests(unittest.TestCase):
    def test_build_verifier_reports_failure_without_skipping(self):
        from runtime.verifier import BuildVerifier
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_text("toolchain:\n  build_command: 'false'\n", encoding="utf-8")
            rejected = BuildVerifier(config_path=config).verify({"project_root": tmp})
            self.assertFalse(rejected["skipped"])
            self.assertFalse(rejected["passed"])
            self.assertFalse(rejected["audit"]["allowed"])
            verdict = BuildVerifier(config_path=config, allow_shell_command=True).verify({"project_root": tmp})
            self.assertFalse(verdict["skipped"])
            self.assertFalse(verdict["passed"])
            self.assertEqual(verdict["exit_code"], 1)

    def test_build_verifier_skips_when_no_command_configured(self):
        from runtime.verifier import BuildVerifier
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_text("toolchain:\n  ide: esp-idf\n", encoding="utf-8")
            verdict = BuildVerifier(config_path=config).verify({"project_root": tmp})
            self.assertTrue(verdict["skipped"])

    def test_build_verifier_captures_error_lines(self):
        from runtime.verifier import BuildVerifier
        errors = BuildVerifier._errors("a:1:1: error: boom\nb:2:2: warning: meh\nc:3:3: fatal error: end")
        self.assertEqual(len(errors), 2)
        self.assertIn("boom", errors[0])


class Stm32AdapterTests(unittest.TestCase):
    IOC_SAMPLE = """#MicroXplorer Configuration settings - do not modify
Mcu.CPN=STM32G431CBU6
Mcu.Pin0=PA8
Mcu.Pin1=PA9
Mcu.Pin2=PB6
PA8.Signal=I2C1_SCL
PA9.Signal=I2C1_SDA
PB6.Signal=USART1_TX
"""

    def test_ioc_extract_facts_maps_pins_to_capabilities(self):
        from runtime.platforms.stm32 import extract_facts, parse_ioc
        with tempfile.TemporaryDirectory() as tmp:
            ioc = Path(tmp) / "demo.ioc"
            ioc.write_text(self.IOC_SAMPLE, encoding="utf-8")
            parsed = parse_ioc(ioc)
            self.assertEqual(parsed["mcu"], "STM32G431CBU6")
            result = extract_facts(ioc)
            caps = {c["id"]: c for c in result["capabilities"]}
            self.assertIn("board.i2c1", caps)
            self.assertEqual(caps["board.i2c1"]["facts"]["scl_pin"]["value"], "PA8")
            self.assertEqual(caps["board.i2c1"]["facts"]["sda_pin"]["value"], "PA9")
            self.assertIn("board.uart1", caps)
            self.assertEqual(caps["board.uart1"]["facts"]["tx_pin"]["value"], "PB6")

    def test_ioc_facts_unblock_i2c_binding_task(self):
        from runtime.capability_gate import CapabilityGate
        from runtime.platforms.stm32 import extract_facts
        with tempfile.TemporaryDirectory() as tmp:
            ioc = Path(tmp) / "demo.ioc"
            ioc.write_text(self.IOC_SAMPLE, encoding="utf-8")
            caps = extract_facts(ioc)["capabilities"]
            task = {
                "id": "board_binding", "title": "bind", "state": "READY",
                "requirements": [
                    {"capability": "board.i2c1", "facts": ["sda_pin", "scl_pin"],
                     "gate": "REQUIRED", "on_missing": "BLOCKED_HARDWARE"}
                ],
                "debt_ids": [], "verification_tier": "target", "outputs": [],
                "handler": "firmware-development", "allowed_paths": ["Core"], "input_roles": [],
            }
            package = {"schema_version": "2.0", "mode": "production", "tasks": [task]}
            gated = CapabilityGate().evaluate(package, caps, [])["tasks"][0]
            self.assertEqual(gated["state"], "READY")
            self.assertNotIn("blockers", gated)

    def test_stm32_adapter_detect_and_validate(self):
        from runtime.platforms.stm32 import Stm32Adapter
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "demo.ioc").write_text(self.IOC_SAMPLE, encoding="utf-8")
            adapter = Stm32Adapter()
            detected = adapter.detect(Path(tmp))
            self.assertEqual(detected["platform"], "stm32")
            validated = adapter.validate(Path(tmp))
            self.assertEqual(validated["status"], "PASS")
            self.assertEqual(validated["mcu"], "STM32G431CBU6")
