# Plugins

Whombat can run external plugins without trusting them. A plugin declares what
it needs in a manifest, runs in its own process, and reaches Whombat data only
through capability-checked host calls. Its user interface is served from a
separate loopback origin and rendered in a sandboxed frame.

This page is the reference for plugin authors and maintainers. The
implementation lives in `src/whombat/plugins/`.

## Why this architecture

Plugins used to be Python modules imported into the Whombat process. That was
convenient and it is still supported for legacy code, but it has no security
boundary: a plugin shares the database session, the settings object and the
event loop, and its UI is served from the application origin.

| Concern | Legacy `whombat_*` module | Manifest plugin |
|---|---|---|
| Runs in | Whombat's process | its own process |
| Sees the database | yes, directly | only through brokers |
| Can block the app | yes | no |
| Can crash the app | yes | no, it is reported as `crashed` |
| Dependency conflicts | shares Whombat's virtualenv | isolated in its own directory |
| UI origin | same as the app | separate loopback port + sandbox |
| Trust | `trusted`, always on | `untrusted`, disabled until approved |
| Lifecycle | restart the app | enable / disable / restart / remove live |

The practical wins:

- **Blast-radius containment** — a buggy plugin burns its own process, not the
  annotation session you have open.
- **Explicit authority** — a plugin cannot read datasets unless you granted
  `datasets.read`, and cannot reach the network outside its declared hosts.
- **No dependency roulette** — installing a plugin no longer mutates Whombat's
  Python environment.
- **Safe UI** — plugin JavaScript runs in a sandboxed frame on another origin,
  so it cannot read Whombat cookies or call authenticated endpoints.
- **Operable** — every plugin has a status, health check, and audit trail.

## Architecture

```
<plugins_dir>/<id>/plugin.toml
        │
        ▼
discover_plugins()            entry points + plugins dir + legacy modules
        │                     (manifests only, no code imported)
        ▼
PluginRegistry                state machine, grants, audit
        │
        ├── enable() ──► GuestProcess ── stdio or loopback JSON-RPC ──► plugin code
        │                    │
        │                    └── host.call ──► capability broker ──► Whombat
        │
        └── ui_url ──► PluginUIServer (127.0.0.1:<ephemeral>) ──► sandboxed iframe
```

Key modules:

| Module | Responsibility |
|---|---|
| `plugins/manifest.py` | parse and validate `plugin.toml` / `manifest.json` |
| `plugins/discovery.py` | find plugins; report bad ones instead of dropping them |
| `plugins/guest.py` | spawn, supervise and talk to the guest process |
| `plugins/registry.py` | status, grants, lifecycle, brokers, audit |
| `plugins/guest_policy.py` | in-guest audit hook (files, sockets, processes) |
| `plugins/ui_server.py` | separate origin that serves plugin assets |
| `plugins/legacy.py` | the old in-process loader (deprecated) |

## Lifecycle

| Status | Meaning |
|---|---|
| `discovered` | found, not yet classified |
| `disabled` | known and inert; nothing imported or started |
| `starting` | guest spawned, waiting for its handshake |
| `ready` | guest answered `plugin.describe`; UI may be opened |
| `unhealthy` | health call failed |
| `crashed` | the guest exited unexpectedly; captured error is in `last_error` |
| `incompatible` | `api_version` is not supported by this build |
| `failed` | bad manifest, duplicate id, missing entry, or start-up failure |

New plugins start `disabled`. Enabling approves the declared capabilities and
starts the guest. Disabling stops it and revokes the grants. A guest that stays
idle for `plugin_idle_timeout_seconds` (default 300, `0` disables the sweep) is
stopped automatically. On app shutdown every guest is stopped.

## Manifest reference

```toml
id = "acoustic-export"
name = "Acoustic Export"
version = "0.3.0"
api_version = "1.0"
description = "Export annotations to third-party formats"
author = "Example Org"
attribution = "Example Org"
thumbnail = "thumb.png"
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
net = ["api.example.org", "*.example.org"]
host = ["datasets.read", "recordings.read"]
```

