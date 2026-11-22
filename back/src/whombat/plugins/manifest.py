"""Plugin manifest models and parsing."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

__all__ = [
    "MAX_MANIFEST_BYTES",
    "SUPPORTED_API_VERSIONS",
    "BackendSpec",
    "Capabilities",
    "ManifestError",
    "PluginManifest",
    "UISpec",
    "is_supported_api_version",
    "load_manifest",
]

SUPPORTED_API_VERSIONS = ("1.0",)
"""Plugin platform API versions this build can run."""

MAX_MANIFEST_BYTES = 64 * 1024

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_VERSION_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")


class ManifestError(ValueError):
    """Raised when a plugin manifest cannot be read or validated.

    Messages never contain absolute paths so they are safe to show in
    the plugin list.
    """


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BackendSpec(_StrictModel):
    """Guest process entry point."""

    entry: str
    """``module:attr`` of the guest handler callable."""

    startup_timeout_ms: int = Field(default=10_000, ge=100, le=120_000)
    call_timeout_ms: int = Field(default=5_000, ge=100, le=120_000)


class UISpec(_StrictModel):
    """Optional plugin user interface."""

    entry: str | None = None
    module: str | None = None


class Capabilities(_StrictModel):
    """Capabilities a plugin declares up front."""

    fs_read: list[str] = Field(default_factory=list)
    net: list[str] = Field(default_factory=list)
    host: list[str] = Field(default_factory=list)


class PluginManifest(_StrictModel):
    """Declarative plugin definition."""

    id: str
    name: str
    version: str | None = None
    api_version: str = "1.0"
    description: str | None = None
    author: str | None = None
    attribution: str | None = None
    thumbnail: str | None = None
    homepage: str | None = None
    license: str | None = None
    backend: BackendSpec | None = None
    ui: UISpec | None = None
    capabilities: Capabilities = Field(default_factory=Capabilities)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not _ID_RE.match(value):
            raise ValueError("id must match ^[a-z0-9][a-z0-9-]{1,63}$")
        return value

    @field_validator("version", "api_version")
    @classmethod
    def _validate_version(cls, value: str | None) -> str | None:
        if value is not None and not _VERSION_RE.match(value):
            raise ValueError("must look like 1.0 or 1.0.0")
        return value

    @model_validator(mode="after")
    def _require_entry(self) -> "PluginManifest":
        has_backend = self.backend is not None
        has_ui = self.ui is not None and bool(self.ui.entry)
        if not has_backend and not has_ui:
            raise ValueError(
                "manifest must declare backend.entry or ui.entry"
            )
        return self


def is_supported_api_version(version: str) -> bool:
    """Return True if this build supports the given API version."""
    return version in SUPPORTED_API_VERSIONS


def load_manifest(path: Path) -> PluginManifest:
    """Read and validate a ``.toml`` or ``.json`` manifest."""
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ManifestError(f"cannot read {path.name}") from error

    if len(raw) > MAX_MANIFEST_BYTES:
        raise ManifestError(f"{path.name} is larger than 64 KiB")

    try:
        if path.suffix == ".toml":
            data = tomllib.loads(raw.decode("utf-8"))
        elif path.suffix == ".json":
            data = json.loads(raw.decode("utf-8"))
        else:
            raise ManifestError(f"unsupported manifest type {path.name}")
    except (
        tomllib.TOMLDecodeError,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ) as error:
        raise ManifestError(f"invalid manifest {path.name}") from error

    try:
        return PluginManifest.model_validate(data)
    except ValidationError as error:
        detail = error.errors()[0]
        location = ".".join(str(part) for part in detail["loc"])
        if location:
            message = f"invalid field {location!r} in {path.name}"
        else:
            reason = str(detail.get("msg", "invalid manifest"))
            reason = reason.removeprefix("Value error, ")
            message = f"{reason} in {path.name}"
        raise ManifestError(message) from error
