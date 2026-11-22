import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vite-plus/test";

import PluginInfo from "./PluginInfo";

describe("PluginInfo", () => {
  it("lets a ready plugin be viewed", () => {
    render(
      <PluginInfo id="acme" name="Acme" url="/plugins/acme" status="ready" />,
    );

    expect(
      screen.getByRole("link", { name: "View" }).getAttribute("href"),
    ).toBe("/plugins/view?id=acme");
  });

  it("does not link to a disabled plugin", () => {
    render(
      <PluginInfo
        id="acme"
        name="Acme"
        url="/plugins/acme"
        status="disabled"
      />,
    );

    expect(screen.queryByRole("link", { name: "View" })).toBeNull();
    expect(screen.getByText("View")).toBeTruthy();
  });

  it("only enables a disabled plugin", () => {
    const onEnable = vi.fn();
    const onDisable = vi.fn();
    render(
      <PluginInfo
        id="acme"
        name="Acme"
        url="/plugins/acme"
        status="disabled"
        onEnable={onEnable}
        onDisable={onDisable}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Enable" }));

    expect(onEnable).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Disable" })).toHaveProperty(
      "disabled",
      true,
    );
  });

  it("shows the last error and the legacy marker", () => {
    render(
      <PluginInfo
        name="hello"
        url="/plugins/pages/hello/"
        status="ready"
        legacy
        lastError="module failed"
      />,
    );

    expect(screen.getByText("legacy")).toBeTruthy();
    expect(screen.getByText("module failed")).toBeTruthy();
  });
});
