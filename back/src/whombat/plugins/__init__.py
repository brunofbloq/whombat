"""Plugin discovery, isolation and lifecycle.

Legacy in-process helpers live in :mod:`whombat.plugins.legacy`; new
plugins declare a manifest and run in their own process.
"""

from whombat.plugins.assets import AssetNotFound, resolve_plugin_asset
from whombat.plugins.discovery import (
    ENTRY_POINT_GROUP,
    PluginSource,
    discover_plugins,
    plugins_dir_for,
)
from whombat.plugins.guest import GuestError, GuestProcess
from whombat.plugins.legacy import (
    add_plugin_pages,
    add_plugin_routes,
    get_plugin_page_url,
    load_plugins,
    plugin_has_pages,
)
from whombat.plugins.manifest import (
    ManifestError,
    PluginManifest,
    is_supported_api_version,
    load_manifest,
)
from whombat.plugins.registry import PluginNotFound, PluginRegistry

__all__ = [
    "ENTRY_POINT_GROUP",
    "AssetNotFound",
    "GuestError",
    "GuestProcess",
    "ManifestError",
    "PluginManifest",
    "PluginNotFound",
    "PluginRegistry",
    "PluginSource",
    "add_plugin_pages",
    "add_plugin_routes",
    "discover_plugins",
    "get_plugin_page_url",
    "is_supported_api_version",
    "load_manifest",
    "load_plugins",
    "plugin_has_pages",
    "plugins_dir_for",
    "resolve_plugin_asset",
]
