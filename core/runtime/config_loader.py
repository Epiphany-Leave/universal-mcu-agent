"""Load structured project configuration and derive runtime capabilities."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, List, Mapping

import yaml

CONFIG_SECTIONS = ("mcu", "hardware", "toolchain", "directories")
VERIFICATION_TIERS = {"analysis", "host", "build", "target"}

# config path -> (capability id, fact name)
MAPPING: Dict[str, tuple[str, str]] = {
    "mcu.family": ("board.identity", "family"),
    "mcu.chip": ("board.identity", "chip"),
    "mcu.arch": ("board.identity", "arch"),
    "mcu.flash": ("board.memory", "flash"),
    "mcu.psram": ("board.memory", "psram"),
    "toolchain.ide": ("platform.toolchain", "ide"),
    "toolchain.version": ("platform.toolchain", "version"),
    "hardware.screen": ("lcd.display", "panel.identity"),
    "hardware.camera": ("board.camera", "identity"),
    "hardware.audio_codec": ("board.audio", "codec"),
    "hardware.amplifier": ("board.audio", "amplifier"),
    "hardware.touch": ("board.touch", "identity"),
    "hardware.wifi": ("board.wifi", "identity"),
}


def _load_yaml(path: str | Path) -> Dict[str, Any]:
    config_path = Path(path)
    if not config_path.is_file():
        return {}
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"invalid project config {config_path}: {exc}") from exc
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("project config must be a YAML mapping")
    from .contract_validator import validate_contract
    validate_contract(payload, "mcu-config.schema.json", label="Project config")
    return payload


def _parse_config(path: str | Path) -> Dict[str, Any]:
    """Flatten supported two-level sections after parsing with ``safe_load``."""
    payload = _load_yaml(path)
    # A malformed profile is a malformed config, even when this call only needs
    # hardware facts.  This prevents silently selecting an unusable verifier.
    _verification_profiles(payload)
    result: Dict[str, Any] = {}
    for section in CONFIG_SECTIONS:
        values = payload.get(section, {})
        if values is None:
            continue
        if not isinstance(values, dict):
            raise ValueError(f"config section {section!r} must be a mapping")
        for key, value in values.items():
            if section == "toolchain" and (
                key == "verification_profiles" or key == "verification_profile" or key.endswith("_profile")
            ):
                continue
            if isinstance(value, (dict, list)):
                raise ValueError(f"config value {section}.{key} must be a scalar")
            result[f"{section}.{key}"] = value
    return result


def _is_safe_relative(value: str) -> bool:
    normalized = value.replace("\\", "/")
    path = Path(normalized)
    return bool(value) and not path.is_absolute() and not PureWindowsPath(value).is_absolute() and ".." not in path.parts


def _verification_profiles(payload: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    verification = payload.get("verification", {})
    toolchain = payload.get("toolchain", {})
    if verification is None:
        verification = {}
    if toolchain is None:
        toolchain = {}
    if not isinstance(verification, dict):
        raise ValueError("config verification must be a mapping")
    if not isinstance(toolchain, dict):
        raise ValueError("config toolchain must be a mapping")
    candidates = [
        toolchain.get("verification_profiles", {}),
        payload.get("verification_profiles", {}),
        verification.get("profiles", {}),
    ]
    raw: Dict[str, Any] = {}
    for candidate in candidates:
        if candidate is None:
            continue
        if not isinstance(candidate, dict):
            raise ValueError("verification profiles must be a mapping")
        raw.update(candidate)

    profiles: Dict[str, Dict[str, Any]] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name or not isinstance(value, dict):
            raise ValueError("each verification profile must be a named mapping")
        profile = deepcopy(value)
        tier = profile.get("tier", name if name in VERIFICATION_TIERS else None)
        if tier not in VERIFICATION_TIERS:
            raise ValueError(f"verification profile {name!r} has an invalid tier")
        profile["tier"] = tier
        command = profile.get("command")
        commands = profile.get("commands")
        if command is not None and (not isinstance(command, str) or not command.strip()):
            raise ValueError(f"verification profile {name!r}.command must be a non-empty string")
        if commands is not None and (
            not isinstance(commands, list)
            or not commands
            or not all(isinstance(item, str) and item.strip() for item in commands)
        ):
            raise ValueError(f"verification profile {name!r}.commands must be a non-empty string list")
        for argv_key in ("argv", "argv_posix", "argv_windows"):
            argv = profile.get(argv_key)
            if argv is not None and (
                not isinstance(argv, list)
                or not argv
                or not all(isinstance(item, str) and item for item in argv)
            ):
                raise ValueError(f"verification profile {name!r}.{argv_key} must be a non-empty string list")
        timeout = profile.get("timeout_seconds", profile.get("timeout"))
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
            raise ValueError(f"verification profile {name!r} timeout must be positive")
        environment = profile.get("environment", profile.get("env"))
        if environment is not None and (
            not isinstance(environment, dict)
            or not all(isinstance(key, str) and isinstance(item, (str, int, float, bool)) for key, item in environment.items())
        ):
            raise ValueError(f"verification profile {name!r} environment must be a scalar mapping")
        working_directory = profile.get("working_directory", profile.get("cwd"))
        if working_directory is not None and (
            not isinstance(working_directory, str) or not _is_safe_relative(working_directory)
        ):
            raise ValueError(f"verification profile {name!r} working directory must be a confined relative path")
        profiles[name] = profile
    return profiles


def load_verification_profiles(config_path: str | Path | None) -> Dict[str, Dict[str, Any]]:
    """Load and validate ``verification_profiles`` or ``verification.profiles``."""
    if not config_path:
        return {}
    return _verification_profiles(_load_yaml(config_path))


def load_capabilities(config_path: str | Path | None) -> List[Dict[str, Any]]:
    """Return capability records derived from non-empty config facts."""
    if not config_path:
        return []
    values = _parse_config(config_path)
    capabilities: Dict[str, Dict[str, Any]] = {}
    for key, value in values.items():
        mapping = MAPPING.get(key)
        if not mapping or value in (None, ""):
            continue
        capability_id, fact_name = mapping
        capability = capabilities.setdefault(
            capability_id,
            {"schema_version": "2.0", "id": capability_id, "state": "AVAILABLE", "facts": {}},
        )
        capability["facts"][fact_name] = {
            "state": "CONFIRMED",
            "value": value,
            "evidence_ids": [f"config:{key}"],
        }
    return list(capabilities.values())


def load_capability_store(path: str | Path | None) -> List[Dict[str, Any]]:
    if not path:
        return []
    store = Path(path)
    if not store.is_file():
        return []
    try:
        payload = json.loads(store.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    if isinstance(payload, dict):
        records = payload.get("capabilities", [])
        return records if isinstance(records, list) else []
    return payload if isinstance(payload, list) else []


def validate_capabilities(capabilities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate capability JSON Schema plus evidence-bearing fact semantics."""
    from .contract_validator import validate_contract

    seen: set[str] = set()
    for index, capability in enumerate(capabilities):
        validate_contract(capability, "capability.schema.json", label=f"Capability[{index}]")
        capability_id = capability["id"]
        if capability_id in seen:
            raise ValueError(f"duplicate capability id: {capability_id}")
        seen.add(capability_id)
        for fact_name, fact in capability.get("facts", {}).items():
            if capability.get("state") in {"UNKNOWN", "BLOCKED"} and fact.get("state") in {"CONFIRMED", "AVAILABLE"}:
                raise ValueError(
                    f"capability {capability_id} is {capability.get('state')} but {fact_name} is {fact.get('state')}"
                )
            if fact.get("state") in {"CONFIRMED", "AVAILABLE"}:
                if fact.get("value") is None:
                    raise ValueError(f"capability {capability_id}.{fact_name} has no confirmed value")
                if not fact.get("evidence_ids"):
                    raise ValueError(f"capability {capability_id}.{fact_name} has no evidence_ids")
            if fact.get("state") == "ASSUMED" and not fact.get("ear_id"):
                raise ValueError(f"assumed capability {capability_id}.{fact_name} has no ear_id")
    return capabilities


