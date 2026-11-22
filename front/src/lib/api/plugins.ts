import { AxiosInstance } from "axios";
import { z } from "zod";

export const PluginStatusEnum = z.enum([
  "discovered",
  "disabled",
  "starting",
  "ready",
  "unhealthy",
  "crashed",
  "incompatible",
  "failed",
]);

export const PluginTrustEnum = z.enum(["trusted", "untrusted"]);

export const PluginSchema = z.object({
  id: z.string().optional(),
  name: z.string(),
  version: z.string().nullable().optional(),
  attribution: z.string().nullable().optional(),
  thumbnail: z.string().nullable().optional(),
  description: z.string().nullable().optional(),
  author: z.string().nullable().optional(),
  api_version: z.string().nullable().optional(),
  status: PluginStatusEnum.optional(),
  trust: PluginTrustEnum.optional(),
  legacy: z.boolean().optional(),
  capabilities: z.array(z.string()).optional(),
  granted: z.array(z.string()).optional(),
  ui_url: z.string().nullable().optional(),
  last_error: z.string().nullable().optional(),
  url: z.string(),
});

export type Plugin = z.infer<typeof PluginSchema>;
export type PluginStatus = Plugin;
export type PluginState = z.infer<typeof PluginStatusEnum>;
export type PluginTrust = z.infer<typeof PluginTrustEnum>;

export const PluginHealthSchema = z.object({
  status: z.string(),
  checks: z.array(z.record(z.string(), z.unknown())).optional(),
  uptime_ms: z.number().nullable().optional(),
  last_error: z.string().nullable().optional(),
});

export type PluginHealth = z.infer<typeof PluginHealthSchema>;

const DEFAULT_ENDPOINTS = {
  list: "/api/v1/plugins/list/",
  status: (id: string) => `/api/v1/plugins/${encodeURIComponent(id)}/status/`,
  enable: (id: string) => `/api/v1/plugins/${encodeURIComponent(id)}/enable/`,
  disable: (id: string) => `/api/v1/plugins/${encodeURIComponent(id)}/disable/`,
  restart: (id: string) => `/api/v1/plugins/${encodeURIComponent(id)}/restart/`,
  health: (id: string) => `/api/v1/plugins/${encodeURIComponent(id)}/health/`,
};

export function registerPluginsAPI(
  instance: AxiosInstance,
  endpoints: typeof DEFAULT_ENDPOINTS = DEFAULT_ENDPOINTS,
) {
  async function getPlugins(): Promise<Plugin[]> {
    const { data } = await instance.get(endpoints.list);
    return z.array(PluginSchema).parse(data);
  }

  async function getPlugin(id: string): Promise<Plugin> {
    const { data } = await instance.get(endpoints.status(id));
    return PluginSchema.parse(data);
  }

  async function enablePlugin(id: string, grants?: string[]): Promise<Plugin> {
    const { data } = await instance.post(
      endpoints.enable(id),
      grants ? { grants } : undefined,
    );
    return PluginSchema.parse(data);
  }

  async function disablePlugin(id: string): Promise<Plugin> {
    const { data } = await instance.post(endpoints.disable(id));
    return PluginSchema.parse(data);
  }

  async function restartPlugin(id: string): Promise<Plugin> {
    const { data } = await instance.post(endpoints.restart(id));
    return PluginSchema.parse(data);
  }

  async function getPluginHealth(id: string): Promise<PluginHealth> {
    const { data } = await instance.get(endpoints.health(id));
    return PluginHealthSchema.parse(data);
  }

  return {
    get: getPlugins,
    status: getPlugin,
    enable: enablePlugin,
    disable: disablePlugin,
    restart: restartPlugin,
    health: getPluginHealth,
  } as const;
}