| Field | Required | Notes |
|---|---|---|
| `id` | yes | `^[a-z0-9][a-z0-9-]{1,63}$`, unique across all sources |
| `name` | yes | shown in the UI |
| `version` | no | `1.0` or `1.0.0`; may be omitted |
| `api_version` | no | defaults to `1.0`; anything else is `incompatible` |
| `[backend].entry` | one of backend/ui | `module:attr` of a `handle(method, params)` callable |
| `[backend].transport` | no | `stdio` (default) or `loopback` |
| `[backend].memory_mb` | no | 16–32768; POSIX `RLIMIT_AS`, Windows Job Object |
| `[ui].entry` | one of backend/ui | path to the HTML entry inside the plugin directory |
| `[capabilities].fs_read` | no | absolute roots the `fs.read` broker may touch |
| `[capabilities].net` | no | hosts the `net.get` broker may fetch |
| `[capabilities].host` | no | broker capabilities requested at enable time |

Rules: unknown keys are rejected, the manifest is capped at 64 KiB, and at least
one of `backend.entry` / `ui.entry` must be present.

### Where plugins are discovered

1. Python entry points in the `whombat.plugins` group, resolving to a manifest
   path or a mapping with the manifest fields.
2. `<plugins_dir>/<id>/plugin.toml` (or `manifest.json`). `plugins_dir` defaults
   to `~/.whombat/plugins` and is configurable with `WHOMBAT_PLUGINS_DIR`.
3. Legacy `whombat_*` modules on the Python path (deprecated).

Two plugins with the same `id` are both reported as `failed` with
`duplicate id`; neither runs.

## Guest contract

`[backend].entry` resolves to a callable:

```python
def handle(method: str, params: dict):
    ...
```

It may be `async`. Supported methods:

| Method | Direction | Purpose |
|---|---|---|
| `plugin.describe` | host → guest | handshake; return a dict |
| `plugin.health` | host → guest | liveness; return a dict |
| `plugin.invoke` | host → guest | `params = {"operation": ..., "args": {...}}` |
| `plugin.shutdown` | host → guest | graceful stop |
| `host.call` | guest → host | broker request (capability checked) |

Transports:

- `stdio` — newline-delimited JSON-RPC 2.0 over stdin/stdout.
- `loopback` — HTTP JSON-RPC on `127.0.0.1:0`, authenticated with a random
  per-launch bearer token passed in `WHOMBAT_PLUGIN_TOKEN`; the guest prints
  `{"event": "ready", "port": <n>}` then serves `POST /rpc`. Missing or wrong
  tokens get `401`.

The guest runs with a scrubbed environment: `PATH`, `SYSTEMROOT`, `WINDIR`,
`TEMP`, `TMP`, `COMSPEC`, `PATHEXT`, `LANG`, its own `PYTHONPATH`, and the
plugin token. No Whombat settings, database URL or credentials.

## HTTP API

All routes are authenticated (session cookie, like the rest of `/api/v1`) and
live under `/api/v1/plugins`.

| Method | Path | Body | Result |
|---|---|---|---|
| GET | `/list/` | – | `PluginStatus[]` |
| GET | `/{id}/status/` | – | `PluginStatus` |
| POST | `/{id}/enable/` | `{"grants": ["datasets.read"]}` (optional) | `PluginStatus` |
| POST | `/{id}/disable/` | – | `PluginStatus` |
| POST | `/{id}/restart/` | – | `PluginStatus` |
| GET | `/{id}/health/` | – | `{status, checks, uptime_ms, last_error}` |
| POST | `/{id}/invoke/` | `{"operation": "...", "args": {...}}` | `{"result": ...}` |
| DELETE | `/{id}/` | – | `{id, status: "removed"}` |

`PluginStatus`:

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

Plugin assets are served from that loopback origin at `/{id}/<path>`, not from
the Whombat origin. Path traversal and symlink escapes return 404.

