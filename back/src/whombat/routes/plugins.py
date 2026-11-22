"""REST API routes for plugins."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel

from whombat import schemas
from whombat.plugins import GuestError, PluginNotFound, PluginRegistry
from whombat.routes.dependencies.auth import get_current_user_dependency
from whombat.routes.dependencies.settings import WhombatSettings

__all__ = [
    "get_plugin_router",
]

_HTTP_FOR_CODE = {
    -32001: status.HTTP_403_FORBIDDEN,
    -32002: status.HTTP_504_GATEWAY_TIMEOUT,
    -32003: status.HTTP_400_BAD_REQUEST,
    -32004: status.HTTP_503_SERVICE_UNAVAILABLE,
}


class EnableRequest(BaseModel):
    """Optional capability approval sent when enabling a plugin."""

    grants: list[str] | None = None


class InvokeRequest(BaseModel):
    """Payload of a proxied plugin call."""

    operation: str
    args: dict = {}


def _registry(request: Request) -> PluginRegistry:
    registry = getattr(request.app.state, "plugin_registry", None)
    if registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Plugin registry is unavailable",
        )
    return registry


def _lookup(registry: PluginRegistry, plugin_id: str) -> dict:
    try:
        return registry.status(plugin_id)
    except PluginNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown plugin {plugin_id}",
        ) from error


async def _ensure_ui_origin(request: Request, registry: PluginRegistry) -> None:
    """Start the plugin origin on first use, e.g. after a hot refresh."""
    if registry.ui_base is not None or not registry.has_ui_plugins():
        return
    server = getattr(request.app.state, "plugin_ui_server", None)
    if server is not None:
        registry.ui_base = await server.start()


def get_plugin_router(settings: WhombatSettings) -> APIRouter:
    """Build the authenticated plugin router."""
    router = APIRouter()
    active_user = get_current_user_dependency(settings)

    @router.get(
        "/list/",
        response_model=list[schemas.PluginStatus],
    )
    async def list_plugins(
        request: Request,
        user: Annotated[schemas.SimpleUser, Depends(active_user)],
    ):
        """List every discovered plugin and its status."""
        del user
        registry = _registry(request)
        await _ensure_ui_origin(request, registry)
        return registry.list_status()

    @router.get(
        "/{plugin_id}/status/",
        response_model=schemas.PluginStatus,
    )
    async def plugin_status(
        plugin_id: str,
        request: Request,
        user: Annotated[schemas.SimpleUser, Depends(active_user)],
    ):
        """Return one plugin's status."""
        del user
        registry = _registry(request)
        _lookup(registry, plugin_id)
        await _ensure_ui_origin(request, registry)
        return registry.status(plugin_id)

    @router.post(
        "/{plugin_id}/enable/",
        response_model=schemas.PluginStatus,
    )
    async def enable_plugin(
        plugin_id: str,
        request: Request,
        user: Annotated[schemas.SimpleUser, Depends(active_user)],
        body: Annotated[EnableRequest | None, Body()] = None,
    ):
        """Enable a plugin and approve its capabilities."""
        del user
        registry = _registry(request)
        _lookup(registry, plugin_id)
        try:
            return await registry.enable(
                plugin_id, None if body is None else body.grants
            )
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
            ) from error

    @router.post(
        "/{plugin_id}/disable/",
        response_model=schemas.PluginStatus,
    )
    async def disable_plugin(
        plugin_id: str,
        request: Request,
        user: Annotated[schemas.SimpleUser, Depends(active_user)],
    ):
        """Disable a plugin and stop its guest process."""
        del user
        registry = _registry(request)
        _lookup(registry, plugin_id)
        return await registry.disable(plugin_id)

    @router.post(
        "/{plugin_id}/restart/",
        response_model=schemas.PluginStatus,
    )
    async def restart_plugin(
        plugin_id: str,
        request: Request,
        user: Annotated[schemas.SimpleUser, Depends(active_user)],
    ):
        """Restart a plugin, keeping approved capabilities."""
        del user
        registry = _registry(request)
        _lookup(registry, plugin_id)
        try:
            return await registry.restart(plugin_id)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
            ) from error

    @router.delete("/{plugin_id}/")
    async def uninstall_plugin(
        plugin_id: str,
        request: Request,
        user: Annotated[schemas.SimpleUser, Depends(active_user)],
    ):
        """Stop and remove a plugin."""
        del user
        registry = _registry(request)
        _lookup(registry, plugin_id)
        return await registry.uninstall(plugin_id)

    @router.get("/{plugin_id}/health/")
    async def plugin_health(
        plugin_id: str,
        request: Request,
        user: Annotated[schemas.SimpleUser, Depends(active_user)],
    ):
        """Return liveness details for a plugin."""
        del user
        registry = _registry(request)
        _lookup(registry, plugin_id)
        return await registry.health(plugin_id)

    @router.post("/{plugin_id}/invoke/")
    async def invoke_plugin(
        plugin_id: str,
        request: Request,
        body: InvokeRequest,
        user: Annotated[schemas.SimpleUser, Depends(active_user)],
    ):
        """Proxy a call into a running plugin."""
        del user
        registry = _registry(request)
        _lookup(registry, plugin_id)
        try:
            return {
                "result": await registry.invoke(
                    plugin_id, body.operation, body.args
                )
            }
        except GuestError as error:
            raise HTTPException(
                status_code=_HTTP_FOR_CODE.get(
                    error.code, status.HTTP_500_INTERNAL_SERVER_ERROR
                ),
                detail=error.message,
            ) from error

    return router
