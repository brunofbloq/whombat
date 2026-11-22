"""In-guest policy for file and socket access.

The plugin process installs a Python audit hook before importing plugin
code. It denies ``open`` calls outside the plugin root, the interpreter
installation and the manifest's ``fs_read`` roots, and denies
``socket.connect`` calls to hosts not declared in the manifest's ``net``
list.

This is a runtime policy, not an OS jail: native code or ``ctypes`` can
bypass it. It exists so a plain Python plugin cannot quietly read
``~/.ssh`` or call an undeclared API.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

__all__ = ["FS_ENV", "NET_ENV", "install"]

FS_ENV = "WHOMBAT_PLUGIN_FS_ROOTS"
NET_ENV = "WHOMBAT_PLUGIN_NET_HOSTS"


def _load_list(name: str) -> list[str]:
    raw = os.environ.get(name)
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [item for item in value if isinstance(item, str)]


def _is_within(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def install(root: str | None = None) -> None:
    """Install the audit hook for this process."""
    allowed_roots = [Path(item).expanduser() for item in _load_list(FS_ENV)]
    if root:
        allowed_roots.append(Path(root))
    for base in (
        sys.prefix,
        sys.base_prefix,
        Path(sys.executable).parent,
        Path(__file__).parent,
    ):
        allowed_roots.append(Path(base))

    roots = []
    for candidate in allowed_roots:
        try:
            roots.append(candidate.resolve())
        except OSError:
            continue

    allowed_hosts = {host.lower() for host in _load_list(NET_ENV)}

    def hook(event: str, args: tuple) -> None:
        if event == "open":
            target = _as_path(args[0])
            if target is None:
                return
            try:
                resolved = target.resolve()
            except OSError:
                return
            if not _is_within(resolved, roots):
                raise PermissionError(
                    "Plugin is not allowed to open that path"
                )
            return

        if event == "socket.connect":
            address = args[1] if len(args) > 1 else None
            host = _host_of(address)
            if host is None:
                return
            if host not in allowed_hosts:
                raise PermissionError(
                    "Plugin is not allowed to reach that host"
                )
            return

        if event in (
            "subprocess.Popen",
            "os.system",
            "os.exec",
            "os.spawn",
            "os.posix_spawn",
        ):
            raise PermissionError(
                "Plugin is not allowed to start processes"
            )

        if event in ("ctypes.dlopen", "ctypes.dlsym"):
            raise PermissionError(
                "Plugin is not allowed to load native libraries"
            )

    sys.addaudithook(hook)


def _as_path(value) -> Path | None:
    if isinstance(value, int):
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if not isinstance(value, str):
        try:
            value = os.fspath(value)
        except TypeError:
            return None
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
    path = Path(value)
    if not path.is_absolute():
        try:
            path = Path.cwd() / path
        except OSError:
            return None
    return path


def _host_of(address) -> str | None:
    if isinstance(address, tuple) and address:
        host = address[0]
    elif isinstance(address, str):
        host = address
    else:
        return None
    if isinstance(host, bytes):
        host = host.decode("utf-8", "replace")
    return host.lower() if isinstance(host, str) else None