Error mapping for `invoke` and brokers: `-32001` → 403, `-32002` → 504,
`-32003` → 400, `-32004` → 503, `-32601` → 400.

## Brokers

A guest asks the host for work with:

```json
{"jsonrpc":"2.0","id":"7","method":"host.call","params":{"capability":"datasets.read","operation":"list","args":{"limit":20}}}
```

| Capability | Operation | Args | Result |
|---|---|---|---|
| `datasets.read` | `list` | `{"limit"?: int}` | `{"datasets": [{"id", "name"}]}` |
| `recordings.read` | `count` | – | `{"count": int}` |
| `fs.read` | `read_text` | `{"path": "relative/or/absolute"}` | `{"path", "text"}` (≤ 64 KiB) |
| `net.get` | `fetch` | `{"url": "https://..."}` | `{"status", "text"}` (≤ 64 KiB) |

Rules:

- The capability must be declared in `capabilities.host` **and** approved at
  enable time; otherwise `-32001`, and the denial is written to the
  `whombat.plugins.audit` logger.
- `fs.read` only accepts paths inside the manifest's `fs_read` roots.
- `net.get` accepts `http`/`https`, only declared hosts (exact or `*.domain`),
  rejects credentials in the URL and redirects, and times out after 5 s.
- Unknown operations fail with `-32601`.

Adding a new broker is host work: register a handler in
`_HOST_OPERATIONS` in `plugins/registry.py` with the capability/operation pair,
document it here, and add a test. Keep capabilities narrow.

## UI contract

`ui_url` is absolute (`http://127.0.0.1:<port>/<id>/<entry>`). The host opens it
only for `ready` plugins, at `/plugins/view/?id=<id>`, inside:

```html
<iframe sandbox="allow-scripts" referrerpolicy="no-referrer" src="<ui_url>"></iframe>
```

Without `allow-same-origin` the frame gets an opaque origin: it cannot read host
cookies, `localStorage` or `IndexedDB`, and no plugin script runs in the host
document.

Handshake:

1. Frame posts `{type: "plugin.ready", id, version}`.
2. Host validates `event.source`, requires `event.origin === "null"`, checks
   `id`, then replies `{type: "host.init", theme, locale, capabilities}`.
3. Calls go through the bridge only; direct DOM or storage access is not part
   of the contract.

## Security model

- Untrusted backend code is never imported by the host.
- Guests get no credentials, session, database URL or settings.
- Guests install a Python audit hook before plugin code is imported. It denies:
  `open` outside the plugin root, the interpreter and the `fs_read` roots;
  `socket.connect` to hosts outside `net`; process creation
  (`subprocess.Popen`, `os.system`, `os.exec*`, `os.spawn*`); and loading new
  native libraries (`ctypes.dlopen`, `ctypes.dlsym`).
- On Windows every guest is additionally in a Job Object with
  `ActiveProcessLimit = 1`, so the kernel rejects child processes even if the
  hook is bypassed.
- `memory_mb` caps the guest (`RLIMIT_AS` on POSIX, Job Object on Windows).
- Every enable/disable and every denied call is logged on
  `whombat.plugins.audit`.

Not enforced: a true OS jail. Native code that is already loaded, or OS
primitives that bypass Python, can still escape the hook. The OS-level sandbox
(Windows AppContainer/restricted token, Linux namespaces/seccomp) is scoped as a
follow-up in [plugins-os-sandbox.md](plugins-os-sandbox.md).

## Worked example: `whombat_tflm_export`

The TFLM export plugin is a real integration that predates the new platform. It
is a useful template because it has both a backend endpoint and a data
transformation.

### How it works today (legacy)

```
back/src/whombat_tflm_export/
  __init__.py      # __description__, __version__, exports router
  router.py        # POST /export/, POST /export/report/
  export.py        # build_export(), load_profile(), ExportResult
  categories.yaml
```

`__init__.py`:

```python
from whombat_tflm_export.export import ExportResult, build_export, load_profile
from whombat_tflm_export.router import router

__description__ = "Export annotation projects as audio-recognizer-vandalism datasets."
__version__ = "0.1.0"
```

