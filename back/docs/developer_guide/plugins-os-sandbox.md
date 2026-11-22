# OS-level guest sandbox (follow-up)

**Status: not implemented.** This document scopes the remaining work needed to
close SEC-006 and SEC-007 at the operating-system level. The current release
enforces those constraints with a Python audit hook plus capability-checked
brokers, and contains child processes on Windows with a Job Object. That is
documented as the accepted boundary until this task lands.

## Why this is separate

The audit hook runs in the guest interpreter, so a plugin that bypasses Python
(already-loaded native code, direct OS primitives) is not stopped. Closing that
requires the OS to enforce the boundary, which means per-platform code, elevated
or specially privileged environments, and CI that can exercise them. It should
not be hidden inside an unrelated feature PR.

## Goals

- A guest cannot read or write files outside its plugin directory and the roots
  declared in `fs_read`, including via native code.
- A guest cannot reach hosts outside `net` (and cannot reach the host's own
  loopback services), including via native code.
- Existing guarantees stay: memory cap, no child processes, crash containment,
  the same manifest and broker contract.

## Non-goals

- Changing the manifest format or the RPC contract.
- Sandboxing the Whombat host itself.
- Supporting macOS in the first iteration (tracked separately).

## Windows design

Preferred: **AppContainer**.

1. Create or reuse a per-plugin AppContainer profile
   (`CreateAppContainerProfile`) with a stable SID derived from the plugin id.
2. Spawn the runner with `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`, the
   container SID, and `internetClient` only when `net` is non-empty.
3. Grant the container SID read+execute on: the plugin directory, the Python
   installation, and each declared `fs_read` root, via explicit ACLs. Everything
   else inherits "no access".
4. Keep the existing Job Object for memory and `ActiveProcessLimit`.

Fallback when AppContainer is unavailable: `CreateRestrictedToken`
(`DISABLE_MAX_PRIVILEGE`, deny-only SIDs) plus `CreateProcessAsUser`, which
requires `SeAssignPrimaryTokenPrivilege` and therefore an elevated service.

Open questions: whether `CreateAppContainerProfile` works without elevation on
supported Windows builds, and how ACLs interact with an existing user profile
that already has access.

## Linux design

1. Spawn the runner through `unshare`/`clone` with user, mount, and network
   namespaces (`-Urmn`), requiring unprivileged user namespaces
   (`kernel.unprivileged_userns_clone` / `user.max_user_namespaces`).
2. Mount namespace: bind-mount only the plugin directory, the Python
   installation, and declared `fs_read` roots; `tmpfs` for `/tmp`; read-only
   root.
3. Network namespace: loopback only by default. For declared `net` hosts, either
   allow no route and require the `net.get` broker, or add a proxy with an
   allowlist.
4. Apply `RLIMIT_AS`, `RLIMIT_NPROC` and `no_new_privs`; optionally a seccomp
   profile denying `socket`, `connect`, `open` outside the bind mounts.

## macOS (later)

A `sandbox_init`/seatbelt profile restricting file reads to the plugin root and
declared roots, and denying outbound network unless declared.

## Failure policy

- If the platform sandbox cannot be applied and the plugin declares `fs_read` or
  `net`, the plugin must fail to start with a clear status, not silently fall
  back to the audit hook.
- Platforms without a supported mechanism report the plugin as `incompatible`
  for those capabilities.

## Testing and CI

- **Windows**: a GitHub Actions `windows-latest` job that creates the
  AppContainer, launches a fixture guest, and asserts a native `open` outside
  the roots and a raw socket connect both fail.
- **Linux**: an `ubuntu-latest` job using unprivileged user namespaces; skip with
  an explicit message when the runner disables them.
- **Unit**: ACL/namespace argument construction tested without spawning where
  possible.
- **Regression**: the existing `tests/test_plugins*.py` suite must pass
  unchanged in both modes.

## Acceptance criteria

- A guest using `ctypes` to call `open` on a file outside its roots fails.
- A guest using a raw socket connect to an undeclared host fails.
- A guest cannot reach the Whombat API on loopback.
- Declared `fs_read` and `net` capabilities still work through both the broker
  and direct access.
- Failure to apply the sandbox is reported, never ignored.

## Rollout

1. Land detection and status reporting (`sandbox: "audit-hook" | "os"`).
2. Windows AppContainer behind a setting, default off until CI is green.
3. Linux namespaces behind the same setting.
4. Flip the default and update SEC-006/SEC-007 to reference this mechanism.
