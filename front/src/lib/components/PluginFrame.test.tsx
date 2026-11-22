import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vite-plus/test";

import PluginFrame from "./PluginFrame";

function frame() {
  const element = screen.getByTitle("Acme") as HTMLIFrameElement;
  return element;
}

describe("PluginFrame", () => {
  it("renders the plugin inside a script-only sandbox", () => {
    render(
      <PluginFrame
        pluginId="acme"
        src="/plugins/assets/acme/i.html"
        title="Acme"
      />,
    );

    const element = frame();

    expect(element.getAttribute("sandbox")).toBe("allow-scripts");
    expect(element.getAttribute("src")).toBe("/plugins/assets/acme/i.html");
    expect(element.getAttribute("srcdoc")).toBeNull();
  });

  it("loads the bundle from another origin inside a sandboxed frame", () => {
    render(
      <PluginFrame
        pluginId="acme"
        src="http://127.0.0.1:49152/acme/index.html"
        title="Acme"
      />,
    );

    const element = frame();

    expect(element.src).toBe("http://127.0.0.1:49152/acme/index.html");
    expect(window.location.origin).not.toBe("http://127.0.0.1:49152");
    expect(element.getAttribute("sandbox")).toBe("allow-scripts");
    expect(element.getAttribute("sandbox")).not.toContain("allow-same-origin");
  });

  it("ignores messages that are not from the frame origin", () => {
    render(
      <PluginFrame
        pluginId="acme"
        src="/plugins/assets/acme/i.html"
        title="Acme"
      />,
    );

    window.dispatchEvent(
      new MessageEvent("message", {
        data: { type: "plugin.ready", id: "acme" },
        origin: "http://localhost",
        source: window,
      }),
    );

    expect(screen.getByText(/loading/)).toBeTruthy();
  });

  it("ignores a ready message for another plugin", async () => {
    render(
      <PluginFrame
        pluginId="acme"
        src="/plugins/assets/acme/i.html"
        title="Acme"
      />,
    );

    const element = frame();
    Object.defineProperty(element, "contentWindow", {
      value: window,
      configurable: true,
    });
    const postMessage = vi.spyOn(window, "postMessage");

    window.dispatchEvent(
      new MessageEvent("message", {
        data: { type: "plugin.ready", id: "other" },
        origin: "null",
        source: window,
      }),
    );

    expect(screen.getByText(/loading/)).toBeTruthy();
    expect(postMessage).not.toHaveBeenCalled();
  });

  it("completes the handshake for the matching plugin", async () => {
    render(
      <PluginFrame
        pluginId="acme"
        src="/plugins/assets/acme/i.html"
        title="Acme"
      />,
    );

    const element = frame();
    Object.defineProperty(element, "contentWindow", {
      value: window,
      configurable: true,
    });
    const postMessage = vi.spyOn(window, "postMessage");

    window.dispatchEvent(
      new MessageEvent("message", {
        data: { type: "plugin.ready", id: "acme", version: "0.1.0" },
        origin: "null",
        source: window,
      }),
    );

    await waitFor(() => expect(screen.getByText(/ready/)).toBeTruthy());
    expect(postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: "host.init" }),
      "*",
    );
  });

  it("ignores malformed messages", () => {
    render(
      <PluginFrame
        pluginId="acme"
        src="/plugins/assets/acme/i.html"
        title="Acme"
      />,
    );

    const element = frame();
    Object.defineProperty(element, "contentWindow", {
      value: window,
      configurable: true,
    });

    window.dispatchEvent(
      new MessageEvent("message", {
        data: { type: "plugin.ready" },
        origin: "null",
        source: window,
      }),
    );

    expect(screen.getByText(/loading/)).toBeTruthy();
  });
});
