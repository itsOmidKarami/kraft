"""A job's way to Kraft: a minimal HTTP/1.1 server on the machine's loopback, so the `kraft` CLI
and anything else that dials `KRAFT_HOST:KRAFT_PORT` reaches the server over the machine's one
connection."""

from __future__ import annotations

import asyncio
import http
import json
from collections.abc import Awaitable, Callable

Handler = Callable[[str, str, dict, bytes], Awaitable[tuple[int, dict, bytes]]]
_MCP = {"detail": "the session MCP endpoint is not forwarded to machines yet"}


class Forwarder:
    def __init__(self, handler: Handler) -> None:
        self._handler, self._server = handler, None

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return self._server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _serve(self, reader, writer) -> None:
        try:
            method, target, _ = (await reader.readline()).decode("latin-1").split(" ", 2)
            headers: dict[str, str] = {}
            while (line := await reader.readline()) not in (b"\r\n", b"\n", b""):
                name, _, value = line.decode("latin-1").partition(":")
                headers[name.strip().lower()] = value.strip()
            length = int(headers.get("content-length") or 0)
            body = await reader.readexactly(length) if length else b""
            if target.split("?", 1)[0] == "/mcp":
                status, out_headers, out = (
                    501,
                    {"content-type": "application/json"},
                    json.dumps(_MCP).encode(),
                )
            else:
                try:
                    status, out_headers, out = await self._handler(method, target, headers, body)
                except Exception as exc:  # noqa: BLE001 -- the caller is a CLI waiting on a socket
                    status, out_headers = 502, {"content-type": "application/json"}
                    out = json.dumps(
                        {"detail": f"the machine could not reach Kraft: {exc}"}
                    ).encode()
            head = [
                f"HTTP/1.1 {status} {http.HTTPStatus(status).phrase}",
                f"content-length: {len(out)}",
                "connection: close",
            ]
            if out_headers.get("content-type"):
                head.append(f"content-type: {out_headers['content-type']}")
            writer.write(("\r\n".join(head) + "\r\n\r\n").encode("latin-1") + out)
            await writer.drain()
        except (ValueError, asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
