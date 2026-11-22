"""Tests for plugin manifest parsing and validation."""

import json

import pytest

from whombat.plugins.manifest import (
    ManifestError,
    PluginManifest,
    is_supported_api_version,
    load_manifest,
)

VALID_TOML = """
id = "acoustic-export"
name = "Acoustic Export"
version = "0.3.0"
api_version = "1.0"
description = "Export annotations"
author = "Example Org"

[backend]
entry = "acoustic_export.plugin:handle"

[capabilities]
host = ["datasets.read"]
"""


def write_manifest(tmp_path, text: str, name: str = "plugin.toml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_valid_toml_manifest(tmp_path):
    manifest = load_manifest(write_manifest(tmp_path, VALID_TOML))

    assert manifest.id == "acoustic-export"
    assert manifest.backend is not None
    assert manifest.backend.entry == "acoustic_export.plugin:handle"
    assert manifest.capabilities.host == ["datasets.read"]


def test_loads_json_manifest(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "id": "json-plugin",
                "name": "JSON",
                "version": "1.0.0",
                "ui": {"entry": "dist/index.html"},
            }
        ),
        encoding="utf-8",
    )

    manifest = load_manifest(path)

    assert manifest.id == "json-plugin"
    assert manifest.ui is not None
    assert manifest.ui.entry == "dist/index.html"


def test_rejects_invalid_id(tmp_path):
    text = VALID_TOML.replace(
        'id = "acoustic-export"', 'id = "Acoustic Export"'
    )

    with pytest.raises(ManifestError, match="id"):
        load_manifest(write_manifest(tmp_path, text))


def test_rejects_unknown_field(tmp_path):
    text = VALID_TOML + '\nunexpected = "value"\n'

    with pytest.raises(ManifestError):
        load_manifest(write_manifest(tmp_path, text))


def test_rejects_manifest_without_entries(tmp_path):
    text = """
id = "empty"
name = "Empty"
version = "1.0.0"
"""

    with pytest.raises(ManifestError, match="backend.entry"):
        load_manifest(write_manifest(tmp_path, text))


def test_rejects_oversized_manifest(tmp_path):
    path = write_manifest(tmp_path, VALID_TOML)
    path.write_text(VALID_TOML + "\n# " + "x" * (64 * 1024), "utf-8")

    with pytest.raises(ManifestError, match="64 KiB"):
        load_manifest(path)


def test_error_does_not_leak_absolute_path(tmp_path):
    path = write_manifest(tmp_path, 'id = "Bad Id"\nname = "x"\n')

    with pytest.raises(ManifestError) as error:
        load_manifest(path)

    assert str(tmp_path) not in str(error.value)


def test_version_is_optional():
    manifest = PluginManifest.model_validate(
        {"id": "noversion", "name": "No version", "ui": {"entry": "x"}}
    )

    assert manifest.version is None


def test_supported_api_version():
    assert is_supported_api_version("1.0") is True
    assert is_supported_api_version("2.0") is False
