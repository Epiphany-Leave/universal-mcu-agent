import json
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from mcu_generate import _hardware_to_md, ensure_output_dir
from project_config import configured_path
from validate_lifecycle import validate_artifacts, validate_bundle, validate_phase


class LifecycleTests(unittest.TestCase):
    def test_completed_with_blocked_work_item_is_valid(self):
        phase = json.loads((TOOLS.parents[1] / "doc" / "completed-assessment.json").read_text(encoding="utf-8"))
        self.assertEqual([], validate_phase(phase))

    def test_waiting_requires_resume_input(self):
        phase = json.loads((TOOLS.parents[1] / "doc" / "waiting-external.json").read_text(encoding="utf-8"))
        phase["requested_external_inputs"] = []
        self.assertTrue(any("requested_external_inputs" in error for error in validate_phase(phase)))

    def test_production_rejects_prototype_waiver(self):
        phase = json.loads((TOOLS.parents[1] / "doc" / "completed-assessment.json").read_text(encoding="utf-8"))
        debts = {"schema_version": "1.0", "revision": 1, "debts": [{"id": "LCD-ID-01", "state": "WAIVED_PROTOTYPE_ONLY", "statement": "unknown", "evidence_required": ["identity"], "dependencies": [], "sources": [], "acceptance_record": None}]}
        bundle = {"phase": phase, "evidence_debts": debts, "artifact_manifest": {"schema_version": "1.0", "revision": 1, "artifacts": []}, "engineering_assumptions": []}
        self.assertTrue(any("prototype-only waiver" in error for error in validate_bundle(bundle)))

    def test_artifact_cycle_is_rejected(self):
        manifest = {"schema_version": "1.0", "revision": 1, "artifacts": [
            {"id": "a", "path": "a.json", "type": "report", "revision": 1, "sha256": None, "producer": "test", "dependencies": ["b"], "supersedes": [], "evidence_class": "GENERATED_CONFIG", "status": "CURRENT"},
            {"id": "b", "path": "b.json", "type": "report", "revision": 1, "sha256": None, "producer": "test", "dependencies": ["a"], "supersedes": [], "evidence_class": "GENERATED_CONFIG", "status": "CURRENT"},
        ]}
        self.assertTrue(any("dependency cycle" in error for error in validate_artifacts(manifest)))

    def test_configured_production_path_and_unknown_rendering(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            config = project / ".mcu-agent" / "config.yaml"
            config.parent.mkdir()
            config.write_text("directories:\n  production: output/release\n", encoding="utf-8")
            self.assertEqual(project / "output/release", configured_path(project, "production"))
            self.assertEqual(project / "output/release/Hardware", ensure_output_dir(project))
        md = _hardware_to_md({"interfaces": [{"type": "ADC", "pin": "PA0"}], "source": {}})
        self.assertIn("UNKNOWN", md)
        self.assertNotIn("12bit", md)


if __name__ == "__main__":
    unittest.main()
