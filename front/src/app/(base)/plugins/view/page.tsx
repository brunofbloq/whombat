"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import usePlugins from "@/app/hooks/api/usePlugins";
import PluginFrame from "@/lib/components/PluginFrame";
import Link from "@/lib/components/ui/Link";

function PluginView() {
  const params = useSearchParams();
  const pluginId = params.get("id") ?? "";
  const { items, query } = usePlugins();
  const plugin = items?.find((item) => (item.id ?? item.name) === pluginId);

  if (query.isLoading) {
    return <div className="p-4">Loading...</div>;
  }

  if (!plugin) {
    return (
      <div className="p-4">
        <p>Unknown plugin {pluginId}.</p>
        <Link mode="text" href="/plugins">
          Back to plugins
        </Link>
      </div>
    );
  }

  if (plugin.status !== "ready" || !plugin.ui_url) {
    return (
      <div className="flex flex-col gap-2 p-4">
        <h1 className="text-xl font-bold">{plugin.name}</h1>
        <p>
          This plugin is not available: {plugin.status}
          {plugin.last_error ? ` (${plugin.last_error})` : ""}.
        </p>
        <Link mode="text" href="/plugins">
          Back to plugins
        </Link>
      </div>
    );
  }

  return (
    <div className="p-4">
      <PluginFrame
        pluginId={plugin.id ?? plugin.name}
        src={plugin.ui_url}
        title={plugin.name}
      />
    </div>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<div className="p-4">Loading...</div>}>
      <PluginView />
    </Suspense>
  );
}