def validate_assumptions(assumptions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate task-scoped engineering assumption records at runtime boundaries."""
    from .contract_validator import validate_contract

    seen: set[str] = set()
    for index, assumption in enumerate(assumptions):
        validate_contract(
            assumption,
            "engineering-assumption-v2.schema.json",
            label=f"Engineering assumption[{index}]",
        )
        assumption_id = assumption["id"]
        if assumption_id in seen:
            raise ValueError(f"duplicate engineering assumption id: {assumption_id}")
        seen.add(assumption_id)
    return assumptions


def merge_capabilities(primary: List[Dict[str, Any]], override: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge capabilities by id; each fact in ``override`` has precedence."""
    merged: Dict[str, Dict[str, Any]] = {}
    for capability in list(primary) + list(override):
        if not isinstance(capability, dict) or not capability.get("id"):
            continue
        capability_id = capability["id"]
        if capability_id not in merged:
            merged[capability_id] = deepcopy(capability)
            merged[capability_id].setdefault("facts", {})
            continue
        target = merged[capability_id]
        for key, value in capability.items():
            if key != "facts":
                target[key] = deepcopy(value)
        facts = capability.get("facts", {})
        if isinstance(facts, dict):
            target.setdefault("facts", {}).update(deepcopy(facts))
    return list(merged.values())


def _split_inline_comment(value: str) -> tuple[str, str]:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in "\"'":
            quote = None if quote == char else char if quote is None else quote
        elif char == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
            return value[:index], value[index:]
    return value, ""


def _format_scalar(value: Any, old_value: str) -> str:
    stripped = old_value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == "'":
        return "'" + str(value).replace("'", "''") + "'"
    dumped = yaml.safe_dump(value, default_flow_style=True, allow_unicode=True).strip()
    return dumped[:-4].rstrip() if dumped.endswith("\n...") else dumped


def update_config(path: str | Path, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Replace existing scalar values without reserializing YAML.

    Indentation, key spacing, quote style, inline comments, blank lines and the
    original newline convention are retained byte-for-byte outside value spans.
    """
    config_path = Path(path)
    if not config_path.is_file():
        raise ValueError(f"config.yaml not found: {config_path}")
    raw = config_path.read_bytes()
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in text else "\n"
    had_final_newline = text.endswith(("\n", "\r"))
    lines = text.splitlines()
    changed: Dict[str, Any] = {}
    section: str | None = None

    for index, line in enumerate(lines):
        stripped = line.strip()
        if line and not line.startswith((" ", "\t")) and not stripped.startswith("#") and ":" in stripped:
            head = stripped.split(":", 1)[0].strip()
            section = head if head in CONFIG_SECTIONS else None
            continue
        if not section:
            continue
        match = re.match(r"^(\s*)([A-Za-z0-9_-]+)(\s*:\s*)(.*)$", line)
        if not match:
            continue
        key = f"{section}.{match.group(2)}"
        if key not in updates:
            continue
        before_comment, comment = _split_inline_comment(match.group(4))
        leading = before_comment[: len(before_comment) - len(before_comment.lstrip())]
        trailing = before_comment[len(before_comment.rstrip()):]
        replacement = _format_scalar(updates[key], before_comment)
        lines[index] = f"{match.group(1)}{match.group(2)}{match.group(3)}{leading}{replacement}{trailing}{comment}"
        changed[key] = updates[key]

    if changed:
        output = newline.join(lines) + (newline if had_final_newline else "")
        encoded = output.encode("utf-8")
        if has_bom:
            encoded = b"\xef\xbb\xbf" + encoded
        config_path.write_bytes(encoded)
    return changed
