from contextlib import asynccontextmanager

from fastapi import FastAPI

from whombat.system.boot import whombat_init
from whombat.system.settings import Settings

__all__ = ["lifespan"]


@asynccontextmanager
async def lifespan(settings: Settings, app: FastAPI):
    """Context manager to run startup and shutdown events."""
    await whombat_init(settings)

    try:
        yield
    finally:
        registry = getattr(app.state, "plugin_registry", None)
        if registry is not None:
            await registry.shutdown()
