"""Confined product and firmware-root layout contracts."""

from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath
from typing import Dict, List

from .config_loader import _parse_config

DEFAULT_DIRECTORIES = {
    "hardware": "hardware",
    "software": "software",
    "requirements": "project/requirements",
    "schematic": "hardware/schematic",
    "datasheet": "hardware/datasheets",
    "firmware": "",  # software/<project-name> by default
    "drivers": "",   # firmware components by default
    "docs": "software/<project-name>/docs",
    "debug": "software/<project-name>/debug",
    "production": "artifacts",
    "tools": "tools",
    "examples": "references",
    "releases": "releases",
    "archive": "archive",
}


def normalize_project_name(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip()).strip("-_").lower()
    if not normalized:
        raise ValueError("project name must contain letters or numbers")
    return normalized


def _relative_directory(value: str, *, key: str, project_name: str | None = None) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"directories.{key} must be a non-empty relative path")
    rendered = value.strip()
    if project_name:
        rendered = rendered.replace("<project-name>", project_name).replace("<项目名称>", project_name)
    # Treat backslashes as separators on every host so a Windows traversal
    # cannot become an innocuous Linux filename.
    normalized = rendered.replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute() or PureWindowsPath(rendered).is_absolute():
        raise ValueError(f"directories.{key} must not be absolute: {value}")
    if ".." in path.parts:
        raise ValueError(f"directories.{key} must not contain '..': {value}")
    return path


def _confined(root: Path, relative: Path, *, label: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes product root (including through a symlink): {relative}") from exc
    return candidate


def load_directories(config_path: str | Path | None) -> Dict[str, str]:
    """Return validated directory settings, config first and defaults second."""
    directories = dict(DEFAULT_DIRECTORIES)
    if config_path:
        for key, value in _parse_config(config_path).items():
            if key.startswith("directories.") and value not in (None, ""):
                if not isinstance(value, str):
                    raise ValueError(f"{key} must be a string")
                directories[key[len("directories."):]] = value
    # Validate every configured path, not just the five currently materialized.
    for key, value in directories.items():
        if value:
            _relative_directory(value, key=key, project_name="project")
    return directories


def project_paths(
    product_root: str | Path,
    project_name: str,
    config_path: str | Path | None = None,
) -> Dict[str, Path]:
    root = Path(product_root).resolve()
    name = normalize_project_name(project_name)
    directories = load_directories(config_path)

    software_relative = _relative_directory(directories["software"], key="software", project_name=name)
    software_root = _confined(root, software_relative, label="software root")
    firmware_setting = directories.get("firmware")
    if firmware_setting:
        project_relative = _relative_directory(firmware_setting, key="firmware", project_name=name)
    else:
        project_relative = software_relative / name
    project = _confined(root, project_relative, label="project root")

    drivers_setting = directories.get("drivers")
    if drivers_setting:
        components_relative = _relative_directory(drivers_setting, key="drivers", project_name=name)
        components_root = _confined(root, components_relative, label="components root")
    else:
        components_root = _confined(root, project_relative / "components", label="components root")

    return {
        "product_root": root,
        "hardware_root": _confined(
            root,
            _relative_directory(directories["hardware"], key="hardware", project_name=name),
            label="hardware root",
        ),
        "software_root": software_root,
        "project_root": project,
        "main_root": _confined(root, project_relative / "main", label="main root"),
        "components_root": components_root,
    }


def create_project_layout(
    product_root: str | Path,
    project_name: str,
    config_path: str | Path | None = None,
) -> Dict[str, Path]:
    """Create only canonical directories after all paths pass confinement."""
    paths = project_paths(product_root, project_name, config_path=config_path)
    paths["product_root"].mkdir(parents=True, exist_ok=True)
    for key in ("hardware_root", "software_root", "project_root", "main_root", "components_root"):
        # Recheck after each mkdir to detect a pre-existing symlink component.
        confined = _confined(paths["product_root"], paths[key].relative_to(paths["product_root"]), label=key)
        confined.mkdir(parents=True, exist_ok=True)
    # Git does not preserve empty directories.  Stable placeholders ensure a
    # claim worktree contains the canonical firmware roots before source exists.
    for key in ("main_root", "components_root"):
        placeholder = paths[key] / ".gitkeep"
        if not placeholder.exists():
            placeholder.touch()
    return paths


def validate_product_layout(
    product_root: str | Path,
    project_name: str,
    config_path: str | Path | None = None,
) -> List[str]:
    try:
        paths = project_paths(product_root, project_name, config_path=config_path)
    except ValueError as exc:
        return [str(exc)]
    errors: List[str] = []
    root = paths["product_root"]
    if (root / "firmware").exists():
        errors.append("root-level firmware/ is forbidden; use software/<project-name>")
    if (root / "drivers").exists():
        errors.append("root-level drivers/ is forbidden; place components inside the firmware project")
    expected = paths["project_root"]
    if expected.exists() and not expected.is_dir():
        errors.append(f"project root is not a directory: {expected}")
    return errors
