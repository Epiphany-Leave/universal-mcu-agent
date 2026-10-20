"""Path confinement helpers shared by optional front ends.

Caller supplied artifact paths are always relative to an explicit product root.
Resolving both paths before ``relative_to`` also catches symlink escapes.
"""

from __future__ import annotations

from pathlib import Path, PureWindowsPath


class ProductPathError(ValueError):
    """Raised when an untrusted path is not confined to its product root."""


def resolve_product_path(
    product_root: str | Path,
    requested_path: str | Path,
    *,
    must_exist: bool = True,
    kind: str | None = None,
) -> Path:
    """Resolve a relative artifact path and prove it stays below ``product_root``.

    Absolute paths, any explicit ``..`` component, and symlinks resolving outside
    the root are rejected. ``kind`` may be ``"file"`` or ``"directory"``.
    """
    if product_root is None or not str(product_root).strip():
        raise ProductPathError("product_root is required")
    if requested_path is None or not str(requested_path).strip():
        raise ProductPathError("artifact path is required")

    try:
        root = Path(product_root).expanduser().resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        raise ProductPathError(f"cannot resolve product_root {product_root!s}: {exc}") from exc
    if not root.is_dir():
        raise ProductPathError(f"product_root is not a directory: {root}")

    raw_text = str(requested_path)
    raw = Path(raw_text).expanduser()
    windows_path = PureWindowsPath(raw_text)
    if raw.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise ProductPathError(f"absolute artifact paths are forbidden: {raw_text}")
    if ".." in raw.parts or ".." in windows_path.parts:
        raise ProductPathError(f"parent traversal is forbidden: {raw_text}")

    try:
        resolved = (root / raw).resolve(strict=must_exist)
        resolved.relative_to(root)
    except FileNotFoundError as exc:
        raise ProductPathError(f"artifact does not exist below product_root: {raw_text}") from exc
    except (OSError, RuntimeError) as exc:
        raise ProductPathError(f"cannot resolve artifact path {raw_text!r}: {exc}") from exc
    except ValueError as exc:
        raise ProductPathError(f"artifact escapes product_root: {raw_text}") from exc

    if kind == "file" and not resolved.is_file():
        raise ProductPathError(f"artifact is not a file: {raw_text}")
    if kind == "directory" and not resolved.is_dir():
        raise ProductPathError(f"artifact is not a directory: {raw_text}")
    if kind not in {None, "file", "directory"}:
        raise ValueError(f"unknown path kind: {kind}")
    return resolved
