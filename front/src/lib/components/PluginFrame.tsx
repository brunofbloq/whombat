import usePluginBridge from "@/lib/components/usePluginBridge";

/**
 * Render a plugin UI inside a sandboxed frame.
 *
 * `allow-scripts` without `allow-same-origin` gives the bundle an opaque
 * origin, so it cannot read host cookies or storage, and no plugin code
 * ever runs in the host document.
 */
export default function PluginFrame({
  pluginId,
  src,
  title,
}: {
  pluginId: string;
  src: string;
  title: string;
}) {
  const { iframeRef, status } = usePluginBridge(pluginId);

  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-stone-500">Sandboxed plugin view: {status}</p>
      <iframe
        ref={iframeRef}
        sandbox="allow-scripts"
        referrerPolicy="no-referrer"
        title={title}
        src={src}
        className="h-[70vh] w-full rounded border"
      />
    </div>
  );
}
