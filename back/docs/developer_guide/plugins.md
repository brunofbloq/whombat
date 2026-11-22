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
transport = "stdio"                        # or "loopback"
memory_mb = 512                            # optional hard ceiling
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

Transport is newline-delimited JSON-RPC 2.0 over stdin/stdout (`stdio`, the
default), or HTTP JSON-RPC on an ephemeral `127.0.0.1` port (`loopback`).

For `loopback` the host generates a random bearer token per launch and passes it
to the guest in `WHOMBAT_PLUGIN_TOKEN`. The guest binds `127.0.0.1:0`, prints
`{"event": "ready", "port": <n>}` on stdout, and serves `POST /rpc`. Every
request must carry `Authorization: Bearer <token>`; a missing or wrong token
gets `401` and no JSON-RPC is executed. The host refuses to talk to a plugin
that rejects its token.

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
| `fs.read` | `read_text` | `{"path": "...", "text": "..."}` for a file inside the declared `fs_read` roots |
| `net.get` | `fetch` | `{"status": 200, "text": "..."}` for an http(s) URL on a declared `net` host |

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

### Resource limits

- `memory_mb` caps the guest. POSIX sets `RLIMIT_AS` and disables core dumps
  from the runner before plugin code is imported; Windows assigns the child to a
  Job Object with `JOB_OBJECT_LIMIT_PROCESS_MEMORY`, so an oversized allocation
  fails inside the guest instead of exhausting the machine. If the limit cannot
  be applied, the host refuses to start the plugin.
- `call_timeout_ms` bounds each RPC, and responses are capped at 1 MiB.
- `fs_read` is enforced for the brokered `fs.read/read_text` operation: paths are
  resolved inside the declared roots, and anything escaping them is denied with
  `-32001`.
- `net` is enforced for the brokered `net.get/fetch` operation: only `http` and
  `https` URLs are accepted, the host must match a declared entry exactly or via
  a `*.example.org` pattern, URLs with embedded credentials are rejected,
  redirects are refused, and responses are capped at 64 KiB.
- The guest process installs a Python audit hook before importing plugin code
  (`guest_policy.py`). It denies `open` outside the plugin root, the interpreter
  installation and the declared `fs_read` roots; denies `socket.connect` to
  hosts not declared in `net`; denies process creation (`subprocess.Popen`,
  `os.system`, `os.exec*`, `os.spawn*`); and denies loading new native libraries
  (`ctypes.dlopen`, `ctypes.dlsym`). A plugin that tries any of these gets
  `PermissionError`.
- The guest process itself is not filesystem- or network-jailed: plugin code
  that opens a file or socket directly is outside the broker. `fs_read` and
  `net` constrain host-brokered access, not the OS.

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
  and the plugin reports `crashed` or `failed` with a message.
- A guest that stays idle for `plugin_idle_timeout_seconds` (default 300, `0`
  disables the sweep) is stopped and the plugin returns to `disabled`.
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
  "ui_url": "http://127.0.0.1:49152/acoustic-export/dist/index.html",
  "legacy": false,
  "last_error": null
}
```

## UI contract

Plugin assets are served from a **separate loopback origin**, not from the
Whombat origin. At start-up (or on the first request after a hot refresh)
Whombat starts a small asset server on an ephemeral `127.0.0.1` port and
reports each plugin's `ui_url` as an absolute URL such as
`http://127.0.0.1:49152/acoustic-export/dist/index.html`. The application
origin no longer serves plugin HTML.

Resolution on that origin is confined: absolute paths, `..` segments, and
symlinks that escape the plugin root return 404. The origin sets no cookies and
sends no CORS headers, so a page there cannot act as the user.

The UI renders in an iframe with `sandbox="allow-scripts"` and no
`allow-same-origin`. Even though the asset origin is already separate, the
sandbox gives the frame an opaque origin: it cannot read host cookies,
`localStorage`, or `IndexedDB`, and no plugin script runs in the host document.
The host page opens it at `/plugins/view/?id=<plugin-id>` and only for `ready`
plugins.

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

### Known gaps

These spec items are not implemented yet and are tracked here so the contract
does not overpromise:

- **No OS jail.** The runtime policy is a Python audit hook. It blocks the
  usual escape hatches (`ctypes`, subprocesses, arbitrary opens and connects),
  but a determined bypass using already-loaded native code or OS primitives is
  still possible. A true jail needs Windows AppContainer/restricted token and
  Linux mount + network namespaces/seccomp, which is not implemented.
- **Direct guest sockets.** The network hook blocks `socket.connect` to
  undeclared hosts, but asyncio's own wake-up socket is created before the hook
  and loopback traffic is not otherwise modelled. Plugins that need network
  access should use `net.get/fetch`, which is fully checked.

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
