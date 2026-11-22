"""Legacy in-process plugin loading.

These helpers implement the original convention: any importable module
named ``whombat_*`` may contribute an ``APIRouter`` mounted at
``/plugins/routes/<name>`` and a ``statics/`` package mounted at
``/plugins/pages/<name>/``.

The convention is kept for a deprecation window. New plugins must ship a
manifest and run out of process; see :mod:`whombat.plugins.registry`.
"""

import importlib
import pkgutil
import warnings
from types import ModuleType
from typing import Generator

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

__all__ = [
    "add_plugin_pages",
    "add_plugin_routes",
    "get_plugin_page_url",
    "load_plugins",
    "plugin_has_pages",
]


def load_plugins() -> Generator[tuple[str, ModuleType], None, None]:
    """Load all modules on the path whose name starts with ``whombat_``."""
    for _, name, _ in pkgutil.iter_modules():
        if not name.startswith("whombat_"):
            continue

        try:
            module = importlib.import_module(name)
        except Exception:
            warnings.warn(
                f"Plugin {name} could not be imported.", stacklevel=2
            )
            continue

        if not hasattr(module, "__description__"):
            warnings.warn(
                f"Plugin {name} has no __description__ attribute.",
                stacklevel=2,
            )
            continue

        if not hasattr(module, "__version__"):
            warnings.warn(
                f"Plugin {name} has no __version__ attribute.",
                stacklevel=2,
            )

        yield name[8:], module


def add_plugin_routes(app: FastAPI, name: str, plugin: ModuleType) -> None:
    """Mount a plugin's ``router`` under ``/plugins/routes/<name>``."""
    router = getattr(plugin, "router", None)
    if not isinstance(router, APIRouter):
        return
    app.include_router(router, prefix=f"/plugins/routes/{name}")


def get_plugin_page_url(name: str) -> str:
    """Return the URL of a plugin's static page."""
    return f"/plugins/pages/{name}/"


def plugin_has_pages(plugin: ModuleType) -> bool:
    """Return True when the plugin exposes a ``statics`` directory."""
    return bool(getattr(plugin, "has_pages", False))


def add_plugin_pages(app: FastAPI, name: str, plugin: ModuleType) -> None:
    """Mount a plugin's ``statics`` package as its page root."""
    if not plugin_has_pages(plugin):
        return

    app.mount(
        get_plugin_page_url(name),
        StaticFiles(packages=[(plugin.__name__, "statics")], html=True),
        name=f"{name}",
    )
