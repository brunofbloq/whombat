"use client";

import usePlugins from "@/app/hooks/api/usePlugins";
import PluginInfo from "@/lib/components/PluginInfo";
import Hero from "@/lib/components/ui/Hero";

export default function Page() {
  const {
    items: plugins,
    query: { isLoading, isError },
    actions,
  } = usePlugins();

  if (isLoading) {
    return <div>Loading...</div>;
  }

  if (isError) {
    return <div>Error</div>;
  }

  return (
    <>
      <Hero text="Whombat Plugins" />
      <div className="container p-4 mx-auto">
        <div>
          <p className="text-lg text-stone-700 dark:text-stone-300">
            External plugins are disabled until you enable them and approve the
            capabilities they declare.
          </p>
        </div>
        <div className="grid grid-cols-2 gap-4 py-6">
          {plugins?.map((plugin) => {
            const pluginId = plugin.id ?? plugin.name;
            return (
              <PluginInfo
                key={pluginId}
                id={pluginId}
                name={plugin.name}
                description={plugin.description}
                attribution={plugin.attribution}
                version={plugin.version}
                thumbnail={plugin.thumbnail}
                url={plugin.url}
                status={plugin.status}
                trust={plugin.trust}
                legacy={plugin.legacy}
                lastError={plugin.last_error}
                onEnable={() =>
                  actions.enable.mutate({
                    id: pluginId,
                    grants: plugin.capabilities,
                  })
                }
                onDisable={() => actions.disable.mutate(pluginId)}
                onRestart={() => actions.restart.mutate(pluginId)}
              />
            );
          })}
        </div>
      </div>
    </>
  );
}
