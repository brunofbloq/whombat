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

MEMORY_GUEST = """
def handle(method, params):
    if method == "plugin.describe":
        return {"id": "sample"}
    data = bytearray(8192 * 1024 * 1024)
    return {"size": len(data)}
"""


def make_plugin(
    tmp_path,
    *,
    plugin_id: str = "sample",
    api_version: str = "1.0",
    version: str = "0.1.0",
    host_caps: list[str] | None = None,
    fs_read: list[str] | None = None,
    memory_mb: int | None = None,
    guest: str = GUEST,
    directory: str | None = None,
    transport: str = "stdio",
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
                f'transport = "{transport}"',
                *( [f"memory_mb = {memory_mb}"] if memory_mb else [] ),
                "startup_timeout_ms = 20000",
                "call_timeout_ms = 10000",
                "",
                "[capabilities]",
                f"host = {json.dumps(host_caps or [])}",
                f"fs_read = {json.dumps(fs_read or [])}",
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

    for _ in range(50):
        if registry.status("sample")["status"] == "crashed":
            break
        await asyncio.sleep(0.1)

    status = registry.status("sample")
    assert status["status"] == "crashed"
    assert status["last_error"]

    # The registry still answers while the guest is gone.
    assert registry.list_status()
    await registry.shutdown()


async def test_start_failure_marks_plugin_failed(settings, tmp_path):
    make_plugin(
        tmp_path,
        guest="raise SystemExit('nope')\n",
    )
    registry = PluginRegistry(settings)

    with pytest.raises(ValueError):
        await registry.enable("sample", [])

    status = registry.status("sample")
    assert status["status"] == "failed"
    assert status["last_error"]
    await registry.shutdown()


async def test_loopback_requires_bearer_token(settings, tmp_path):
    import json
    import urllib.error
    import urllib.request

    make_plugin(tmp_path, transport="loopback")
    registry = PluginRegistry(settings)
    await registry.enable("sample", [])
    guest = registry._records["sample"].guest
    assert guest is not None
    assert guest.url is not None

    def post(token: str | None):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            guest.url,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "plugin.health",
                    "params": {},
                }
            ).encode(),
            method="POST",
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=5) as reply:
            return reply.status, json.loads(reply.read())

    with pytest.raises(urllib.error.HTTPError) as missing:
        post(None)
    assert missing.value.code == 401

    with pytest.raises(urllib.error.HTTPError) as wrong:
        post("not-the-token")
    assert wrong.value.code == 401

    status, payload = post(guest.token)
    assert status == 200
    assert payload["result"] == {"ok": True}

    await registry.shutdown()


async def test_loopback_invoke_reaches_the_guest(settings, tmp_path):
    make_plugin(tmp_path, transport="loopback")
    registry = PluginRegistry(settings)
    await registry.enable("sample", [])

    result = await registry.invoke("sample", "echo")

    assert result == {"echo": "echo"}
    await registry.shutdown()


async def test_guest_memory_limit_is_enforced(settings, tmp_path):
    make_plugin(tmp_path, guest=MEMORY_GUEST, memory_mb=2048)
    registry = PluginRegistry(settings)
    await registry.enable("sample", [])

    with pytest.raises(GuestError):
        await registry.invoke("sample", "allocate")

    await registry.shutdown()


async def test_fs_read_is_confined_to_declared_roots(settings, tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "inside.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")

    make_plugin(
        tmp_path,
        host_caps=["fs.read"],
        fs_read=[str(allowed)],
    )
    registry = PluginRegistry(settings)
    await registry.enable("sample", ["fs.read"])

    result = await registry.handle_host_call(
        "sample",
        {
            "capability": "fs.read",
            "operation": "read_text",
            "args": {"path": "inside.txt"},
        },
    )
    assert result["text"] == "hello"

    with pytest.raises(GuestError) as error:
        await registry.handle_host_call(
            "sample",
            {
                "capability": "fs.read",
                "operation": "read_text",
                "args": {"path": "../outside.txt"},
            },
        )
    assert error.value.code == -32001

    await registry.shutdown()


async def test_idle_guest_is_stopped(settings, tmp_path):
    make_plugin(tmp_path)
    settings.plugin_idle_timeout_seconds = 60
    registry = PluginRegistry(settings)
    await registry.enable("sample", [])
    assert registry.status("sample")["status"] == "ready"

    registry._last_activity["sample"] -= 120
    stopped = await registry.sweep_idle()

    assert "sample" in stopped
    assert registry.status("sample")["status"] == "disabled"
    assert registry._records["sample"].guest is None


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
