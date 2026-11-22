"""Discover plugins declared as manifests.

Sources, in order:

1. Python entry points in the ``whombat.plugins`` group whose target is a
   manifest path or a mapping with the manifest fields.
2. A per-user plugins directory, ``<plugins_dir>/<id>/plugin.toml``
   (``manifest.json`` also accepted).
3. Legacy ``whombat_*`` modules imported in-process (deprecated).

Discovery never raises for a single bad plugin: the failure becomes an
entry with an ``error`` so the UI can report it.
"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from whombat.plugins import legacy
from whombat.plugins.manifest import (
    ManifestError,
    PluginManifest,
    load_manifest,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "MANIFEST_NAMES",
    "PluginSource",
    "discover_plugins",
    "plugins_dir_for",
]

ENTRY_POINT_GROUP = "whombat.plugins"
MANIFEST_NAMES = ("plugin.toml", "manifest.json")


@dataclass(frozen=True)
class PluginSource:
    """A discovered plugin, valid or not."""

    id: str
    root: Path | None = None
    manifest: PluginManifest | None = None
    entry_point: str | None = None
    module: ModuleType | None = None
    legacy: bool = False
    error: str | None = None


def plugins_dir_for(settings) -> Path:
    """Return the directory scanned for manifest plugins."""
    configured = getattr(settings, "plugins_dir", None)
    if configured:
        return Path(configured)
    return Path.home() / ".whombat" / "plugins"


def _manifest_in(root: Path) -> Path | None:
    for name in MANIFEST_NAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _directory_sources(settings) -> list[PluginSource]:
    base = plugins_dir_for(settings)
    if not base.is_dir():
        return []

    sources: list[PluginSource] = []
    for child in sorted(base.iterdir(), key=lambda path: path.name.casefold()):
        if not child.is_dir():
            continue
        manifest_path = _manifest_in(child)
        if manifest_path is None:
            continue
        try:
            manifest = load_manifest(manifest_path)
        except ManifestError as error:
            sources.append(
                PluginSource(id=child.name, root=child, error=str(error))
            )
            continue
        sources.append(
            PluginSource(id=manifest.id, root=child, manifest=manifest)
        )
    return sources


def _entry_point_sources() -> list[PluginSource]:
    sources: list[PluginSource] = []
    for entry_point in importlib.metadata.entry_points(
        group=ENTRY_POINT_GROUP
    ):
        try:
            target = entry_point.load()
        except Exception:
            sources.append(
                PluginSource(
                    id=entry_point.name,
                    entry_point=entry_point.name,
                    error="entry point could not be loaded",
                )
            )
            continue

        try:
            if isinstance(target, Mapping):
                manifest = PluginManifest.model_validate(dict(target))
                root = None
            elif isinstance(target, (str, Path)):
                path = Path(target)
                manifest = load_manifest(path)
                root = path.parent
            else:
                raise ManifestError(
                    "entry point must resolve to a manifest path or mapping"
                )
        except (ManifestError, ValueError) as error:
            sources.append(
                PluginSource(
                    id=entry_point.name,
                    entry_point=entry_point.name,
                    error=str(error),
                )
            )
            continue

        sources.append(
            PluginSource(
                id=manifest.id,
                root=root,
                manifest=manifest,
                entry_point=entry_point.name,
            )
        )
    return sources


def _legacy_sources() -> list[PluginSource]:
    return [
        PluginSource(id=name, module=module, legacy=True)
        for name, module in legacy.load_plugins()
    ]


def discover_plugins(settings) -> list[PluginSource]:
    """Return every discovered plugin, conflicts included."""
    sources = (
        _entry_point_sources()
        + _directory_sources(settings)
        + _legacy_sources()
    )

    counts: dict[str, int] = {}
    for source in sources:
        counts[source.id] = counts.get(source.id, 0) + 1

    result: list[PluginSource] = []
    for source in sources:
        if counts[source.id] > 1:
            result.append(
                PluginSource(
                    id=source.id,
                    root=source.root,
                    entry_point=source.entry_point,
                    error="duplicate id",
                )
            )
            continue
        result.append(source)

    return sorted(result, key=lambda source: source.id)
