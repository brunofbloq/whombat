"""Guest-side entrypoint for an out-of-process plugin.

The host starts this module with ``--entry module:attr`` (and optionally
``--root``). The attribute must be a callable accepting ``(method,
params)`` and returning a JSON-serialisable result, optionally awaitable.

Two transports are supported:

* ``stdio`` (default): newline-delimited JSON-RPC over stdin/stdout.
* ``loopback``: HTTP JSON-RPC on an ephemeral 127.0.0.1 port, authenticated
  with a per-launch bearer token supplied by the host in
  ``WHOMBAT_PLUGIN_TOKEN``.

The event loop is created before the guest policy is installed, because
asyncio's own wake-up socket would otherwise be denied by the network hook.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import importlib
import inspect
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from whombat.plugins import guest_policy, limits

ERROR_UNKNOWN_METHOD = -32601
ERROR_PLUGIN = -32004
TOKEN_ENV = "WHOMBAT_PLUGIN_TOKEN"


def _emit(message: dict) -> None:
    sys.stdout.write(json.dumps(message, default=str) + "\n")
    sys.stdout.flush()


def _load_handler(entry: str):
    module_name, separator, attribute = entry.partition(":")
    if not separator:
        raise ValueError("entry must look like module:attr")
    module = importlib.import_module(module_name)
    handler = getattr(module, attribute)
    if not callable(handler):
        raise TypeError("entry attribute is not callable")
    return handler


async def _dispatch(handler, method: str, params: dict) -> Any:
    if method == "plugin.shutdown":
        return {"stopping": True}
    if method == "plugin.describe":
        result = handler("plugin.describe", params)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, dict) else {"entry": method}
    if method == "plugin.health":
        result = handler("plugin.health", params)
        if inspect.isawaitable(result):
            result = await result
        return result if result is not None else {"status": "ok"}
    result = handler(method, params)
    if inspect.isawaitable(result):
        result = await result
    return result


def _error_response(request_id, message: str, code: int) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


async def _serve_stdio_async(handler) -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(request, dict):
            continue

        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, dict):
            _emit(
                _error_response(
                    request_id, "Unsupported request", ERROR_UNKNOWN_METHOD
                )
            )
            continue

        try:
            result = await _dispatch(handler, method, params)
            _emit({"jsonrpc": "2.0", "id": request_id, "result": result})
        except Exception as error:
            _emit(
                _error_response(
                    request_id, type(error).__name__, ERROR_PLUGIN
                )
            )

        if method == "plugin.shutdown":
            return


def _serve_loopback(handler, token: str, loop: asyncio.AbstractEventLoop) -> None:
    def dispatch(method: str, params: dict) -> Any:
        future = asyncio.run_coroutine_threadsafe(
            _dispatch(handler, method, params), loop
        )
        return future.result(timeout=30)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:  # silence request logging
            return

        def _respond(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            expected = f"Bearer {token}"
            return hmac.compare_digest(header, expected)

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            if not self._authorized():
                self._respond(401, {"error": "unauthorized"})
                return

            length = int(self.headers.get("Content-Length") or 0)
            try:
                request = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, ValueError):
                self._respond(400, {"error": "bad request"})
                return
            if not isinstance(request, dict):
                self._respond(400, {"error": "bad request"})
                return

            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params") or {}
            if not isinstance(method, str) or not isinstance(params, dict):
                self._respond(
                    200,
                    _error_response(
                        request_id,
                        "Unsupported request",
                        ERROR_UNKNOWN_METHOD,
                    ),
                )
                return

            try:
                result = dispatch(method, params)
                self._respond(
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": result,
                    },
                )
            except Exception as error:
                self._respond(
                    200,
                    _error_response(
                        request_id, type(error).__name__, ERROR_PLUGIN
                    ),
                )

            if method == "plugin.shutdown":
                threading.Thread(
                    target=self.server.shutdown, daemon=True
                ).start()

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            self._respond(405, {"error": "use POST"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    print(json.dumps({"event": "ready", "port": port}), flush=True)
    server.serve_forever(poll_interval=0.1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry", required=True)
    parser.add_argument("--root", default=None)
    parser.add_argument(
        "--transport", choices=("stdio", "loopback"), default="stdio"
    )
    arguments = parser.parse_args()

    if arguments.root:
        sys.path.insert(0, arguments.root)

    limits.apply_posix_memory_limit()

    # The loop must exist before the policy is installed.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    guest_policy.install(arguments.root)

    handler = _load_handler(arguments.entry)

    if arguments.transport == "loopback":
        token = os.environ.get(TOKEN_ENV, "")
        if not token:
            raise SystemExit("missing plugin token")
        threading.Thread(target=loop.run_forever, daemon=True).start()
        _serve_loopback(handler, token, loop)
        return

    loop.run_until_complete(_serve_stdio_async(handler))


if __name__ == "__main__":
    main()
