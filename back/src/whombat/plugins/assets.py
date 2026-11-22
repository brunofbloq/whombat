"""Confined resolution of plugin static assets."""

from __future__ import annotations

from pathlib import Path

from whombat.core import files
from whombat.exceptions import PathOutsideRootError

__all__ = ["AssetNotFound", "resolve_plugin_asset"]


class AssetNotFound(ValueError):
    """Raised when an asset is missing or escapes its plugin root."""


def resolve_plugin_asset(root: Path, relative: str) -> Path:
    """Return a regular file inside ``root`` or raise ``AssetNotFound``."""
    if not relative:
        raise AssetNotFound("Asset not found")

    candidate = Path(relative.replace("\\", "/"))
    if candidate.is_absolute() or candidate.drive:
        raise AssetNotFound("Asset not found")
    if any(part in ("..", ".") for part in candidate.parts):
        raise AssetNotFound("Asset not found")

    try:
        target = files.resolve_within_root(root / candidate, root)
    except (PathOutsideRootError, OSError):
        raise AssetNotFound("Asset not found") from None

    if not target.is_file():
        raise AssetNotFound("Asset not found")
    return target
