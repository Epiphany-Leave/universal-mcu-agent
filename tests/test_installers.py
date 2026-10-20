from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
BASH_INSTALLER = ROOT / "install.sh"
POWERSHELL_INSTALLER = ROOT / "install.ps1"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
BEGIN = "<!-- BEGIN UNIVERSAL MCU AGENT -->"
END = "<!-- END UNIVERSAL MCU AGENT -->"


@unittest.skipUnless(os.name != "nt" and shutil.which("bash"), "Bash installer tests require POSIX bash")
class BashInstallerTests(unittest.TestCase):
    def run_installer(self, project: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            ["bash", str(BASH_INSTALLER), *arguments, "--project", str(project)],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if check and result.returncode:
            self.fail(f"installer exited {result.returncode}:\n{result.stdout}")
        return result

    def test_codex_install_is_idempotent_and_preserves_agents_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "product"
            project.mkdir()
            agents = project / "AGENTS.md"
            agents.write_text("# Team rules\n\nKeep this text.\n", encoding="utf-8")

            self.run_installer(project, "--agent", "codex")
            self.run_installer(project, "--agent", "pi")

            text = agents.read_text(encoding="utf-8-sig")
            self.assertIn("# Team rules", text)
            self.assertIn("Keep this text.", text)
            self.assertEqual(1, text.count(BEGIN))
            self.assertEqual(1, text.count(END))
            self.assertIn(".mcu-agent/agent-entry.md", text)
            agent_dir = project / ".mcu-agent"
            self.assertEqual(VERSION, (agent_dir / "VERSION").read_text(encoding="utf-8").strip())
            self.assertEqual(
                {"schema_version": "2.0", "revision": 1, "artifacts": []},
                yaml.safe_load((agent_dir / "artifact-manifest.yaml").read_text(encoding="utf-8-sig")),
            )
            self.assertEqual(
                (ROOT / "adapters/common/runtime-entry.md").read_text(encoding="utf-8"),
                (agent_dir / "agent-entry.md").read_text(encoding="utf-8"),
            )
            self.assertFalse((agent_dir / "backups").exists())
            self.assertTrue((project / ".agents/skills/mcu-agent/SKILL.md").is_file())
            gitignore = (project / ".gitignore").read_text(encoding="utf-8-sig")
            self.assertEqual(1, gitignore.count("# BEGIN UNIVERSAL MCU AGENT"))
            self.assertIn(".mcu-agent/state/", gitignore)
            self.assertIn(".mcu-agent/workspaces/", gitignore)

    def test_all_host_entries_use_managed_blocks_and_preserve_user_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "product"; project.mkdir()
            files = [
                project / "CLAUDE.md",
                project / ".cursorrules",
                project / ".opencode/instructions/mcu-agent.md",
            ]
            for path in files:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("user host rule\n", encoding="utf-8")
            self.run_installer(project, "--agent", "all")
            self.run_installer(project, "--agent", "all")
            for path in files:
                text = path.read_text(encoding="utf-8-sig")
                self.assertIn("user host rule", text)
                self.assertEqual(1, text.count("<!-- BEGIN UNIVERSAL MCU AGENT ENTRY -->"))
                self.assertEqual(1, text.count("<!-- END UNIVERSAL MCU AGENT ENTRY -->"))

    def test_update_backs_up_and_migrates_without_deleting_user_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "product"
            project.mkdir()
            self.run_installer(project, "--agent", "codex")
            agent = project / ".mcu-agent"

            (agent / "config.yaml").write_text("user_config: keep\n", encoding="utf-8")
            (agent / "artifact-manifest.yaml").write_text("revision: 9\n", encoding="utf-8")
            (agent / "project-contract.json").write_text('{"sentinel": true}\n', encoding="utf-8")
            (agent / "custom/user.txt").write_text("custom", encoding="utf-8")
            (agent / "knowledge/project/experience.md").write_text("experience", encoding="utf-8")
            (agent / "state/intents").mkdir(parents=True, exist_ok=True)
            (agent / "state/intents/conflict.json").write_text(json.dumps({"revision": 5, "owner": "state"}), encoding="utf-8")
            (agent / "state/intents/legacy-wins.json").write_text(json.dumps({"revision": 1, "owner": "state"}), encoding="utf-8")
            (agent / "runtime/intents").mkdir(parents=True, exist_ok=True)
            (agent / "runtime/intents/conflict.json").write_text(json.dumps({"revision": 2, "owner": "legacy"}), encoding="utf-8")
            (agent / "runtime/intents/legacy-wins.json").write_text(json.dumps({"revision": 8, "owner": "legacy"}), encoding="utf-8")
            (agent / "runtime/intents/moved.json").write_text(json.dumps({"revision": 7}), encoding="utf-8")
            (agent / "runtime/claims").mkdir()
            (agent / "runtime/claims/active.json").write_text(json.dumps({"revision": 2}), encoding="utf-8")
            (agent / "runtime/capabilities.json").write_text(json.dumps({"revision": 3}), encoding="utf-8")
            (agent / "state/phase-state.json").write_text(json.dumps({"revision": 9, "owner": "state"}), encoding="utf-8")
            (agent / "runtime/phase-state").mkdir()
            (agent / "runtime/phase-state/old.json").write_text(json.dumps({"revision": 1}), encoding="utf-8")
            (agent / "runtime/engineering-assumptions.json").write_text(
                json.dumps({"revision": 4, "assumptions": []}), encoding="utf-8"
            )
            (agent / "knowledge/stm32").mkdir()
            (agent / "knowledge/stm32/obsolete.md").write_text("old", encoding="utf-8")
            (agent / "knowledge/VERSION").write_text("0.0.0\n", encoding="utf-8")

            self.run_installer(project, "--agent", "all", "--update")

            self.assertEqual("user_config: keep\n", (agent / "config.yaml").read_text(encoding="utf-8"))
            self.assertEqual("revision: 9\n", (agent / "artifact-manifest.yaml").read_text(encoding="utf-8"))
            self.assertEqual('{"sentinel": true}\n', (agent / "project-contract.json").read_text(encoding="utf-8"))
            self.assertEqual("custom", (agent / "custom/user.txt").read_text(encoding="utf-8"))
            self.assertEqual("experience", (agent / "knowledge/project/experience.md").read_text(encoding="utf-8"))
            conflict = json.loads((agent / "state/intents/conflict.json").read_text(encoding="utf-8"))
            self.assertEqual("state", conflict["owner"])
            legacy_winner = json.loads((agent / "state/intents/legacy-wins.json").read_text(encoding="utf-8"))
            self.assertEqual("legacy", legacy_winner["owner"])
            self.assertTrue((agent / "state/intents/moved.json").is_file())
            self.assertTrue((agent / "state/claims/active.json").is_file())
            self.assertTrue((agent / "state/capabilities.json").is_file())
            self.assertTrue((agent / "state/phase-state.json").is_file())
            self.assertEqual("state", json.loads((agent / "state/phase-state.json").read_text(encoding="utf-8"))["owner"])
            self.assertTrue((agent / "state/legacy/phase-state/old.json").is_file())
            self.assertTrue((agent / "state/engineering-assumptions.json").is_file())
            self.assertFalse((agent / "knowledge/stm32").exists())
            self.assertTrue((agent / "knowledge/platform/stm32/stm32-project.md").is_file())
            self.assertEqual(VERSION, (agent / "knowledge/VERSION").read_text(encoding="utf-8").strip())
            reports = sorted(
                (agent / "state/migration-reports").glob("*.json"),
                key=lambda path: path.stat().st_mtime_ns,
            )
            self.assertTrue(reports)
            report = json.loads(reports[-1].read_text(encoding="utf-8"))
            self.assertTrue(any(action["action"] == "state-kept-equal-or-higher-revision" for action in report["actions"]))
            backups = list((agent / "backups").iterdir())
            self.assertEqual(1, len(backups))
            self.assertEqual("user_config: keep\n", (backups[0] / "config.yaml").read_text(encoding="utf-8"))
            self.assertEqual('{"sentinel": true}\n', (backups[0] / "project-contract.json").read_text(encoding="utf-8"))
            agents_text = (project / "AGENTS.md").read_text(encoding="utf-8-sig")
            self.assertEqual(1, agents_text.count(BEGIN))

    def test_force_refresh_creates_backup_and_preserves_user_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "product"
            project.mkdir()
            self.run_installer(project, "--agent", "codex")
            agent = project / ".mcu-agent"
            (agent / "state/sentinel.json").write_text('{"keep": true}\n', encoding="utf-8")
            self.run_installer(project, "--agent", "pi", "--force")
            self.assertEqual('{"keep": true}\n', (agent / "state/sentinel.json").read_text(encoding="utf-8"))
            backups = list((agent / "backups").iterdir())
            self.assertTrue(backups)
            self.assertTrue(any((backup / "state/sentinel.json").is_file() for backup in backups))

    def test_installer_rejects_linked_agent_tree_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "product"; project.mkdir()
            external = root / "external-agent"; external.mkdir()
            sentinel = external / "sentinel.txt"; sentinel.write_text("untouched", encoding="utf-8")
            (project / ".mcu-agent").symlink_to(external, target_is_directory=True)
            result = self.run_installer(project, "--agent", "all", check=False)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("refusing installer operation", result.stdout.lower())
            self.assertEqual("untouched", sentinel.read_text(encoding="utf-8"))
            self.assertEqual([sentinel], list(external.iterdir()))

            project2 = root / "product2"; (project2 / ".agents").mkdir(parents=True)
            external_skills = root / "external-skills"; target = external_skills / "mcu-agent"
            target.mkdir(parents=True)
            skill_sentinel = target / "sentinel.txt"; skill_sentinel.write_text("untouched", encoding="utf-8")
            (project2 / ".agents/skills").symlink_to(external_skills, target_is_directory=True)
            result2 = self.run_installer(project2, "--agent", "all", check=False)
            self.assertNotEqual(0, result2.returncode)
            self.assertIn("linked/reparse managed path", result2.stdout.lower())
            self.assertEqual("untouched", skill_sentinel.read_text(encoding="utf-8"))
            self.assertFalse((project2 / ".mcu-agent").exists())

    def test_missing_project_and_unknown_agent_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"
            result = self.run_installer(missing, "--agent", "pi", check=False)
            self.assertNotEqual(0, result.returncode)
            project = Path(temporary) / "product"
            project.mkdir()
            result = self.run_installer(project, "--agent", "unknown", check=False)
            self.assertNotEqual(0, result.returncode)


@unittest.skipUnless(shutil.which("pwsh"), "pwsh is not available")
class PowerShellInstallerTests(unittest.TestCase):
    def test_pi_install_runs_in_sandbox_and_preserves_agents_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "product"
            project.mkdir()
            (project / "AGENTS.md").write_text("outside block\n", encoding="utf-8")
            result = subprocess.run(
                [
                    shutil.which("pwsh") or "pwsh",
                    "-NoProfile",
                    "-File",
                    str(POWERSHELL_INSTALLER),
                    "-Agent",
                    "pi",
                    "-Project",
                    str(project),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            self.assertEqual(0, result.returncode, result.stdout)
            text = (project / "AGENTS.md").read_text(encoding="utf-8-sig")
            self.assertIn("outside block", text)
            self.assertEqual(1, text.count(BEGIN))
            agent = project / ".mcu-agent"
            self.assertEqual(VERSION, (agent / "VERSION").read_text(encoding="utf-8-sig").strip())
            self.assertTrue((project / ".agents/skills/mcu-agent/SKILL.md").is_file())
            gitignore = (project / ".gitignore").read_text(encoding="utf-8-sig")
            self.assertIn(".mcu-agent/state/", gitignore)
            self.assertIn(".mcu-agent/workspaces/", gitignore)

            (agent / "state/sentinel.json").write_text('{"keep": true}\n', encoding="utf-8")
            (agent / "config.yaml").write_text("user_config: keep\n", encoding="utf-8")
            (agent / "custom/user.txt").write_text("custom", encoding="utf-8")
            update = subprocess.run(
                [
                    shutil.which("pwsh") or "pwsh", "-NoProfile", "-File", str(POWERSHELL_INSTALLER),
                    "-Agent", "all", "-Project", str(project), "-Update",
                ],
                cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            self.assertEqual(0, update.returncode, update.stdout)
            self.assertEqual('{"keep": true}\n', (agent / "state/sentinel.json").read_text(encoding="utf-8-sig"))
            self.assertEqual("user_config: keep\n", (agent / "config.yaml").read_text(encoding="utf-8-sig"))
            self.assertEqual("custom", (agent / "custom/user.txt").read_text(encoding="utf-8-sig"))
            self.assertTrue(any((backup / "state/sentinel.json").is_file() for backup in (agent / "backups").iterdir()))


if __name__ == "__main__":
    unittest.main()
