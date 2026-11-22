import { describe, expect, it } from "vite-plus/test";

import { PluginHealthSchema, PluginSchema } from "./plugins";

const fullStatus = {
  id: "acoustic-export",
  name: "Acoustic Export",
  version: "0.3.0",
  api_version: "1.0",
  description: null,
  author: null,
  attribution: null,
  thumbnail: null,
  url: "/plugins/acoustic-export",
  status: "disabled",
  trust: "untrusted",
  capabilities: ["datasets.read"],
  granted: [],
  ui_url: "/plugins/assets/acoustic-export/dist/index.html",
  legacy: false,
  last_error: null,
};

describe("PluginSchema", () => {
  it("accepts a full plugin status", () => {
    expect(PluginSchema.parse(fullStatus).status).toBe("disabled");
  });

  it("accepts the legacy payload without the new fields", () => {
    const legacy = { name: "hello", url: "/plugins/pages/hello/" };

    const parsed = PluginSchema.parse(legacy);

    expect(parsed.status).toBeUndefined();
    expect(parsed.description).toBeUndefined();
  });

  it("rejects an unknown status", () => {
    expect(() =>
      PluginSchema.parse({ ...fullStatus, status: "exploded" }),
    ).toThrow();
  });

  it("accepts a null description", () => {
    expect(PluginSchema.parse(fullStatus).description).toBeNull();
  });
});

describe("PluginHealthSchema", () => {
  it("parses a health payload", () => {
    const health = PluginHealthSchema.parse({
      status: "ready",
      checks: [{ name: "guest", ok: true }],
      uptime_ms: 1200,
      last_error: null,
    });

    expect(health.uptime_ms).toBe(1200);
  });
});
