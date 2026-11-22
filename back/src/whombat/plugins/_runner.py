"""Guest-side entrypoint for an out-of-process plugin.

The host starts this module with ``--entry module:attr`` (and optionally
``--root``). The attribute must be a callable accepting ``(method,
params)`` and returning a JSON-serialisable result, optionally awaitable.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import sys
from typing import Any

ERROR_UNKNOWN_METHOD = -32601
ERROR_PLUGIN = -32004


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


async def _serve(handler) -> None:
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
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": ERROR_UNKNOWN_METHOD,
                        "message": "Unsupported request",
                    },
                }
            )
            continue

        try:
            result = await _dispatch(handler, method, params)
            _emit(
                {"jsonrpc": "2.0", "id": request_id, "result": result}
            )
        except Exception as error:
            _emit(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": ERROR_PLUGIN,
                        "message": type(error).__name__,
                    },
                }
            )

        if method == "plugin.shutdown":
            return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry", required=True)
    parser.add_argument("--root", default=None)
    arguments = parser.parse_args()

    if arguments.root:
        sys.path.insert(0, arguments.root)

    handler = _load_handler(arguments.entry)
    asyncio.run(_serve(handler))


if __name__ == "__main__":
    main()
