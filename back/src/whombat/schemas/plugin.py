"""Schemas for handling plugin information."""

from pydantic import BaseModel, Field

__all__ = [
    "PluginInfo",
    "PluginStatus",
]


class PluginStatus(BaseModel):
    """Full plugin state exposed to the UI."""

    id: str = Field(..., description="Plugin identifier.")
    name: str = Field(..., description="Human readable plugin name.")
    url: str = Field(..., description="Route to the plugin pages.")
    description: str | None = Field(
        default=None, description="Plugin description."
    )
    version: str | None = Field(default=None, description="Plugin version.")
    api_version: str | None = Field(
        default=None, description="Plugin platform API version."
    )
    thumbnail: str | None = Field(
        default=None, description="Plugin thumbnail."
    )
    attribution: str | None = Field(
        default=None, description="Plugin attribution."
    )
    author: str | None = Field(default=None, description="Plugin author.")
    status: str = Field(
        default="disabled",
        description="Lifecycle status of the plugin.",
    )
    trust: str = Field(
        default="untrusted",
        description="Trust level of the plugin.",
    )
    capabilities: list[str] = Field(
        default_factory=list,
        description="Capabilities the plugin declares.",
    )
    granted: list[str] = Field(
        default_factory=list,
        description="Capabilities the user approved.",
    )
    ui_url: str | None = Field(
        default=None, description="URL of the sandboxed plugin UI."
    )
    legacy: bool = Field(
        default=False, description="True for legacy in-process plugins."
    )
    last_error: str | None = Field(
        default=None, description="Reason the plugin is not usable."
    )


# Backwards compatible alias for the original list response.
PluginInfo = PluginStatus
