import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from whombat.plugins.ui_server import PluginUIServer
from whombat.system.boot import whombat_init
from whombat.system.settings import Settings

__all__ = ["lifespan"]

SWEEP_INTERVAL_SECONDS = 60


async def _sweep_idle_plugins(registry) -> None:
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        try:
            await registry.sweep_idle()
        except Exception:  # noqa: BLE001 - the sweep must never kill the app
            continue


@asynccontextmanager
async def lifespan(settings: Settings, app: FastAPI):
    """Context manager to run startup and shutdown events."""
    await whombat_init(settings)

    registry = getattr(app.state, "plugin_registry", None)
    ui_server: PluginUIServer | None = None
    if registry is not None:
        ui_server = PluginUIServer(registry)
        app.state.plugin_ui_server = ui_server
        if registry.has_ui_plugins():
            registry.ui_base = await ui_server.start()

    sweeper = (
        asyncio.create_task(_sweep_idle_plugins(registry))
        if registry is not None
        else None
    )

    try:
        yield
    finally:
        if sweeper is not None:
            sweeper.cancel()
        if ui_server is not None:
            await ui_server.stop()
        if registry is not None:
            await registry.shutdown()
