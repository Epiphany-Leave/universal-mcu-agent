"""Optional router for the external ``esp-dev-skill`` knowledge package.

The package is deliberately treated as a read-only knowledge provider.  It can
suggest grounded reference files for a Task, but it cannot change lifecycle
state, widen the edit scope, or replace platform validation.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List


class EspDevSkillError(ValueError):
    """Raised when an explicitly selected ESP skill root is invalid."""


class EspDevSkillRouter:
    """Resolve a bounded set of ESP skill references for a model Task."""

    DEFAULT_RELATIVE_ROOTS = (
        Path(".mcu-agent") / "skills" / "esp-dev-skill",
        Path("integrations") / "esp-dev-skill",
    )

    # Keep routing conservative.  The external package remains authoritative
    # for the actual recipe content; this table only chooses likely entrypoints.
    ROUTES = (
        (re.compile(r"qmc\s*-?\s*5883|i2c|磁力计", re.I), "esp-idf", ("i2c_master",)),
        (re.compile(r"freertos|任务|调度|scheduler", re.I), "esp-idf", ("freertos_task",)),
        (re.compile(r"lvgl|ui|界面|显示|lcd|屏幕|animation|动画", re.I), "esp-brookesia", ()),
        (re.compile(r"mqtt", re.I), "esp-mqtt", ("mqtt",)),
        (re.compile(r"zigbee", re.I), "esp-zigbee-sdk", ()),
        (re.compile(r"matter", re.I), "esp-matter", ()),
        (re.compile(r"thread", re.I), "esp-thread-br", ()),
        (re.compile(r"nimble", re.I), "esp-nimble", ()),
        (re.compile(r"ble|蓝牙", re.I), "esp-idf", ("ble_peripheral",)),
        (re.compile(r"wifi|wi-fi|无线", re.I), "esp-idf", ("wifi_sta",)),
        (re.compile(r"arduino", re.I), "arduino-esp32", ()),
        (re.compile(r"esp8266", re.I), "ESP8266_RTOS_SDK", ()),
    )

    def __init__(self, skill_root: str | Path | None = None, *, product_root: str | Path | None = None):
        self.explicit_root = Path(skill_root).expanduser() if skill_root else None
        self.product_root = Path(product_root).resolve() if product_root else None

    def locate(self) -> Path | None:
        candidates: List[Path] = []
        if self.explicit_root:
            candidates.append(self.explicit_root)
        env_root = os.environ.get("MCU_ESP_DEV_SKILL_ROOT")
        if env_root:
            candidates.append(Path(env_root).expanduser())
        if self.product_root:
            candidates.extend(self.product_root / relative for relative in self.DEFAULT_RELATIVE_ROOTS)
        candidates.append(Path(__file__).resolve().parents[2] / "integrations" / "esp-dev-skill")

        for candidate in candidates:
            root = candidate.resolve()
            if (root / "SKILL.md").is_file() and (root / "repos").is_dir():
                return root
        if self.explicit_root:
            raise EspDevSkillError(f"esp-dev-skill root is invalid or missing SKILL.md: {self.explicit_root}")
        return None

    def describe(self) -> Dict[str, Any]:
        root = self.locate()
        if root is None:
            return {"available": False, "provider": "esp-dev-skill"}
        skill_text = (root / "SKILL.md").read_text(encoding="utf-8-sig")
        return {
            "available": True,
            "provider": "esp-dev-skill",
            "root": str(root),
            "version": self._frontmatter_value(skill_text, "version") or "unknown",
            "license_declared": self._frontmatter_value(skill_text, "license") or "unknown",
            "license_file_present": any((root / name).is_file() for name in ("LICENSE", "LICENSE.md", "NOTICE")),
            "source_manifest": str(root / "resources" / "repo_index.md"),
        }

    def route(self, request: str, *, platform: str | None = None) -> Dict[str, Any]:
        root = self.locate()
        if root is None:
            return {"available": False, "provider": "esp-dev-skill", "references": []}

        repo, recipe_names = self._select_route(request, platform)
        repo_root = root / "repos" / repo
        if not repo_root.is_dir():
            raise EspDevSkillError(f"routed ESP skill repository does not exist: {repo}")
        references: List[Dict[str, str]] = [
            {"kind": "subskill", "path": str(repo_root / "SKILL.md")},
        ]
        for recipe in recipe_names:
            recipe_path = repo_root / "recipes" / f"{recipe}.md"
            if recipe_path.is_file():
                references.append({"kind": "recipe", "path": str(recipe_path)})
        for name in ("api_reference.md", "config_reference.md", "pitfalls.md"):
            path = repo_root / "resources" / name
            if path.is_file():
                references.append({"kind": "resource", "path": str(path)})

        return {
            "available": True,
            "provider": "esp-dev-skill",
            "root": str(root),
            "repository": repo,
            "references": references,
            "read_order": ["subskill", "recipe", "resource"],
            "policy": (
                "Read these files as implementation guidance only. Keep the Task's "
                "allowed_roots, evidence gates, and verification contract authoritative."
            ),
        }

    def _select_route(self, request: str, platform: str | None) -> tuple[str, tuple[str, ...]]:
        text = request or ""
        lowered = text.lower()
        normalized_platform = platform.lower() if platform else ""
        if normalized_platform in {"arduino", "arduino-esp32"}:
            default = "arduino-esp32"
        elif normalized_platform in {"esp8266", "esp8266-rtos-sdk"}:
            default = "ESP8266_RTOS_SDK"
        else:
            default = "esp-idf"
        for pattern, repo, recipes in self.ROUTES:
            if pattern.search(text):
                if repo == "arduino-esp32" and "arduino" not in lowered:
                    continue
                if default == "arduino-esp32" and repo == "esp-idf":
                    repo = default
                    mapped_recipe = {
                        "i2c_master": "i2c_master_slave",
                        "freertos_task": "freertos_tasks",
                        "ble_peripheral": "ble",
                        "wifi_sta": "wifi_sta_ap",
                    }.get(recipes[0], recipes[0]) if recipes else None
                    recipes = (mapped_recipe,) if mapped_recipe else ()
                return repo, recipes
        return default, ()

    @staticmethod
    def _frontmatter_value(text: str, key: str) -> str | None:
        match = re.search(rf"^\s*{re.escape(key)}:\s*[\"']?([^\"'\r\n]+)", text, re.MULTILINE)
        return match.group(1).strip() if match else None


def route_esp_skill(request: str, *, product_root: str | Path | None = None,
                    skill_root: str | Path | None = None, platform: str | None = None) -> Dict[str, Any]:
    """Convenience function used by CLI/MCP integrations."""
    return EspDevSkillRouter(skill_root, product_root=product_root).route(request, platform=platform)
