"""Product and firmware-root layout contracts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List


def normalize_project_name(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip()).strip("-_").lower()
    if not normalized:
        raise ValueError("project name must contain letters or numbers")
    return normalized


def project_paths(product_root: str | Path, project_name: str) -> Dict[str, Path]:
    root = Path(product_root)
    name = normalize_project_name(project_name)
    project = root / "software" / name
    return {
        "product_root": root,
        "hardware_root": root / "hardware",
        "software_root": root / "software",
        "project_root": project,
        "main_root": project / "main",
        "components_root": project / "components",
    }


def create_project_layout(product_root: str | Path, project_name: str) -> Dict[str, Path]:
    """Create only the canonical product and firmware-project directories."""
    paths = project_paths(product_root, project_name)
    for key in ("hardware_root", "software_root", "project_root", "main_root", "components_root"):
        paths[key].mkdir(parents=True, exist_ok=True)
    return paths


def validate_product_layout(product_root: str | Path, project_name: str) -> List[str]:
    paths = project_paths(product_root, project_name)
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
