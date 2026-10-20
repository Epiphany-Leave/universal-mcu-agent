"""Machine-owned build/HIL verification and manual target evidence validation."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

DEFAULT_TIMEOUT = 900


class VerificationConfigError(ValueError):
    pass


class BuildVerifier:
    """Run a structured verification profile with ``shell=False``.

    Supported profile locations are ``verification.profiles.<tier>``,
    ``verification_profiles.<tier>`` and ``toolchain.verification_profiles.<tier>``.
    A profile contains ``argv_posix``/``argv_windows`` (or portable ``argv``),
    optional ``cwd`` and ``timeout``. Legacy ``toolchain.build_command`` is
    rejected unless ``toolchain.allow_shell_command`` is explicitly true.
    """

    def __init__(
        self,
        config_path: str | Path | None = None,
        timeout: int | None = None,
        *,
        allow_shell_command: bool = False,
    ):
        self.config_path = Path(config_path) if config_path else None
        self.timeout = timeout
        self.allow_shell_command = allow_shell_command
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        if not self.config_path or not self.config_path.is_file():
            return {}
        if yaml is None:
            raise VerificationConfigError("PyYAML is required for verification configuration")
        try:
            payload = yaml.safe_load(self.config_path.read_text(encoding="utf-8-sig")) or {}
        except Exception as exc:
            raise VerificationConfigError(f"cannot parse verification config: {exc}") from exc
        if not isinstance(payload, dict):
            raise VerificationConfigError("verification config must be a YAML object")
        try:
            from .contract_validator import validate_contract
            validate_contract(payload, "mcu-config.schema.json", label="Project config")
        except ValueError as exc:
            raise VerificationConfigError(str(exc)) from exc
        return payload

    def build_command(self) -> str | None:
        toolchain = self.config.get("toolchain") or {}
        return toolchain.get("build_command") if isinstance(toolchain, dict) else None

    def profile(self, tier: str) -> Dict[str, Any] | None:
        verification = self.config.get("verification") or {}
        toolchain = self.config.get("toolchain") or {}
        candidates = [
            (verification.get("profiles") or {}) if isinstance(verification, dict) else {},
            self.config.get("verification_profiles") or {},
            (toolchain.get("verification_profiles") or {}) if isinstance(toolchain, dict) else {},
        ]
        for profiles in candidates:
            if isinstance(profiles, dict) and isinstance(profiles.get(tier), dict):
                return dict(profiles[tier])
        direct = [
            self.config.get("verification_profile"),
            verification.get("profile") if isinstance(verification, dict) else None,
            verification if isinstance(verification, dict) and any(str(key).startswith("argv") for key in verification) else None,
            toolchain.get(f"{tier}_profile") if isinstance(toolchain, dict) else None,
            toolchain.get("verification_profile") if isinstance(toolchain, dict) else None,
        ]
        for profile in direct:
            if isinstance(profile, dict):
                declared_tier = profile.get("tier")
                if declared_tier in (None, tier):
                    return dict(profile)
        return None

    def verify(self, context: Dict[str, Any]) -> Dict[str, Any]:
        tier = str(context.get("verification_tier") or "build")
        profile_name = str(context.get("verification_profile") or tier)
        profile = self.profile(profile_name)
        if profile is not None:
            declared_tier = str(profile.get("tier") or tier)
            if declared_tier != tier:
                return {
                    "passed": False,
                    "skipped": False,
                    "tier": tier,
                    "reason": f"verification profile {profile_name!r} is declared for tier {declared_tier!r}",
                }
            if tier == "target" and not (
                profile.get("hil") is True
                or str(profile.get("kind", "")).lower() == "hil"
            ):
                return {
                    "passed": False,
                    "skipped": False,
                    "tier": "target",
                    "reason": "target verification profile must declare kind: hil",
                }
            return self._run_profile(profile, context, tier)

        command = self.build_command()
        if not command or tier != "build":
            reason = (
                "no target HIL verification profile configured"
                if tier == "target"
                else f"no verification profile configured for {profile_name!r}"
            )
            return {"passed": None, "skipped": True, "tier": tier, "reason": reason}
        toolchain = self.config.get("toolchain") or {}
        allowed_shell = self.allow_shell_command or (
            isinstance(toolchain, dict) and toolchain.get("allow_shell_command") is True
        ) or self.config.get("allow_shell_command") is True
        if not allowed_shell:
            return {
                "passed": False,
                "skipped": False,
                "tier": tier,
                "reason": "legacy toolchain.build_command rejected; configure an argv profile or pass --allow-shell-command",
                "audit": {"legacy_shell_command": True, "allowed": False},
            }
        return self._run_legacy(str(command), context, tier)

    def _run_profile(self, profile: Dict[str, Any], context: Dict[str, Any], tier: str) -> Dict[str, Any]:
        key = "argv_windows" if os.name == "nt" else "argv_posix"
        argv = profile.get(key, profile.get("argv"))
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            return {"passed": False, "skipped": False, "tier": tier, "reason": f"verification profile requires non-empty {key}/argv string array"}
        try:
            cwd = self._cwd(profile.get("cwd"), context)
            timeout = self._timeout(profile.get("timeout"))
        except VerificationConfigError as exc:
            return {"passed": False, "skipped": False, "tier": tier, "reason": str(exc)}
        return self._invoke(argv, cwd=cwd, timeout=timeout, tier=tier, shell=False, audit={"profile": True, "shell": False})

    def _run_legacy(self, command: str, context: Dict[str, Any], tier: str) -> Dict[str, Any]:
        timeout = self._timeout((self.config.get("toolchain") or {}).get("build_timeout"))
        cwd = self._cwd(None, context)
        return self._invoke(
            command,
            cwd=cwd,
            timeout=timeout,
            tier=tier,
            shell=True,
            audit={"legacy_shell_command": True, "allowed": True, "shell": True},
        )

    def _cwd(self, configured: Any, context: Dict[str, Any]) -> Path:
        project = Path(context.get("project_root") or ".").resolve()
        cwd = project if not configured else (Path(configured).resolve() if Path(configured).is_absolute() else (project / str(configured)).resolve())
        try:
            cwd.relative_to(project)
        except ValueError as exc:
            raise VerificationConfigError(f"verification cwd escapes project root: {configured}") from exc
        if not cwd.is_dir():
            raise VerificationConfigError(f"verification cwd is not a directory: {cwd}")
        return cwd

    def _timeout(self, configured: Any) -> int:
        value = self.timeout if self.timeout is not None else configured
        try:
            timeout = int(value) if value is not None else DEFAULT_TIMEOUT
        except (TypeError, ValueError) as exc:
            raise VerificationConfigError(f"invalid verification timeout: {value}") from exc
        if timeout < 1:
            raise VerificationConfigError("verification timeout must be positive")
        return timeout

    def _invoke(self, command: Any, *, cwd: Path, timeout: int, tier: str, shell: bool, audit: Dict[str, Any]) -> Dict[str, Any]:
        try:
            proc = subprocess.run(command, shell=shell, cwd=str(cwd), capture_output=True, timeout=timeout)
            raw = (proc.stdout or b"") + (proc.stderr or b"")
            try:
                output = raw.decode("utf-8")
            except UnicodeDecodeError:
                output = raw.decode("gbk", errors="replace")
        except subprocess.TimeoutExpired:
            return {"passed": False, "skipped": False, "tier": tier, "exit_code": "TIMEOUT", "output_excerpt": f"verification timed out after {timeout}s", "errors": [f"timeout after {timeout}s"], "audit": audit}
        except Exception as exc:
            return {"passed": False, "skipped": False, "tier": tier, "exit_code": "ERROR", "output_excerpt": f"verification invocation failed: {exc}", "errors": [str(exc)], "audit": audit}
        shown = command if isinstance(command, str) else list(command)
        return {"passed": proc.returncode == 0, "skipped": False, "tier": tier, "command": shown, "exit_code": proc.returncode, "output_excerpt": self._excerpt(output), "errors": self._errors(output), "audit": audit}

    @staticmethod
    def _excerpt(output: str, limit: int = 3000) -> str:
        output = output.strip()
        return output if len(output) <= limit else "...(head truncated)...\n" + output[-limit:]

    @staticmethod
    def _errors(output: str) -> List[str]:
        lines: List[str] = []
        for line in output.splitlines():
            lowered = line.lower()
            if "error:" in lowered or "fatal error" in lowered or "undefined reference" in lowered:
                lines.append(line.strip())
                if len(lines) >= 30:
                    break
        return lines


def validate_target_attestation(evidence: Dict[str, Any], *, product_root: str | Path) -> Dict[str, Any]:
    """Validate operator/device/time/steps/result and file-backed evidence hash."""
    if not isinstance(evidence, dict):
        raise VerificationConfigError("target evidence must be an object")
    for field in ("operator", "device", "timestamp", "steps", "result"):
        if not evidence.get(field):
            raise VerificationConfigError(f"target evidence missing {field}")
    if not isinstance(evidence["steps"], list) or not evidence["steps"] or not all(isinstance(step, str) and step.strip() for step in evidence["steps"]):
        raise VerificationConfigError("target evidence steps must be a non-empty string array")
    try:
        timestamp = datetime.fromisoformat(str(evidence["timestamp"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise VerificationConfigError("target evidence timestamp must be ISO-8601") from exc
    if timestamp.tzinfo is None:
        raise VerificationConfigError("target evidence timestamp must include a timezone")
    result = evidence["result"]
    passed = result is True or str(result).upper() in {"PASS", "PASSED", "SUCCESS"}
    if not passed:
        raise VerificationConfigError("target evidence result is not passing")
    item = evidence.get("evidence")
    path_value = item.get("path") if isinstance(item, dict) else evidence.get("evidence_path")
    expected_hash = item.get("sha256") if isinstance(item, dict) else evidence.get("evidence_sha256")
    if not path_value or not expected_hash:
        raise VerificationConfigError("target evidence requires evidence path and sha256")
    root = Path(product_root).resolve()
    candidate = Path(path_value)
    path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise VerificationConfigError("target evidence path escapes product root") from exc
    if not path.is_file() or path.is_symlink():
        raise VerificationConfigError(f"target evidence file is missing or a symlink: {path}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual.lower() != str(expected_hash).lower():
        raise VerificationConfigError("target evidence hash mismatch")
    validated = dict(evidence)
    validated["evidence"] = {"path": str(path.relative_to(root)), "sha256": actual}
    validated["passed"] = True
    validated["tier"] = "target"
    validated["kind"] = "manual-attestation"
    return validated
