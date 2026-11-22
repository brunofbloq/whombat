"""Separate loopback origin that serves plugin UI assets.

Plugin HTML must not be served from the Whombat origin: otherwise a plugin
opened outside the sandbox frame would inherit the app's cookies and could
call authenticated APIs. This server binds an ephemeral port on 127.0.0.1
and exposes only confined files under each plugin's directory.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from whombat.plugins.assets import AssetNotFound, resolve_plugin_asset
from whombat.plugins.registry import PluginNotFound, PluginRegistry

__all__ = ["PluginUIServer", "build_ui_app"]

_START_ATTEMPTS = 200
_START_DELAY = 0.05


def build_ui_app(registry: PluginRegistry) -> FastAPI:
    """Build the asset-only app served from the plugin origin."""
    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        title="Whombat plugin assets",
    )

    @app.get("/{plugin_id}/{path:path}")
    async def plugin_asset(plugin_id: str, path: str):
        try:
            root = registry.asset_root(plugin_id)
        except PluginNotFound as error:
            raise HTTPException(
                status_code=404, detail="Unknown plugin"
            ) from error
        if root is None:
            raise HTTPException(status_code=404, detail="No assets")

        try:
            asset = resolve_plugin_asset(Path(root), path)
        except AssetNotFound as error:
            raise HTTPException(
                status_code=404, detail=str(error)
            ) from error
        return FileResponse(asset)

    return app


class PluginUIServer:
    """Run the plugin origin on an ephemeral loopback port."""

    def __init__(self, registry: PluginRegistry) -> None:
        self.registry = registry
        self.base_url: str | None = None
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> str:
        """Start the server and return its origin, e.g. ``http://127.0.0.1:49152``."""
        if self.base_url is not None:
            return self.base_url

        config = uvicorn.Config(
            build_ui_app(self.registry),
            host="127.0.0.1",
            port=0,
            log_level="warning",
            access_log=False,
            lifespan="off",
        )
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())

        for _ in range(_START_ATTEMPTS):
            if self._server.started:
                break
            await asyncio.sleep(_START_DELAY)
        else:
            raise RuntimeError("Plugin UI server did not start")

        sockets = self._server.servers[0].sockets
        port = sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}"
        return self.base_url

    async def stop(self) -> None:
        """Stop the server and wait for its task."""
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()
        self._server = None
        self._task = None
        self.base_url = None
