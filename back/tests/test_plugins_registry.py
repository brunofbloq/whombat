"""Tests for plugin discovery, lifecycle, grants and asset confinement."""

import asyncio
import json
from pathlib import Path

import pytest

from whombat.plugins import (
    AssetNotFound,
    GuestError,
    PluginRegistry,
    resolve_plugin_asset,
)
from whombat.system.settings import Settings

GUEST = """
def handle(method, params):
    if method == "plugin.describe":
        return {"id": "sample", "version": "0.1.0"}
    if method == "invoke":
        return {"echo": params.get("operation")}
    return {"ok": True}
"""

CRASHING_GUEST = """
import os

def handle(method, params):
    if method == "plugin.describe":
        return {"id": "sample"}
    os._exit(3)
"""


def make_plugin(
    tmp_path,
    *,
    plugin_id: str = "sample",
    api_version: str = "1.0",
    version: str = "0.1.0",
    host_caps: list[str] | None = None,
    guest: str = GUEST,
    directory: str | None = None,
) -> Path:
    root = tmp_path / "plugins" / (directory or plugin_id)
    package = root / "sample_guest"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text(guest, encoding="utf-8")
    (root / "plugin.toml").write_text(
        "\n".join(
            [
                f'id = "{plugin_id}"',
                'name = "Sample"',
                f'version = "{version}"',
                f'api_version = "{api_version}"',
                "",
                "[backend]",
                'entry = "sample_guest:handle"',
                "startup_timeout_ms = 5000",
                "call_timeout_ms = 3000",
                "",
                "[capabilities]",
                f"host = {json.dumps(host_caps or [])}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return root


@pytest.fixture
def settings(tmp_path):
    return Settings(
        plugins_dir=tmp_path / "plugins",
        db_url="sqlite+aiosqlite://",
        log_to_file=False,
    )


def test_discovered_plugin_is_disabled(settings, tmp_path):
    make_plugin(tmp_path)
    registry = PluginRegistry(settings)

    status = registry.status("sample")

    assert status["status"] == "disabled"
    assert status["trust"] == "untrusted"
    assert status["capabilities"] == []
    assert registry._records["sample"].guest is None


def test_incompatible_api_version_is_not_started(settings, tmp_path):
    make_plugin(tmp_path, api_version="9.0")
    registry = PluginRegistry(settings)

    status = registry.status("sample")

    assert status["status"] == "incompatible"
    assert "unsupported" in (status["last_error"] or "")


def test_duplicate_ids_are_reported_as_conflicts(settings, tmp_path):
    make_plugin(tmp_path, directory="first")
    make_plugin(tmp_path, directory="second")
    registry = PluginRegistry(settings)

    statuses = [
        status for status in registry.list_status() if status["id"] == "sample"
    ]

    assert [status["status"] for status in statuses] == ["failed", "failed"]
    assert all(
        status["last_error"] == "duplicate id" for status in statuses
    )


async def test_enable_starts_guest_and_disable_stops_it(settings, tmp_path):
    make_plugin(tmp_path, host_caps=["datasets.read"])
    registry = PluginRegistry(settings)

    started = await registry.enable("sample", ["datasets.read"])

    assert started["status"] == "ready"
    assert started["granted"] == ["datasets.read"]
    assert registry._records["sample"].guest is not None
    assert registry._records["sample"].guest.alive

    stopped = await registry.disable("sample")

    assert stopped["status"] == "disabled"
    assert stopped["granted"] == []
    await registry.shutdown()


async def test_enable_rejects_undeclared_capability(settings, tmp_path):
    make_plugin(tmp_path)
    registry = PluginRegistry(settings)

    with pytest.raises(ValueError, match="not declared"):
        await registry.enable("sample", ["datasets.read"])


async def test_invoke_reaches_the_guest(settings, tmp_path):
    make_plugin(tmp_path)
    registry = PluginRegistry(settings)
    await registry.enable("sample", [])

    result = await registry.invoke("sample", "echo")

    assert result == {"echo": "echo"}
    await registry.shutdown()


async def test_host_call_denied_without_grant(settings, tmp_path, caplog):
    make_plugin(tmp_path)
    registry = PluginRegistry(settings)
    await registry.enable("sample", [])

    with pytest.raises(GuestError) as error:
        await registry.handle_host_call(
            "sample",
            {"capability": "datasets.read", "operation": "list"},
        )

    assert error.value.code == -32001
    assert "capability denied" in caplog.text
    await registry.shutdown()


async def test_host_call_rejects_unknown_operation(settings, tmp_path):
    make_plugin(tmp_path, host_caps=["datasets.read"])
    registry = PluginRegistry(settings)
    await registry.enable("sample", ["datasets.read"])

    with pytest.raises(GuestError) as error:
        await registry.handle_host_call(
            "sample",
            {"capability": "datasets.read", "operation": "nope"},
        )

    assert error.value.code == -32601
    await registry.shutdown()


async def test_guest_crash_is_contained(settings, tmp_path):
    make_plugin(tmp_path, guest=CRASHING_GUEST)
    registry = PluginRegistry(settings)
    await registry.enable("sample", [])
    guest = registry._records["sample"].guest
    assert guest is not None

    await asyncio.sleep(0.2)

    with pytest.raises(GuestError) as error:
        await registry.invoke("sample", "boom")
    assert error.value.code == -32004

    # The registry still answers while the guest is gone.
    assert registry.status("sample")["status"] in {"ready", "unhealthy"}
    await registry.shutdown()


def test_grants_persist_and_invalidate_on_capability_change(
    settings, tmp_path
):
    make_plugin(tmp_path, host_caps=["datasets.read"])
    first = PluginRegistry(settings)
    first._records["sample"].grants = ["datasets.read"]
    first._write_grants()

    reopened = PluginRegistry(settings)
    assert reopened.status("sample")["granted"] == ["datasets.read"]

    make_plugin(tmp_path, host_caps=["recordings.read"])
    changed = PluginRegistry(settings)
    assert changed.status("sample")["granted"] == []


def test_asset_resolution_is_confined(tmp_path):
    root = tmp_path / "plugin"
    (root / "dist").mkdir(parents=True)
    (root / "dist" / "index.html").write_text("ok", encoding="utf-8")

    assert resolve_plugin_asset(root, "dist/index.html").read_text() == "ok"

    for escape in ("../secret.txt", "..\\secret.txt", "/etc/passwd"):
        with pytest.raises(AssetNotFound):
            resolve_plugin_asset(root, escape)
