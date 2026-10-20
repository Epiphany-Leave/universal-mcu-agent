"""General embedded skill routing for model Task context.

The runtime can consume two kinds of external knowledge packages:

- ``esp-dev-skill`` (repo-style): one SKILL.md + ``repos/<framework>/`` sub-skills.
  Routed by the ESP-specific table in ``esp_dev_skill.py``.
- ``aix-skills`` (folder-style): ``skills/<topic>/SKILL.md`` entries (STM32 HAL,
  sensor bring-up, RTOS debugging, ...).

This router scans ``.mcu-agent/skills/`` for both layouts, scores every skill
against the request (keyword overlap on name/description/frontmatter), and
returns the top-N bounded references. It is read-only guidance: skills can
never widen Task scope or bypass evidence/platform validation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from .esp_dev_skill import EspDevSkillRouter


class SkillRouter:
    # Chinese -> English synonym expansion so requests like "摄像头驱动" match
    # skills whose descriptions are written in English (sensor, camera, i2c...).
    SYNONYMS = {
        "摄像头": ["camera", "sensor", "image", "vision", "isp", "ov3660", "ov2640", "ov5640"],
        "相机": ["camera", "image", "vision"],
        "传感器": ["sensor", "driver", "i2c", "spi", "register"],
        "屏幕": ["lcd", "display", "lvgl", "ui", "panel"],
        "音频": ["audio", "i2s", "codec", "es8389", "speaker", "mic"],
        "网络": ["wifi", "net", "mqtt", "tcp", "websocket", "http"],
        "蓝牙": ["ble", "bluetooth", "nimble"],
        "调试": ["debug", "debugging", "trace"],
        "中断": ["interrupt", "isr", "irq", "nvic"],
        "存储": ["flash", "littlefs", "fatfs", "storage", "sdmmc", "nvs"],
        "定时器": ["timer", "pwm", "watchdog"],
        "电机": ["motor", "foc", "pwm", "encoder"],
        "电池": ["battery", "charger", "fuel", "gauge", "adc"],
        "校准": ["calibrat", "offset", "scale"],
    }

    def __init__(self, skill_root: str | Path | None = None, *, product_root: str | Path | None = None):
        self.explicit_root = Path(skill_root).expanduser() if skill_root else None
        self.product_root = Path(product_root).resolve() if product_root else None

    def _scan_roots(self) -> List[Path]:
        roots: List[Path] = []
        if self.explicit_root:
            roots.append(self.explicit_root)
        if self.product_root:
            roots.append(self.product_root / ".mcu-agent" / "skills")
        return roots

    def discover(self) -> List[Dict[str, Any]]:
        """List every discoverable skill entry as {id, path, name, description, keywords}."""
        entries: List[Dict[str, Any]] = []
        seen: set[Path] = set()
        for root in self._scan_roots():
            if not root.is_dir():
                continue
            # esp-dev-skill repo layout: <root>/repos/<framework>/SKILL.md
            esp_root = root / "esp-dev-skill"
            if (esp_root / "SKILL.md").is_file():
                repo_dir = esp_root / "repos"
                if repo_dir.is_dir():
                    for repo in sorted(repo_dir.iterdir()):
                        skill_file = repo / "SKILL.md"
                        if skill_file.is_file() and skill_file not in seen:
                            seen.add(skill_file)
                            frontmatter = self._frontmatter(skill_file)
                            entries.append({
                                "id": f"esp:{repo.name}",
                                "path": str(skill_file),
                                "name": frontmatter.get("name") or repo.name,
                                "description": frontmatter.get("description", ""),
                                "kind": "esp-repo",
                            })
            # aix-skills folder layout: <root>/<pkg>/skills/<topic>/SKILL.md
            for package in sorted(root.iterdir()):
                if package.name == "esp-dev-skill":
                    continue
                skills_dir = package / "skills"
                if not skills_dir.is_dir():
                    continue
                for skill in sorted(skills_dir.iterdir()):
                    skill_file = skill / "SKILL.md"
                    if skill_file.is_file() and skill_file not in seen:
                        seen.add(skill_file)
                        frontmatter = self._frontmatter(skill_file)
                        entries.append({
                            "id": f"{package.name}:{skill.name}",
                            "path": str(skill_file),
                            "name": frontmatter.get("name") or skill.name,
                            "description": frontmatter.get("description", ""),
                            "kind": "skill",
                        })
        return entries

    def route(self, request: str, *, platform: str | None = None, limit: int = 5) -> Dict[str, Any]:
        entries = self.discover()
        if not entries:
            return {"available": False, "references": [], "matched": []}
        terms = self._terms(f"{request} {platform or ''}")
        scored = []
        for entry in entries:
            haystack = f"{entry['name']} {entry['description']}".lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                scored.append((score, entry))
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        selected = [entry for _, entry in scored[:limit]]
        return {
            "available": bool(selected),
            "references": [
                {
                    "kind": entry["kind"],
                    "id": entry["id"],
                    "name": entry["name"],
                    "path": entry["path"],
                }
                for entry in selected
            ],
            "matched": [entry["id"] for entry in selected],
            "policy": (
                "Read the listed SKILL.md files as implementation guidance only. "
                "Keep the Task's allowed_roots, evidence gates, and verification "
                "contract authoritative."
            ),
        }

    @staticmethod
    def _terms(text: str) -> List[str]:
        lowered = text.lower()
        terms = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{2,}", lowered)
        stop = {
            "write", "implement", "create", "build", "make", "use", "for", "the",
            "with", "and", "driver", "驱动", "开发", "实现", "写", "一个", "从",
            "提取", "信息", "加上", "给", "做", "修改", "增加", "添加",
        }
        expanded = set()
        for term in terms:
            if len(term) < 2 or term in stop:
                continue
            expanded.add(term)
            for key, synonyms in SkillRouter.SYNONYMS.items():
                if key in term:
                    expanded.update(synonyms)
        return list(expanded)

    @staticmethod
    def _frontmatter(path: Path) -> Dict[str, str]:
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
        result: Dict[str, str] = {}
        if not match:
            return result
        for line in match.group(1).splitlines():
            kv = re.match(r"^(\w+)\s*:\s*(.*)$", line)
            if kv:
                result[kv.group(1)] = kv.group(2).strip().strip("\"'")
        return result


def route_skills(
    request: str,
    *,
    product_root: str | Path | None = None,
    skill_root: str | Path | None = None,
    platform: str | None = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """Combine ESP-specific routing (esp-dev-skill) with general skill routing."""
    esp = EspDevSkillRouter(skill_root, product_root=product_root).route(request, platform=platform)
    general = SkillRouter(skill_root, product_root=product_root).route(
        request, platform=platform, limit=limit
    )
    references = list(esp.get("references", [])) + list(general.get("references", []))
    return {
        "available": bool(references),
        "esp_dev_skill": esp,
        "general_skills": general,
        "references": references,
        "policy": (
            "Read the listed skill files as implementation guidance only. Keep "
            "the Task's allowed_roots, evidence gates, and verification contract "
            "authoritative."
        ),
    }
