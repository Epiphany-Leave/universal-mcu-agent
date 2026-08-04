"""Small, dependency-free reader for paths in .mcu-agent/config.yaml."""

from pathlib import Path
from typing import Dict, Iterable, List


DEFAULT_DIRECTORIES = {
    "requirements": "project/requirements",
    "schematic": "hardware/schematic",
    "datasheet": "hardware/datasheets",
    "production": "artifacts",
    "examples": "references",
}

LEGACY_DIRECTORIES = {
    "requirements": ["00_项目管理"],
    "schematic": ["01_硬件设计/原理图", "01_硬件设计/00_原理图", "01_硬件设计", "02_Hardware"],
    "datasheet": ["01_硬件设计/数据手册"],
    "production": ["05_生产资料"],
    "examples": ["07_参考资料"],
}


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def load_directories(project_path: Path) -> Dict[str, str]:
    """Read the flat `directories` mapping without requiring PyYAML."""
    paths = dict(DEFAULT_DIRECTORIES)
    config_path = project_path / ".mcu-agent" / "config.yaml"
    if not config_path.is_file():
        return paths

    in_directories = False
    try:
        for raw_line in config_path.read_text(encoding="utf-8-sig").splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(raw_line) - len(raw_line.lstrip())
            if indent == 0:
                in_directories = stripped == "directories:"
                continue
            if not in_directories or indent < 2 or ":" not in stripped:
                continue
            key, value = stripped.split(":", 1)
            value = value.split(" #", 1)[0].strip()
            if value:
                paths[key.strip()] = _unquote(value)
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read {config_path}: {exc}") from exc
    return paths


def configured_path(project_path: Path, key: str) -> Path:
    value = load_directories(project_path).get(key, DEFAULT_DIRECTORIES.get(key, key))
    value = value.replace("<project-name>", project_path.name.lower())
    candidate = Path(value)
    return candidate if candidate.is_absolute() else project_path / candidate


def candidate_paths(project_path: Path, key: str) -> List[Path]:
    """Return configured/default path first, then existing legacy paths."""
    result = [configured_path(project_path, key)]
    for relative in LEGACY_DIRECTORIES.get(key, []):
        candidate = project_path / relative
        if candidate not in result:
            result.append(candidate)
    return result


def existing_paths(project_path: Path, keys: Iterable[str]) -> List[Path]:
    result = []
    for key in keys:
        result.extend(path for path in candidate_paths(project_path, key) if path.exists())
    return result
