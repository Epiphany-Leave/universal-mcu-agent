"""ESP-IDF application and component inclusion contract."""

from __future__ import annotations

import json
import re
from pathlib import Path
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
        if cmake.is_file():
            text = cmake.read_text(encoding="utf-8", errors="ignore")
            if "project.cmake" in text or "ESP_PLATFORM" in text:
                evidence.append("CMakeLists.txt")
        if (root / "sdkconfig").is_file():
            evidence.append("sdkconfig")
        if (root / "main" / "CMakeLists.txt").is_file():
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

        top_text = cmake.read_text(encoding="utf-8", errors="ignore") if cmake.is_file() else ""
        if not cmake.is_file():
            errors.append("missing project CMakeLists.txt")
        elif not PROJECT_RE.search(top_text):
            errors.append("top-level CMakeLists.txt does not declare project()")
        elif "project.cmake" not in top_text and "ESP_PLATFORM" not in top_text:
            errors.append("top-level CMakeLists.txt does not load the ESP-IDF project contract")
        if not main_cmake.is_file():
            errors.append("missing main/CMakeLists.txt")
        elif not REGISTER_RE.search(main_cmake.read_text(encoding="utf-8", errors="ignore")):
            errors.append("main/CMakeLists.txt does not call idf_component_register()")

        target = None
        if sdkconfig.is_file():
            match = TARGET_RE.search(sdkconfig.read_text(encoding="utf-8", errors="ignore"))
            target = match.group(1).strip() if match else None
            if not target:
                warnings.append("sdkconfig does not declare CONFIG_IDF_TARGET")
        else:
            message = "sdkconfig is absent; target is not configured yet"
            (errors if build_succeeded else warnings).append(message)
        if build_succeeded and not target:
            errors.append("successful build result has no verified ESP-IDF target")

        compile_files = self._compile_files(compile_commands or root / "build" / "compile_commands.json")
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
                candidate = Path(value)
                if not candidate.is_absolute() and record.get("directory"):
                    candidate = Path(record["directory"]) / candidate
                files.add(candidate.resolve())
        return files

    @staticmethod
    def _component_roots(root: Path, top_text: str) -> List[Path]:
        result: List[Path] = []
        default_root = root / "components"
        if default_root.is_dir():
            result.extend(path.resolve() for path in default_root.iterdir() if path.is_dir())
        for match in EXTRA_COMPONENT_RE.finditer(top_text):
            for token in re.findall(r'"([^"]+)"|([^\s"]+)', match.group(1)):
                value = token[0] or token[1]
                if not value or "$" in value:
                    continue
                candidate = Path(value)
                candidate = candidate if candidate.is_absolute() else root / candidate
                if candidate.is_dir():
                    result.append(candidate.resolve())
        unique = []
        seen = set()
        for path in result:
            if path not in seen:
                seen.add(path)
                unique.append(path)
        return unique

    @staticmethod
    def _validate_component(
        root: Path,
        component_root: Path,
        compile_files: Set[Path] | None,
        *,
        component_name: str | None = None,
    ) -> Dict[str, Any]:
        errors: List[str] = []
        cmake = component_root / "CMakeLists.txt"
        if not cmake.is_file():
            errors.append(f"component {component_name or component_root.name} is missing CMakeLists.txt")
            registered = False
        else:
            registered = bool(REGISTER_RE.search(cmake.read_text(encoding="utf-8", errors="ignore")))
            if not registered:
                errors.append(f"component {component_name or component_root.name} does not call idf_component_register()")

        sources = [path.resolve() for suffix in ("*.c", "*.cc", "*.cpp", "*.S") for path in component_root.rglob(suffix)]
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
