"""Subprocess acceptance test for the canonical product/WP/task CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "core" / "runtime" / "cli.py"


class CanonicalCliE2ETests(unittest.TestCase):
    def run_cli(self, *arguments: str, expect: int = 0) -> dict:
        command_path = getattr(self, "cli_path", CLI)
        completed = subprocess.run(
            [sys.executable, str(command_path), *map(str, arguments)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        if completed.returncode != expect:
            self.fail(
                f"CLI returned {completed.returncode}, expected {expect}\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )
        return json.loads(completed.stdout) if completed.stdout.strip() else {}

    def test_canonical_claim_id_flow_across_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            product = Path(temporary) / "product"
            project = product / "software" / "demo"
            project.mkdir(parents=True)
            if os.name == "nt":
                powershell = shutil.which("pwsh")
                if not powershell:
                    self.skipTest("pwsh is required for native Windows E2E")
                installer = [
                    powershell, "-NoProfile", "-File", str(ROOT / "install.ps1"),
                    "-Project", str(product), "-Agent", "all",
                ]
            else:
                installer = [
                    "bash", str(ROOT / "install.sh"), "--project", str(product), "--agent", "all",
                ]
            installed = subprocess.run(
                installer, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, timeout=120,
            )
            self.assertEqual(0, installed.returncode, installed.stdout)
            agent = product / ".mcu-agent"
            self.cli_path = agent / "runtime" / "cli.py"
            python_argv = json.dumps([sys.executable, "-c", "print('verified')"])
            (agent / "config.yaml").write_text(
                "verification:\n"
                "  profiles:\n"
                "    build:\n"
                "      tier: build\n"
                f"      argv_posix: {python_argv}\n"
                f"      argv_windows: {python_argv}\n"
                "      cwd: .\n"
                "      timeout: 30\n",
                encoding="utf-8",
            )

            layout = self.run_cli(
                "project-layout", str(product), "demo", "--create",
                "--platform", "stm32", "--chip", "STM32G431CBU6",
            )
            self.assertEqual(str(project.resolve()), layout["paths"]["project_root"])
            (project / "demo.ioc").write_text(
                "ProjectManager.DeviceId=STM32G431CBU6\n", encoding="utf-8"
            )
            evidence = product / "docs"
            evidence.mkdir()
            for role, filename in (
                ("requirements.structured", "requirements.json"),
                ("device.project.capabilities", "device.json"),
                ("hardware.peripheral_connections", "connections.json"),
            ):
                path = evidence / filename
                path.write_text(role, encoding="utf-8")
                registered = self.run_cli(
                    "artifact", "register", str(product), "--role", role,
                    "--path", path.relative_to(product), "--scope", "project",
                )
                self.assertEqual(role, registered["artifact"]["role"])

            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=product, check=True)
            subprocess.run(["git", "add", "."], cwd=product, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=product, check=True)

            session = self.run_cli(
                "session-bootstrap", str(product), "--agent", "pi", "--project-root", str(project)
            )["session_id"]
            plan = self.run_cli(
                "plan", "implement feature", "--product-root", str(product),
                "--project-root", str(project), "--session-id", session,
            )
            package = plan["work_packages"][0]
            task = package["tasks"][0]
            claim = self.run_cli(
                "claim-task", str(product), package["work_package_id"], task["id"],
                "--session-id", session, "--project-root", str(project), "--workspace",
            )
            self.assertTrue(claim["claim_id"])
            self.assertIn("context", claim)

            workspace_project = Path(claim["project_root"])
            (workspace_project / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            self.assertFalse((project / "main.c").exists())
            result_file = Path(temporary) / "model-result.json"
            result_file.write_text(
                json.dumps({"summary": "implemented", "changed_files": []}), encoding="utf-8"
            )
            final = self.run_cli(
                "finalize-task", str(product), package["work_package_id"], task["id"],
                str(result_file), "--claim-id", claim["claim_id"],
            )
            self.assertEqual("DONE", final["state"], final)
            self.assertEqual(["main.c"], final["execution"]["changed_files"])
            self.assertTrue(final["execution"]["verification"]["passed"])
            promoted = self.run_cli(
                "promote-task", str(product), package["work_package_id"], task["id"],
                "--claim-id", claim["claim_id"],
            )
            self.assertTrue((project / "main.c").is_file())
            self.assertTrue(Path(promoted["audit_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
