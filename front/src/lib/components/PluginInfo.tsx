import React from "react";

import Link from "@/lib/components/ui/Link";

const STATUS_STYLES: Record<string, string> = {
  ready: "bg-emerald-100 text-emerald-800",
  disabled: "bg-stone-200 text-stone-700",
  starting: "bg-sky-100 text-sky-800",
  discovered: "bg-stone-200 text-stone-700",
  unhealthy: "bg-amber-100 text-amber-800",
  crashed: "bg-red-100 text-red-800",
  incompatible: "bg-amber-100 text-amber-800",
  failed: "bg-red-100 text-red-800",
};

export default function PluginInfo({
  id,
  name,
  description,
  attribution,
  version,
  thumbnail,
  url,
  status = "disabled",
  trust,
  legacy,
  lastError,
  onEnable,
  onDisable,
  onRestart,
}: {
  id?: string;
  name: string;
  description?: string | null;
  url: string;
  attribution?: string | null;
  version?: string | null;
  thumbnail?: string | null;
  status?: string;
  trust?: string;
  legacy?: boolean;
  lastError?: string | null;
  onEnable?: () => void;
  onDisable?: () => void;
  onRestart?: () => void;
}) {
  const ready = status === "ready";
  const viewHref = id ? `/plugins/view/?id=${encodeURIComponent(id)}` : null;

  return (
    <div className="flex flex-col items-center bg-white rounded-lg border shadow-sm md:flex-row md:max-w-xl border-stone-200 dark:bg-stone-800 dark:border-stone-700">
      {thumbnail && (
        <img
          className="object-cover w-96 h-full md:w-48 md:h-auto md:rounded-none pl-4"
          src={thumbnail}
          alt="Plugin thumbnail"
        />
      )}
      <div className="flex flex-col justify-between p-4 leading-normal">
        <div className="mb-1 flex items-center gap-2">
          <h5 className="text-xl font-bold tracking-tight dark:text-white text-stone-900">
            {name}
          </h5>
          <span
            className={`rounded px-2 py-0.5 text-xs font-medium ${
              STATUS_STYLES[status] ?? STATUS_STYLES.disabled
            }`}
          >
            {status}
          </span>
          {legacy && (
            <span className="rounded bg-purple-100 px-2 py-0.5 text-xs font-medium text-purple-800">
              legacy
            </span>
          )}
        </div>
        <small className="mb-3 text-sm text-stone-500">
          {[version, attribution, trust && `trust: ${trust}`]
            .filter(Boolean)
            .join(" - ")}
        </small>
        <p className="mb-2 text-stone-700 dark:text-stone-400">
          {description ?? ""}
        </p>
        {lastError && <p className="mb-2 text-xs text-red-600">{lastError}</p>}
        <div className="flex flex-wrap items-center gap-2">
          {viewHref && ready ? (
            <Link mode="text" href={viewHref}>
              View
            </Link>
          ) : (
            <span className="cursor-not-allowed text-sm text-stone-400">
              View
            </span>
          )}
          <button
            type="button"
            className="rounded border px-2 py-1 text-sm disabled:opacity-40"
            disabled={ready || status === "incompatible"}
            onClick={onEnable}
          >
            Enable
          </button>
          <button
            type="button"
            className="rounded border px-2 py-1 text-sm disabled:opacity-40"
            disabled={!ready}
            onClick={onDisable}
          >
            Disable
          </button>
          <button
            type="button"
            className="rounded border px-2 py-1 text-sm disabled:opacity-40"
            disabled={!ready}
            onClick={onRestart}
          >
            Restart
          </button>
          <span className="text-xs text-stone-400">{url}</span>
        </div>
      </div>
    </div>
  );
}
