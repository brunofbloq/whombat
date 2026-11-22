"""Tests for the plugin HTTP routes."""

import json

import pytest
from fastapi.testclient import TestClient

from whombat.system import create_app
from whombat.system.settings import get_settings

GUEST = """
def handle(method, params):
    if method == "plugin.describe":
        return {"id": "sample"}
    return {"ok": True}
"""


def make_plugin(
    base,
    *,
    version: str | None = "0.1.0",
    ui: bool = False,
    guest: str = GUEST,
):
    root = base / "plugins" / "sample"
    package = root / "sample_guest"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(guest, encoding="utf-8")
    lines = ['id = "sample"', 'name = "Sample"', 'api_version = "1.0"']
    if version is not None:
        lines.append(f'version = "{version}"')
    lines += ["", "[backend]", 'entry = "sample_guest:handle"']
    if ui:
        (root / "dist").mkdir()
        (root / "dist" / "index.html").write_text("ui", encoding="utf-8")
        lines += ["", "[ui]", 'entry = "dist/index.html"']
    lines += ["", "[capabilities]", f"host = {json.dumps([])}", ""]
    (root / "plugin.toml").write_text("\n".join(lines), encoding="utf-8")
    return root


def add_plugin(client, tmp_path, **kwargs):
    """Create a plugin and rescan the running app's registry."""
    root = make_plugin(tmp_path, **kwargs)
    client.app.state.plugin_registry.refresh()
    return root


@pytest.fixture
def plugin_client(settings, tmp_path):
    settings.plugins_dir = tmp_path / "plugins"
    settings.plugins_dir.mkdir()
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client


class TestPluginRoutes:
    def test_list_requires_authentication(self, plugin_client):
        response = plugin_client.get("/api/v1/plugins/list/")

        assert response.status_code == 401

    def test_lists_disabled_plugin_with_full_status(
        self, plugin_client, cookies, tmp_path
    ):
        add_plugin(plugin_client, tmp_path, ui=True)

        response = plugin_client.get(
            "/api/v1/plugins/list/", cookies=cookies
        )

        assert response.status_code == 200
        entry = next(
            item for item in response.json() if item["id"] == "sample"
        )
        assert entry["status"] == "disabled"
        assert entry["trust"] == "untrusted"
        assert entry["legacy"] is False
        assert entry["ui_url"] == "/plugins/assets/sample/dist/index.html"

    def test_list_accepts_missing_version(
        self, plugin_client, cookies, tmp_path
    ):
        add_plugin(plugin_client, tmp_path, version=None)

        response = plugin_client.get(
            "/api/v1/plugins/list/", cookies=cookies
        )

        assert response.status_code == 200
        entry = next(
            item for item in response.json() if item["id"] == "sample"
        )
        assert entry["version"] is None

    def test_enable_and_disable(self, plugin_client, cookies, tmp_path):
        add_plugin(plugin_client, tmp_path)

        enabled = plugin_client.post(
            "/api/v1/plugins/sample/enable/", cookies=cookies
        )
        assert enabled.status_code == 200
        assert enabled.json()["status"] == "ready"

        disabled = plugin_client.post(
            "/api/v1/plugins/sample/disable/", cookies=cookies
        )
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "disabled"

    def test_enable_rejects_undeclared_grant(
        self, plugin_client, cookies, tmp_path
    ):
        add_plugin(plugin_client, tmp_path)

        response = plugin_client.post(
            "/api/v1/plugins/sample/enable/",
            json={"grants": ["datasets.read"]},
            cookies=cookies,
        )

        assert response.status_code == 400

    def test_unknown_plugin_is_404(self, plugin_client, cookies):
        response = plugin_client.get(
            "/api/v1/plugins/nope/status/", cookies=cookies
        )

        assert response.status_code == 404


CRASHING = """
import os

def handle(method, params):
    if method == "plugin.describe":
        return {"id": "sample"}
    os._exit(4)
"""


class TestCrashedPlugin:
    def test_crash_is_reported_and_invoke_returns_503(
        self, plugin_client, cookies, tmp_path
    ):
        import time

        add_plugin(plugin_client, tmp_path, guest=CRASHING)
        enabled = plugin_client.post(
            "/api/v1/plugins/sample/enable/", cookies=cookies
        )
        assert enabled.status_code == 200

        response = plugin_client.post(
            "/api/v1/plugins/sample/invoke/",
            json={"operation": "anything"},
            cookies=cookies,
        )
        assert response.status_code == 503

        for _ in range(30):
            status = plugin_client.get(
                "/api/v1/plugins/sample/status/", cookies=cookies
            )
            if status.json()["status"] == "crashed":
                break
            time.sleep(0.1)
        assert status.json()["status"] == "crashed"

        # The rest of the API keeps working.
        listed = plugin_client.get(
            "/api/v1/plugins/list/", cookies=cookies
        )
        assert listed.status_code == 200


class TestPluginAssets:
    def test_serves_asset_inside_root(self, plugin_client, tmp_path):
        add_plugin(plugin_client, tmp_path, ui=True)

        response = plugin_client.get(
            "/plugins/assets/sample/dist/index.html"
        )

        assert response.status_code == 200
        assert response.text == "ui"

    def test_rejects_traversal(self, plugin_client, tmp_path):
        add_plugin(plugin_client, tmp_path, ui=True)

        response = plugin_client.get(
            "/plugins/assets/sample/../../etc/passwd"
        )

        assert response.status_code in {400, 404}
