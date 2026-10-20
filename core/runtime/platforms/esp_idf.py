"""ESP-IDF application and component inclusion contract."""

from __future__ import annotations

import json
import platform as host_platform
import re
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterable, List, Set

from .base import PlatformAdapter


REGISTER_RE = re.compile(r"idf_component_register\s*\(", re.IGNORECASE)
TARGET_RE = re.compile(r'^CONFIG_IDF_TARGET="?([^"\r\n]+)"?', re.MULTILINE)
PROJECT_RE = re.compile(r"\bproject\s*\(", re.IGNORECASE)
EXTRA_COMPONENT_RE = re.compile(r"EXTRA_COMPONENT_DIRS\s+([^\)]+)\)", re.IGNORECASE | re.DOTALL)


class EspIdfAdapter(PlatformAdapter):
    name = "esp-idf"

    def detect(self, root: Path) -> Dict[str, Any]:
        root = root.resolve()
        cmake = root / "CMakeLists.txt"
        evidence: List[str] = []
        if self._confined_file(cmake, root):
            text = cmake.read_text(encoding="utf-8", errors="ignore")
            if "project.cmake" in text or "ESP_PLATFORM" in text:
                evidence.append("CMakeLists.txt")
        if self._confined_file(root / "sdkconfig", root):
            evidence.append("sdkconfig")
        if self._confined_file(root / "main" / "CMakeLists.txt", root):
            evidence.append("main/CMakeLists.txt")
        return {
            "platform": self.name if evidence else None,
            "root": str(root),
            "kind": "application" if evidence else "unknown",
            "confidence": "high" if len(evidence) >= 2 else "medium" if evidence else "low",
            "evidence": evidence,
        }

    def validate(
        self,
        root: Path,
        *,
        compile_commands: Path | None = None,
        build_succeeded: bool | None = None,
        expected_components: Iterable[str] = (),
    ) -> Dict[str, Any]:
        root = root.resolve()
        errors: List[str] = []
        warnings: List[str] = []
        cmake = root / "CMakeLists.txt"
        sdkconfig = root / "sdkconfig"
        main_cmake = root / "main" / "CMakeLists.txt"

        top_text = cmake.read_text(encoding="utf-8", errors="ignore") if self._confined_file(cmake, root) else ""
        if not self._confined_file(cmake, root):
            errors.append("missing or escaping project CMakeLists.txt")
        elif not PROJECT_RE.search(top_text):
            errors.append("top-level CMakeLists.txt does not declare project()")
        elif "project.cmake" not in top_text and "ESP_PLATFORM" not in top_text:
            errors.append("top-level CMakeLists.txt does not load the ESP-IDF project contract")
        if not self._confined_file(main_cmake, root):
            errors.append("missing or escaping main/CMakeLists.txt")
        elif not REGISTER_RE.search(main_cmake.read_text(encoding="utf-8", errors="ignore")):
            errors.append("main/CMakeLists.txt does not call idf_component_register()")

        target = None
        if self._confined_file(sdkconfig, root):
            match = TARGET_RE.search(sdkconfig.read_text(encoding="utf-8", errors="ignore"))
            target = match.group(1).strip() if match else None
            if not target:
                warnings.append("sdkconfig does not declare CONFIG_IDF_TARGET")
        else:
            message = "sdkconfig is absent; target is not configured yet"
            (errors if build_succeeded else warnings).append(message)
        if build_succeeded and not target:
            errors.append("successful build result has no verified ESP-IDF target")

        compile_path = Path(compile_commands) if compile_commands is not None else root / "build" / "compile_commands.json"
        resolved_compile_path = compile_path.resolve()
        if compile_path.exists() and not self._is_within(resolved_compile_path, root):
            errors.append("compile_commands.json escapes project root")
            compile_files: Set[Path] | None = set()
        else:
            compile_files = self._compile_files(compile_path)
            if compile_files is not None:
                compile_files = {path for path in compile_files if self._is_within(path, root)}
        component_roots = self._component_roots(root, top_text)
        components = []
        for component_root in component_roots:
            components.append(self._validate_component(root, component_root, compile_files))
        components.append(self._validate_component(root, root / "main", compile_files, component_name="main"))
        discovered_names = {component["name"] for component in components}
        for expected in expected_components:
            if expected not in discovered_names:
                message = f"expected component {expected} was not discovered"
                components.append({
                    "name": expected,
                    "root": None,
                    "registered": False,
                    "included": False,
                    "inclusion_evidence": "not discovered",
                    "errors": [message],
                })
        errors.extend(error for component in components for error in component["errors"])

        if build_succeeded is True and any(not item["included"] for item in components):
            errors.append("build process passed but one or more declared components were not included")

        return {
            "platform": self.name,
            "scope": str(root),
            "target": target,
            "status": "PASS" if not errors else "FAIL",
            "build_succeeded": build_succeeded,
            "components": components,
            "errors": errors,
            "warnings": warnings,
        }

    @staticmethod
    def _is_wsl(*, system: str | None = None, release: str | None = None) -> bool:
        """Pure-testable WSL detection; native Windows is never treated as WSL."""
        system_name = system if system is not None else host_platform.system()
        release_name = release if release is not None else host_platform.release()
        return system_name == "Linux" and "microsoft" in release_name.lower()

    @staticmethod
    def _normalize_windows_path(value: str, *, is_wsl: bool | None = None) -> str:
        """Convert drive paths only when compile metadata is consumed in WSL."""
        wsl = EspIdfAdapter._is_wsl() if is_wsl is None else is_wsl
        match = re.match(r"^([A-Za-z]):[\\/](.*)$", value)
        if wsl and match:
            return f"/mnt/{match.group(1).lower()}/{match.group(2).replace(chr(92), '/')}"
        return value

    @staticmethod
    def _compile_files(path: Path) -> Set[Path] | None:
        if not path.is_file():
            return None
        try:
            records = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return set()
        files = set()
        for record in records if isinstance(records, list) else []:
            value = record.get("file") if isinstance(record, dict) else None
            if value:
                candidate = Path(EspIdfAdapter._normalize_windows_path(value))
                if not candidate.is_absolute() and record.get("directory"):
                    base = Path(EspIdfAdapter._normalize_windows_path(record["directory"]))
                    candidate = base / candidate
                files.add(candidate.resolve())
        return files

    @staticmethod
    def _component_roots(root: Path, top_text: str) -> List[Path]:
        root = root.resolve()
        result: List[Path] = []
        default_root = root / "components"
        if default_root.is_dir():
            for path in default_root.iterdir():
                resolved = path.resolve()
                if path.is_dir() and EspIdfAdapter._is_within(resolved, root):
                    result.append(resolved)
        for match in EXTRA_COMPONENT_RE.finditer(top_text):
            for token in re.findall(r'"([^"]+)"|([^\s"]+)', match.group(1)):
                value = token[0] or token[1]
                if not value or "$" in value:
                    continue
                normalized = value.replace("\\", "/")
                relative = Path(normalized)
                # Registry/project metadata is never allowed to nominate an
                # absolute, parent-traversing, or symlink-escaping component.
                if relative.is_absolute() or PureWindowsPath(value).is_absolute() or ".." in relative.parts:
                    continue
                candidate = (root / relative).resolve()
                if candidate.is_dir() and EspIdfAdapter._is_within(candidate, root):
                    result.append(candidate)
        unique = []
        seen = set()
        for path in result:
            if path not in seen:
                seen.add(path)
                unique.append(path)
        return unique

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _confined_file(path: Path, root: Path) -> bool:
        return path.is_file() and EspIdfAdapter._is_within(path.resolve(), root.resolve())

    @staticmethod
    def _validate_component(
        root: Path,
        component_root: Path,
        compile_files: Set[Path] | None,
        *,
        component_name: str | None = None,
    ) -> Dict[str, Any]:
        root = root.resolve()
        component_root = component_root.resolve()
        errors: List[str] = []
        if not EspIdfAdapter._is_within(component_root, root):
            name = component_name or component_root.name
            return {
                "name": name,
                "root": None,
                "registered": False,
                "included": False,
                "inclusion_evidence": "rejected path outside project root",
                "errors": [f"component {name} escapes project root"],
            }
        cmake = component_root / "CMakeLists.txt"
        if not cmake.is_file():
            errors.append(f"component {component_name or component_root.name} is missing CMakeLists.txt")
            registered = False
        else:
            registered = bool(REGISTER_RE.search(cmake.read_text(encoding="utf-8", errors="ignore")))
            if not registered:
                errors.append(f"component {component_name or component_root.name} does not call idf_component_register()")

        sources = [
            path.resolve()
            for suffix in ("*.c", "*.cc", "*.cpp", "*.S")
            for path in component_root.rglob(suffix)
            if EspIdfAdapter._is_within(path.resolve(), component_root)
        ]
        if compile_files is None:
            included = registered
            inclusion_evidence = "structure-only; compile_commands.json unavailable"
        else:
            included_sources = [path for path in sources if path in compile_files]
            included = registered and (not sources or bool(included_sources))
            inclusion_evidence = f"{len(included_sources)}/{len(sources)} sources in compile_commands.json"
            if registered and sources and not included_sources:
                errors.append(f"component {component_name or component_root.name} has no source in compile_commands.json")
        try:
            relative_root = str(component_root.relative_to(root))
        except ValueError:
            relative_root = str(component_root)
        return {
            "name": component_name or component_root.name,
            "root": relative_root,
            "registered": registered,
            "included": included,
            "inclusion_evidence": inclusion_evidence,
            "errors": errors,
        }
