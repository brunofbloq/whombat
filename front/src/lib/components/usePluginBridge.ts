import { useCallback, useEffect, useRef, useState } from "react";
import { z } from "zod";

const BridgeMessage = z.discriminatedUnion("type", [
  z.object({
    type: z.literal("plugin.ready"),
    id: z.string(),
    version: z.string().optional(),
  }),
  z.object({
    type: z.literal("plugin.result"),
    id: z.string(),
    ok: z.boolean(),
    data: z.unknown().optional(),
    error: z.string().optional(),
  }),
]);

export type PluginFrameStatus = "loading" | "ready";

/**
 * Bridge between the host page and a sandboxed plugin frame.
 *
 * The frame has no `allow-same-origin`, so it reports the opaque origin
 * `"null"`. Messages are only accepted from that exact frame and origin.
 */
export default function usePluginBridge(pluginId: string) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [status, setStatus] = useState<PluginFrameStatus>("loading");

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const frame = iframeRef.current;
      if (!frame || event.source !== frame.contentWindow) return;
      if (event.origin !== "null") return;

      const parsed = BridgeMessage.safeParse(event.data);
      if (!parsed.success) return;

      if (parsed.data.type === "plugin.ready") {
        if (parsed.data.id !== pluginId) return;
        setStatus("ready");
        frame.contentWindow?.postMessage(
          {
            type: "host.init",
            theme: "light",
            locale: "en",
            capabilities: [],
          },
          "*",
        );
      }
    };

    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [pluginId]);

  const post = useCallback((message: unknown) => {
    iframeRef.current?.contentWindow?.postMessage(message, "*");
  }, []);

  return { iframeRef, status, post };
}
