# Plugins

Whombat can load external plugins without trusting them. A plugin declares
what it needs in a manifest, runs in its own process, and reaches Whombat data
only through capability-checked host calls. Its user interface is served as
static files and rendered in a sandboxed frame.

This guide documents the contract. The implementation lives in
`src/whombat/plugins/`.

## Two plugin styles

| Style | How it runs | Trust | Status |
|---|---|---|---|
| Manifest plugin | Guest process + sandboxed UI | `untrusted` | Current |
| Legacy `whombat_*` module | Imported into the backend process | `trusted` | Deprecated |

Legacy modules keep working during the deprecation window. They are imported at
start-up, mounted at `/plugins/routes/<name>/` (and `/plugins/pages/<name>/` for
`statics/`), reported as `legacy`, and emit a `DeprecationWarning`. New work
should use manifests.

## Layout

```
<plugins_dir>/<plugin-id>/
  plugin.toml          # required manifest
  <package>/           # optional guest code, imported only by the guest
  dist/                # optional UI bundle
```

`plugins_dir` defaults to `~/.whombat/plugins` and is configurable with
`WHOMBAT_PLUGINS_DIR`.

## Manifest

```toml
id = "acoustic-export"
name = "Acoustic Export"
version = "0.3.0"
api_version = "1.0"
description = "Export annotations to third-party formats"
author = "Example Org"
homepage = "https://example.org"
license = "MIT"

[backend]
entry = "acoustic_export.plugin:handle"   # module:attr
startup_timeout_ms = 10000
call_timeout_ms = 5000

[ui]
entry = "dist/index.html"
module = "dist/plugin.js"

[capabilities]
fs_read = ["/projects/acoustic"]
net = ["api.example.org"]
host = ["datasets.read", "recordings.read"]
```

Rules:

- `id` matches `^[a-z0-9][a-z0-9-]{1,63}$` and is the plugin's identity. Two
  plugins with the same `id` are both reported as `failed` (`duplicate id`).
- `version` and `api_version` look like `1.0` or `1.0.0`. `version` is optional;
  a missing version is reported as `null`.
- `api_version` must be supported by the running Whombat build (`1.0` today).
  Anything else is `incompatible` and is never imported or started.
- At least one of `[backend].entry` or `[ui].entry` is required.
- Unknown keys are a validation error (`extra="forbid"`).
- The manifest may be `plugin.toml` or `manifest.json`, and is limited to 64 KiB.
- Manifests are declarative: no executable code, no `pip install`.

Discovery also supports Python entry points in the `whombat.plugins` group. An
entry point must resolve to a manifest path or a mapping with the manifest
fields.

## Backend guest

`[backend].entry` is `module:attr`. The attribute must be callable as
`handle(method, params) -> result` (sync or async). The guest process is started
with `python -m whombat.plugins._runner`, with the plugin directory on
`sys.path` and a scrubbed environment: only `PATH`, `SYSTEMROOT`, `WINDIR`,
`TEMP`, `TMP`, `COMSPEC`, `PATHEXT`, `LANG`, and the plugin's own `PYTHONPATH`.
No `WHOMBAT_*` variables, database URLs, or settings are inherited.

Transport is newline-delimited JSON-RPC 2.0 over stdin/stdout.

### Host → guest methods

| Method | Purpose |
|---|---|
| `plugin.describe` | Handshake; must return a JSON object |
| `plugin.health` | Liveness and dependency checks |
| `plugin.shutdown` | Graceful stop |
| `invoke` | Proxied call: `{"operation": "...", "args": {...}}` |

### Guest → host

```json
{"jsonrpc":"2.0","id":"7","method":"host.call","params":{"capability":"datasets.read","operation":"list","args":{"limit":20}}}
```

Ship operations:

| Capability | Operation | Result |
|---|---|---|
| `datasets.read` | `list` | `{"datasets": [{"id": "...", "name": "..."}]}` |
| `recordings.read` | `count` | `{"count": 42}` |

A call for any other capability, or one that was not granted, fails with
`-32001` and is written to the audit log. Undeclared capabilities cannot be
granted at runtime.

### Error codes

