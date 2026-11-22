"""Host-side supervisor for a plugin guest process.

The guest is a separate Python process speaking newline-delimited
JSON-RPC 2.0 over stdin/stdout. It never imports Whombat code, never
inherits the host environment, and is killed or quarantined when it
misbehaves.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import deque
from typing import Any, Awaitable, Callable

from whombat.plugins.discovery import PluginSource

__all__ = ["GuestError", "GuestProcess"]

MAX_RESPONSE_BYTES = 1024 * 1024
_STDERR_LINES = 8
_ENV_KEYS = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "COMSPEC",
    "PATHEXT",
    "LANG",
)

ERROR_UNKNOWN_METHOD = -32601
ERROR_INVALID_PARAMS = -32003
ERROR_UNAVAILABLE = -32004


class GuestError(RuntimeError):
    """A failed host↔guest exchange, carrying a JSON-RPC error code."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class GuestProcess:
    """Own the lifetime and RPC channel of one plugin process."""

    def __init__(
        self,
        source: PluginSource,
        broker: Callable[[dict], Awaitable[Any]] | None = None,
    ) -> None:
        if source.manifest is None or source.manifest.backend is None:
            raise GuestError(ERROR_UNAVAILABLE, "Plugin has no backend")
        self.source = source
        self.manifest = source.manifest
        self.backend = source.manifest.backend
        self._broker = broker
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._stderr_reader: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._next_id = 0
        self._write_lock = asyncio.Lock()
        self._stderr: deque[str] = deque(maxlen=_STDERR_LINES)
        self._last_error: str | None = None
        self.started_at: float | None = None

    @property
    def alive(self) -> bool:
        return (
            self._process is not None
            and self._process.returncode is None
            and self._process.stdin is not None
        )

    @property
    def last_error(self) -> str | None:
        if self._last_error:
            return self._last_error
        if self._stderr:
            return self._stderr[-1]
        return None

    async def start(self) -> None:
        """Spawn the guest and complete the describe handshake."""
        if self.alive:
            return

        command = [
            sys.executable,
            "-m",
            "whombat.plugins._runner",
            "--entry",
            self.backend.entry,
        ]
        root = self.source.root
        if root is not None:
            command += ["--root", str(root)]

        environment = {
            key: os.environ[key]
            for key in _ENV_KEYS
            if key in os.environ
        }
        if root is not None:
            parts = [str(root), os.environ.get("PYTHONPATH", "")]
            environment["PYTHONPATH"] = os.pathsep.join(
                part for part in parts if part
            )

        self._last_error = None
        self._process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(root) if root is not None else None,
            env=environment,
        )
        self._reader = asyncio.create_task(self._read_stdout())
        self._stderr_reader = asyncio.create_task(self._read_stderr())

        try:
            await self.call(
                "plugin.describe",
                {},
                timeout_ms=self.backend.startup_timeout_ms,
            )
        except GuestError:
            await self.stop()
            raise
        self.started_at = asyncio.get_running_loop().time()

    async def call(
        self,
        method: str,
        params: dict | None = None,
        timeout_ms: int | None = None,
    ) -> Any:
        """Send one JSON-RPC request and wait for its result."""
        if not self.alive:
            raise GuestError(
                ERROR_UNAVAILABLE, "Plugin process is unavailable"
            )

        self._next_id += 1
        request_id = str(self._next_id)
        future: asyncio.Future = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request_id] = future

        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        timeout = (timeout_ms or self.backend.call_timeout_ms) / 1000
        try:
            await self._write(message)
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as error:
            self._last_error = f"{method} timed out"
            raise GuestError(-32002, "Plugin call timed out") from error
        finally:
            self._pending.pop(request_id, None)

    async def stop(self) -> None:
        """Ask the guest to shut down, then terminate it."""
        process = self._process
        if process is None:
            return

        if process.returncode is None:
            try:
                await self.call("plugin.shutdown", {}, timeout_ms=1500)
            except GuestError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 2)
            except asyncio.TimeoutError:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 2)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()

        for task in (self._reader, self._stderr_reader):
            if task is not None and not task.done():
                task.cancel()
        self._fail_pending("Plugin process stopped")
        self._process = None

    async def _write(self, message: dict) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise GuestError(
                ERROR_UNAVAILABLE, "Plugin process is unavailable"
            )
        payload = json.dumps(message) + "\n"
        async with self._write_lock:
            try:
                process.stdin.write(payload.encode("utf-8"))
                await process.stdin.drain()
            except (OSError, RuntimeError) as error:
                self._last_error = "Plugin process is unavailable"
                raise GuestError(
                    ERROR_UNAVAILABLE, "Plugin process is unavailable"
                ) from error

    async def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            while True:
                try:
                    line = await process.stdout.readline()
                except (ConnectionResetError, OSError):
                    break
                if not line:
                    break
                if len(line) > MAX_RESPONSE_BYTES:
                    self._last_error = "Plugin response exceeded size limit"
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self._last_error = "Plugin wrote a malformed message"
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("method") == "host.call":
                    asyncio.create_task(self._answer_broker(message))
                    continue
                self._resolve(message)
        finally:
            self._fail_pending("Plugin process is unavailable")
            if self._process is process:
                self._last_error = self._last_error or "Plugin process exited"

    async def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            self._stderr.append(
                line.decode("utf-8", "replace").strip()[:500]
            )

    def _resolve(self, message: dict) -> None:
        request_id = message.get("id")
        future = self._pending.get(str(request_id))
        if future is None or future.done():
            return
        error = message.get("error")
        if error:
            future.set_exception(
                GuestError(
                    int(error.get("code", ERROR_UNAVAILABLE)),
                    str(error.get("message", "Plugin error")),
                )
            )
            return
        future.set_result(message.get("result"))

    async def _answer_broker(self, message: dict) -> None:
        request_id = message.get("id")
        try:
            if self._broker is None:
                raise GuestError(
                    ERROR_UNKNOWN_METHOD, "No host operations available"
                )
            params = message.get("params") or {}
            if not isinstance(params, dict):
                raise GuestError(ERROR_INVALID_PARAMS, "Invalid params")
            result = await self._broker(params)
            reply = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }
        except GuestError as error:
            reply = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": error.code, "message": error.message},
            }
        except Exception:
            reply = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": ERROR_UNAVAILABLE,
                    "message": "Host operation failed",
                },
            }
        try:
            await self._write(reply)
        except GuestError:
            pass

    def _fail_pending(self, message: str) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(
                    GuestError(ERROR_UNAVAILABLE, message)
                )
        self._pending.clear()
