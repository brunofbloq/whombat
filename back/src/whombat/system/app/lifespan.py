import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

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
        if registry is not None:
            await registry.shutdown()
