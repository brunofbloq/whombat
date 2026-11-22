"""Plugin registry: lifecycle, capability grants and brokered calls."""

from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy import func, select

from whombat import models
from whombat.plugins.discovery import (
    PluginSource,
    discover_plugins,
    plugins_dir_for,
)
from whombat.plugins.guest import GuestError, GuestProcess
from whombat.plugins.manifest import is_supported_api_version

__all__ = [
    "PluginNotFound",
    "PluginRegistry",
    "STATUSES",
]

logger = logging.getLogger(__name__)
audit = logging.getLogger("whombat.plugins.audit")

STATUSES = (
    "discovered",
    "disabled",
    "starting",
    "ready",
    "unhealthy",
    "crashed",
    "incompatible",
    "failed",
)

GRANTS_FILE = ".grants.json"

Broker = Callable[[str, dict], Awaitable[Any]]


class PluginNotFound(KeyError):
    """Raised when a plugin id is not registered."""


@dataclass
class _Record:
    source: PluginSource
    status: str
    grants: list[str] = field(default_factory=list)
    guest: GuestProcess | None = None
    last_error: str | None = None
    enabled_at: float | None = None


class PluginRegistry:
    """Own plugin discovery, status and guest processes."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.plugins_dir = plugins_dir_for(settings)
        self._records: dict[str, _Record] = {}
        self._index: dict[str, str] = {}
        self._last_activity: dict[str, float] = {}
        self.idle_timeout = getattr(
            settings, "plugin_idle_timeout_seconds", 0
        )
        self._grants_path = self.plugins_dir / GRANTS_FILE
        self.refresh()

    # -- discovery ---------------------------------------------------

    def refresh(self) -> None:
        """Rescan plugin sources, preserving running guests."""
        stored = self._read_grants()
        records: dict[str, _Record] = {}
        index: dict[str, str] = {}

        for position, source in enumerate(
            discover_plugins(self.settings)
        ):
            key = source.id
            if key in index:
                key = f"{source.id}#{position}"
            else:
                index[source.id] = key

            if source.error is not None:
                records[key] = _Record(
                    source=source,
                    status="failed",
                    last_error=source.error,
                )
                continue

            if source.legacy:
                records[key] = _Record(
                    source=source, status="ready", grants=[]
                )
                continue

            manifest = source.manifest
            assert manifest is not None
            if not is_supported_api_version(manifest.api_version):
                records[key] = _Record(
                    source=source,
                    status="incompatible",
                    last_error=(
                        f"unsupported api_version {manifest.api_version}"
                    ),
                )
                continue

            grants = self._valid_grants(manifest, stored.get(manifest.id))
            records[key] = _Record(
                source=source,
                status="disabled",
                grants=grants,
            )

        for plugin_id, record in records.items():
            previous = self._records.get(plugin_id)
            if previous is None or previous.guest is None:
                continue
            if previous.guest.alive:
                record.guest = previous.guest
                record.status = previous.status
                record.last_error = previous.last_error
                record.enabled_at = previous.enabled_at

        self._records = records
        self._index = index

    # -- queries -----------------------------------------------------

    def list_status(self) -> list[dict]:
        """Return one status dict per plugin."""
        return [
            self._status(self._records[plugin_id])
            for plugin_id in sorted(self._records)
        ]

    def status(self, plugin_id: str) -> dict:
        """Return the status dict for one plugin."""
        return self._status(self._record(plugin_id))

    def asset_root(self, plugin_id: str) -> Path | None:
        """Return the directory that holds a plugin's static assets."""
        return self._record(plugin_id).source.root

    def legacy_plugins(self) -> list[tuple[str, object]]:
        """Return ``(name, module)`` for legacy in-process plugins."""
        return [
            (record.source.id, record.source.module)
            for record in self._records.values()
            if record.source.legacy and record.source.module is not None
        ]

    def _record(self, plugin_id: str) -> _Record:
        key = self._index.get(plugin_id)
        if key is None:
            raise PluginNotFound(plugin_id)
        return self._records[key]

    def _status(self, record: _Record) -> dict:
        source = record.source
        manifest = source.manifest
        legacy_result = {
            "id": source.id,
            "name": source.id,
            "version": getattr(source.module, "__version__", None),
            "api_version": None,
            "description": getattr(
                source.module, "__description__", None
            ),
            "author": None,
            "attribution": getattr(
                source.module, "__attribution__", None
            ),
            "thumbnail": getattr(source.module, "__thumbnail__", None),
            "url": f"/plugins/pages/{source.id}/",
            "status": record.status,
            "trust": "trusted",
            "capabilities": [],
            "granted": [],
            "ui_url": f"/plugins/pages/{source.id}/",
            "legacy": True,
            "last_error": record.last_error,
        }
        if source.legacy:
            return legacy_result

        if manifest is None:
            return {
                **legacy_result,
                "name": source.id,
                "trust": "untrusted",
                "legacy": False,
                "url": f"/plugins/{source.id}",
                "ui_url": None,
            }

        ui_url = None
        if manifest.ui is not None and manifest.ui.entry:
            ui_url = (
                f"/plugins/assets/{manifest.id}/{manifest.ui.entry}"
            )

        return {
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "api_version": manifest.api_version,
            "description": manifest.description,
            "author": manifest.author,
            "attribution": manifest.attribution,
            "thumbnail": manifest.thumbnail,
            "url": f"/plugins/{manifest.id}",
            "status": record.status,
            "trust": "untrusted",
            "capabilities": list(manifest.capabilities.host),
            "granted": list(record.grants),
            "ui_url": ui_url,
            "legacy": False,
            "last_error": record.last_error,
        }

    # -- lifecycle ---------------------------------------------------

    async def enable(
        self, plugin_id: str, grants: list[str] | None = None
    ) -> dict:
        """Start a plugin after approving its capabilities."""
        record = self._record(plugin_id)
        source = record.source

        if record.status == "incompatible":
            raise ValueError(
                record.last_error or "Plugin API version is unsupported"
            )
        if record.status == "ready":
            return self._status(record)
        if source.error is not None:
            raise ValueError(source.error)
        if source.legacy:
            record.status = "ready"
            return self._status(record)

        manifest = source.manifest
        assert manifest is not None

        declared = list(manifest.capabilities.host)
        requested = declared if grants is None else list(grants)
        unknown = sorted(set(requested) - set(declared))
        if unknown:
            raise ValueError(
                "capabilities were not declared: " + ", ".join(unknown)
            )

        record.grants = requested
        if manifest.backend is None:
            record.status = "ready"
            record.last_error = None
            self._write_grants()
            audit.info("plugin enabled id=%s (ui only)", plugin_id)
            return self._status(record)

        guest = GuestProcess(
            source,
            broker=lambda params: self._host_call(plugin_id, params),
            on_exit=lambda: self._mark_crashed(plugin_id),
        )
        record.status = "starting"
        try:
            await guest.start()
        except GuestError as error:
            record.status = "failed"
            record.last_error = error.message
            audit.info(
                "plugin failed to start id=%s error=%s", plugin_id, error.message
            )
            raise ValueError(error.message) from error

        record.guest = guest
        record.status = "ready"
        record.last_error = None
        record.enabled_at = time.monotonic()
        self._last_activity[plugin_id] = record.enabled_at
        self._write_grants()
        audit.info("plugin enabled id=%s grants=%s", plugin_id, ",".join(requested))
        return self._status(record)

    async def disable(self, plugin_id: str) -> dict:
        """Stop a plugin and revoke its guest process."""
        record = self._record(plugin_id)
        await self._stop(record)
        record.status = "disabled"
        record.grants = []
        self._write_grants()
        audit.info("plugin disabled id=%s", plugin_id)
        return self._status(record)

    async def restart(self, plugin_id: str) -> dict:
        """Stop and start a plugin, keeping approved grants."""
        record = self._record(plugin_id)
        grants = list(record.grants)
        await self._stop(record)
        record.status = "disabled"
        return await self.enable(plugin_id, grants)

    async def uninstall(self, plugin_id: str) -> dict:
        """Stop a plugin and forget it."""
        record = self._record(plugin_id)
        await self._stop(record)
        key = self._index.pop(plugin_id, None)
        if key is not None:
            self._records.pop(key, None)

        stored = self._read_grants()
        stored.pop(plugin_id, None)
        self._write_grants(stored)

        root = record.source.root
        if root is not None and _is_within(root, self.plugins_dir):
            shutil.rmtree(root, ignore_errors=True)

        audit.info("plugin uninstalled id=%s", plugin_id)
        return {"id": plugin_id, "status": "removed"}

    async def shutdown(self) -> None:
        """Stop every running guest."""
        for record in self._records.values():
            await self._stop(record)

    async def health(self, plugin_id: str) -> dict:
        """Return liveness details for a plugin."""
        record = self._record(plugin_id)
        checks: list[dict] = []
        if record.guest is not None and record.guest.alive:
            try:
                result = await record.guest.call(
                    "plugin.health", {}, timeout_ms=2000
                )
                checks.append({"name": "guest", "ok": True, "result": result})
            except GuestError as error:
                record.status = "unhealthy"
                record.last_error = error.message
                checks.append(
                    {"name": "guest", "ok": False, "error": error.message}
                )
        else:
            checks.append(
                {
                    "name": "guest",
                    "ok": record.source.legacy,
                    "error": record.last_error,
                }
            )

        uptime_ms = None
        if record.enabled_at is not None:
            uptime_ms = int((time.monotonic() - record.enabled_at) * 1000)

        return {
            "status": record.status,
            "checks": checks,
            "uptime_ms": uptime_ms,
            "last_error": record.last_error,
        }

    async def invoke(
        self, plugin_id: str, operation: str, args: dict | None = None
    ) -> Any:
        """Proxy a call into a running plugin guest."""
        record = self._record(plugin_id)
        guest = record.guest
        if guest is None or not guest.alive or record.status != "ready":
            raise GuestError(-32004, "Plugin is not running")
        self._last_activity[plugin_id] = time.monotonic()
        return await guest.call(
            "invoke", {"operation": operation, "args": args or {}}
        )

    async def sweep_idle(self) -> list[str]:
        """Stop guests that have been idle longer than the timeout."""
        if self.idle_timeout <= 0:
            return []

        now = time.monotonic()
        stopped: list[str] = []
        for plugin_id, record in list(self._records.items()):
            if record.guest is None or not record.guest.alive:
                continue
            last = self._last_activity.get(
                plugin_id, record.enabled_at or now
            )
            if now - last < self.idle_timeout:
                continue
            await self._stop(record)
            record.status = "disabled"
            record.grants = []
            stopped.append(record.source.id)

        if stopped:
            self._write_grants()
            audit.info("idle plugins stopped: %s", ",".join(stopped))
        return stopped

    async def _stop(self, record: _Record) -> None:
        if record.guest is not None:
            await record.guest.stop()
            if not record.guest.alive:
                record.guest = None
        record.enabled_at = None

    def _mark_crashed(self, plugin_id: str) -> None:
        """Record an unexpected guest exit without disturbing the host."""
        try:
            record = self._record(plugin_id)
        except PluginNotFound:
            return
        if record.guest is None:
            return
        record.status = "crashed"
        record.last_error = (
            record.guest.last_error or "Plugin process exited"
        )
        record.enabled_at = None
        audit.warning(
            "plugin crashed id=%s error=%s",
            plugin_id,
            record.last_error,
        )

    # -- capability broker -------------------------------------------

    async def _host_call(self, plugin_id: str, params: dict) -> Any:
        return await self.handle_host_call(plugin_id, params)

    async def handle_host_call(self, plugin_id: str, params: dict) -> Any:
        """Authorize and run one brokered host operation."""
        record = self._record(plugin_id)
        capability = params.get("capability")
        operation = params.get("operation")
        arguments = params.get("args") or {}
        if not isinstance(arguments, dict):
            raise GuestError(-32003, "Invalid params")

        if not isinstance(capability, str) or capability not in record.grants:
            audit.warning(
                "capability denied plugin=%s capability=%s operation=%s",
                plugin_id,
                capability,
                operation,
            )
            raise GuestError(-32001, "Capability denied")

        handler = _HOST_OPERATIONS.get((capability, operation))
        if handler is None:
            raise GuestError(-32601, "Unknown operation")

        self._last_activity[plugin_id] = time.monotonic()
        return await handler(self.settings, arguments)

    # -- grants persistence ------------------------------------------

    def _valid_grants(
        self, manifest, stored: dict | None
    ) -> list[str]:
        if not stored:
            return []
        declared = list(manifest.capabilities.host)
        if stored.get("version") != manifest.version:
            return []
        if stored.get("declared") != declared:
            return []
        granted = stored.get("granted") or []
        return [name for name in granted if name in declared]

    def _read_grants(self) -> dict:
        try:
            raw = self._grants_path.read_text("utf-8")
        except OSError:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Ignoring corrupt plugin grants file")
            return {}
        return data if isinstance(data, dict) else {}

    def _write_grants(self, data: dict | None = None) -> None:
        if data is None:
            data = {
                record.source.manifest.id: {
                    "version": record.source.manifest.version,
                    "declared": list(
                        record.source.manifest.capabilities.host
                    ),
                    "granted": list(record.grants),
                }
                for record in self._records.values()
                if record.source.manifest is not None
                and record.grants
            }
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self._grants_path.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


async def _list_datasets(settings, args: dict) -> dict:
    # Imported lazily to avoid a circular import at app start-up.
    from whombat.api.sessions import create_session
    from whombat.system.database import get_database_url

    limit = max(1, min(int(args.get("limit", 20)), 200))
    async with create_session(get_database_url(settings)) as session:
        rows = await session.execute(
            select(models.Dataset.id, models.Dataset.name)
            .order_by(models.Dataset.name)
            .limit(limit)
        )
        return {
            "datasets": [
                {"id": str(row.id), "name": row.name} for row in rows
            ]
        }


async def _count_recordings(settings, args: dict) -> dict:
    from whombat.api.sessions import create_session
    from whombat.system.database import get_database_url

    async with create_session(get_database_url(settings)) as session:
        total = await session.scalar(
            select(func.count()).select_from(models.Recording)
        )
    return {"count": int(total or 0)}


_HOST_OPERATIONS: dict[tuple[str, str], Callable[..., Awaitable[Any]]] = {
    ("datasets.read", "list"): _list_datasets,
    ("recordings.read", "count"): _count_recordings,
}