`router.py` declares ordinary FastAPI endpoints and receives Whombat internals
directly:

```python
from whombat.routes.dependencies import Session, WhombatSettings

router = APIRouter()

@router.post("/export/")
async def export_project(
    request: ExportRequest, session: Session, settings: WhombatSettings
):
    result = await build_export(
        session, settings.audio_dir, request.project_uuid, load_profile()
    )
    return StreamingResponse(BytesIO(result.zip_bytes), media_type="application/zip")
```

Because the module name starts with `whombat_` and it exposes `__description__`,
Whombat imports it at startup and mounts it at
`/plugins/routes/tflm_export/export/`. It appears in the plugin list as
`legacy: true`, `trust: "trusted"`, `status: "ready"`.

That is the whole integration: no registration file, no build step. Put a
`whombat_<name>` package on the Python path and restart.

### Same plugin as a manifest plugin

```
~/.whombat/plugins/tflm-export/
  plugin.toml
  tflm_export/__init__.py
  dist/index.html
```

```toml
id = "tflm-export"
name = "TFLM Export"
version = "0.1.0"
api_version = "1.0"
[backend]
entry = "tflm_export:handle"
[ui]
entry = "dist/index.html"
[capabilities]
host = ["projects.read"]
```

```python
# tflm_export/__init__.py
def handle(method, params):
    if method == "plugin.describe":
        return {"id": "tflm-export", "version": "0.1.0"}
    if method == "plugin.health":
        return {"status": "ok"}
    if method == "plugin.invoke":
        operation = params["operation"]
        if operation == "export":
            return {"zip": build_export_bytes(params["args"])}
        raise ValueError(f"unsupported operation {operation}")
```

The blocker is data access. The current export reads project, recording and
annotation tables through a `Session`; a manifest plugin cannot. Two honest
options:

1. **Keep it legacy.** It works, it is marked `legacy`, and it is unaffected by
   the new code. This is the current state of the branch.
2. **Add a broker and port it.** Define a narrow capability such as
   `projects.read` / `export_payload` that returns the rows the exporter needs,
   then move `build_export` into the guest and return the zip through `invoke`.
   That is a design change to the broker surface, not just a file move, and
   should be reviewed like one.

A UI is optional for this plugin: the export can be triggered from the plugin
page through `invoke`, or from a small `dist/index.html` that calls the host
bridge.

### Checklist for a new plugin

1. Pick an `id` and create `~/.whombat/plugins/<id>/plugin.toml`.
2. Write `handle()` and expose `plugin.describe` / `plugin.health`.
3. Declare only the capabilities you actually call.
4. Add `dist/index.html` if you need a UI; otherwise omit `[ui]`.
5. Start Whombat, open **Plugins**, enable it, approve the capabilities.
6. Check `status: ready` and `GET /api/v1/plugins/<id>/health/`.
7. Disable it and confirm the process is gone.

## Migration from a legacy module

1. Move the code into its own directory with a `plugin.toml`.
2. Replace router endpoints with `invoke` operations, or with broker calls if
   you need Whombat data.
3. Move `statics/` into `dist/` and declare `[ui].entry`.
4. Remove the module from Whombat's environment once the manifest version is
   installed.
5. Keep both for one release if you need a transition; the legacy loader stays
   for a deprecation window.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `incompatible` | `api_version` not supported | set `api_version = "1.0"` |
| `failed` at discovery | bad manifest, duplicate `id`, unsupported key | fix `last_error`, restart |
| `failed` on enable | entry module missing, guest handshake failed | check `entry`, run the module manually |
| `crashed` | guest exited or was killed | check `last_error`; enable/restart |
| `unhealthy` | `plugin.health` failed or timed out | inspect the guest |
| Broker returns `-32001` | capability not declared or not granted | add it to `host` and re-enable |
| UI 404 | `[ui].entry` wrong or file missing | verify the path inside the plugin dir |
| UI blank | frame never sends `plugin.ready` | check the bridge in the console |
