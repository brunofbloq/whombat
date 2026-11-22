# Plugins

Plugins extend Whombat with features the core app does not ship — extra export
formats, custom views, integrations with other tools. They are optional: nothing
runs until you enable it.

Whombat supports two kinds of plugin:

- **External plugins** (recommended) are folders with a `plugin.toml` manifest.
  They are disabled by default, run in their own process, and show up in the
  Plugins page with an **Enable** button.
- **Legacy plugins** are old-style Python packages that Whombat loads at
  start-up. They still work and are marked `legacy` in the list, but they are
  being replaced by external plugins.

## Where plugins live

External plugins are read from:

```
~/.whombat/plugins/<plugin-id>/
  plugin.toml
  ...plugin files...
```

On Windows that is `C:\Users\<you>\.whombat\plugins\`. You can point Whombat
somewhere else with the `WHOMBAT_PLUGINS_DIR` environment variable.

Installing a plugin usually means copying or unzipping its folder into that
directory and restarting Whombat. Whombat does not download or install plugins
for you.

## Enabling a plugin

1. Open **User → Plugins**.
2. Find the plugin in the list, which shows its description if one is available.
   New plugins show the status **disabled**.
3. Click **Enable**.
4. Whombat lists the data the plugin asked for — for example "read datasets" or
   "read recordings". Only continue if you trust the plugin with that data.
5. The status becomes **ready**.

Plugins that are disabled cannot run code, open files, or use the network.

### What the statuses mean

| Status | Meaning |
|---|---|
| **disabled** | installed, not running |
| **starting** | being launched |
| **ready** | running |
| **unhealthy** | running but its health check failed |
| **crashed** | it stopped unexpectedly; try **Restart** |
| **incompatible** | built for a different Whombat plugin version |
| **failed** | it could not start; the card shows the reason |

If a plugin stays `failed` or keeps `crashed`, disable it and contact whoever
provided it. Whombat itself keeps working either way.

## Permissions plugins can ask for

Whombat only lets a plugin do what it declared and you approved:

| Permission | What it allows |
|---|---|
| Read datasets | list your datasets (names and identifiers) |
| Read recordings | count/read recording metadata |
| Read files | read text files inside specific folders the plugin declared |
| Network access | fetch data from specific hosts the plugin declared |

A plugin cannot ask for something new at runtime. If a plugin update needs more
access, Whombat asks you to approve it again.

## Opening and closing a plugin

- The **View** link appears only if the plugin has its own UI page. It opens the
  page inside a sandboxed frame. The plugin cannot read your Whombat session,
  cookies, or other browser data from there.
- **Test** runs the health check. It is available for **ready** and `legacy`
  plugins and disabled for **disabled** plugins.
- **Disable** stops it immediately and revokes its permissions.
- **Restart** stops and starts it again, keeping the permissions you approved.
- Removing the plugin is done by deleting its folder from the plugins directory
  and restarting Whombat. Use **Disable** first so nothing is running.

An enabled plugin that sits unused is stopped automatically after a few minutes
and returns to **disabled**; enable it again when you need it.

## Legacy plugins

Old plugins installed as Python packages appear with a `legacy` badge and the
status **ready**. They run inside Whombat itself, so they have the same access
Whombat does. Keep them only if you trust their source, and prefer an external
version when the author provides one.

## Safety notes

- Enable only plugins you trust. A plugin runs code on your computer.
- The plugin's screen is sandboxed, but the plugin's backend runs with the
  permissions you approved.
- Whombat logs plugin start-up, shutdown, and denied permission requests so you
  can see what happened.