| Code | Meaning | HTTP mapping |
|---|---|---|
| `-32601` | Unknown method or operation | 400 |
| `-32001` | Capability denied | 403 |
| `-32002` | Call timed out | 504 |
| `-32003` | Invalid params | 400 |
| `-32004` | Guest unavailable | 503 |

## Lifecycle

Statuses: `discovered`, `disabled`, `starting`, `ready`, `unhealthy`,
`crashed`, `incompatible`, `failed`.

- New plugins are `disabled`. Nothing from the plugin directory is imported or
  executed until the user enables it.
- Enabling starts the guest, completes the describe handshake, and marks the
  plugin `ready`. Grants are persisted in `<plugins_dir>/.grants.json` together
  with the manifest version and declared capabilities.
- Changing the declared capabilities or the version invalidates the stored
  grants; the plugin must be approved again.
- A crash or timeout stops that plugin only; the rest of the app keeps serving
  and the plugin reports `failed` or `unhealthy` with a message.
- Disabling, restarting, and uninstalling work without restarting Whombat.
- On app shutdown every guest is stopped.

## HTTP API

All routes are authenticated and mounted under `/api/v1/plugins`:

| Method | Path |
|---|---|
| GET | `/list/` |
| GET | `/{id}/status/` |
| POST | `/{id}/enable/` (optional body `{"grants": ["datasets.read"]}`) |
| POST | `/{id}/disable/` |
| POST | `/{id}/restart/` |
| DELETE | `/{id}/` |
| GET | `/{id}/health/` |
| POST | `/{id}/invoke/` |

`PluginStatus` extends the original list entry, so existing consumers keep
working:

```json
{
  "id": "acoustic-export",
  "name": "Acoustic Export",
  "version": "0.3.0",
  "api_version": "1.0",
  "description": "...",
  "author": "...",
  "attribution": null,
  "thumbnail": null,
  "url": "/plugins/acoustic-export",
  "status": "disabled",
  "trust": "untrusted",
  "capabilities": ["datasets.read"],
  "granted": [],
  "ui_url": "/plugins/assets/acoustic-export/dist/index.html",
  "legacy": false,
  "last_error": null
}
```

## UI contract

Assets under the plugin directory are served at
`/plugins/assets/<plugin-id>/<path>`. Resolution is confined: absolute paths,
`..` segments, and symlinks that escape the plugin root return 404.

The UI renders in an iframe with `sandbox="allow-scripts"` and no
`allow-same-origin`, so the bundle gets an opaque origin: it cannot read host
cookies, `localStorage`, or `IndexedDB`, and no plugin script runs in the host
document. The host page opens it at `/plugins/view/?id=<plugin-id>` and only for
`ready` plugins.

Handshake:

1. The frame posts `{type: "plugin.ready", id, version}`.
2. The host validates `event.source`, requires `event.origin === "null"`, checks
   the `id` against the opened plugin, and replies with
   `{type: "host.init", theme, locale, capabilities}`.
3. UI calls must go through the message bridge; direct DOM or storage access is
   not part of the contract.

## Security model

- Untrusted backend code is never imported by the host.
- Guests get no credentials, session, database URL, or settings object.
- Capability checks fail closed for undeclared or ungranted operations.
- Guest output is size-bounded and stderr is kept in a small ring buffer.
- Every enable/disable and every denied capability is logged on the
  `whombat.plugins.audit` logger.
- Network and filesystem capabilities are declared but not yet enforced by the
  host; a guest can still make its own outbound calls. Treat them as an
  inventory of intent, not a sandbox boundary.

## Example guest

```python
# acoustic_export/plugin.py
def handle(method: str, params: dict):
    if method == "plugin.describe":
        return {"id": "acoustic-export", "version": "0.3.0"}
    if method == "plugin.health":
        return {"status": "ok"}
    if method == "invoke" and params["operation"] == "ping":
        return {"pong": True}
    raise ValueError("unsupported")
```

## Migrating a legacy plugin

1. Move the code into its own directory with a `plugin.toml`.
2. Replace `APIRouter` endpoints with `invoke` operations, or with brokered host
   calls if you need Whombat data.
3. Move `statics/` into `dist/` and declare `[ui].entry`.
4. Drop the `whombat_` module from the installed environment once the manifest
   version is in place.
