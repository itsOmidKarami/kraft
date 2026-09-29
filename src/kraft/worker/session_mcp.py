"""The MCP server a sandboxed claude reaches at `http://kraft/mcp` (sandbox
part 2, P5; spec §5): its `--permission-prompt-tool`, and nothing else.

Not `kraft.mcp.build()`, which is a person's whole surface acting as
whoever runs it. This one has one tool, and it acts as the session the
channel says the request came in on: the `X-Kraft-Session-Id` the egress
proxy set in place of whatever the worker sent, never an environment
variable. It is served only through that proxy, never on the daemon's port.

Stateless, with JSON replies: the proxy answers one request per connection
and streams nothing back.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

#: `(session id, the permission ask's JSON)` -> `(status, reply body)`: the
#: daemon's own `POST /api/worker-sessions/{sid}/permission`, the gate the
#: host's MCP tool asks too.
Ask = Callable[[str, dict], Awaitable[tuple[int, object]]]


def build(ask: Ask) -> MCPServer:
    server = MCPServer("kraft")

    @server.tool()
    async def permission_request(
        ctx: Context, tool_name: str, input: dict, tool_use_id: str | None = None
    ) -> dict:
        """Not for you to call directly. Kraft passes this tool to the agent CLI
        as `--permission-prompt-tool`, and the CLI calls it when it wants to ask
        whether a tool use is allowed. It answers from the permission grant on
        the node this session is running, and records the decision on the work
        item."""
        # Denied on any failure, like the host's tool: an unanswered ask is
        # never a grant.
        sid = (ctx.headers or {}).get("x-kraft-session-id")
        if not sid:
            return {"behavior": "deny", "message": "not a Kraft worker session"}
        try:
            status, body = await ask(
                sid, {"tool_name": tool_name, "input": input, "tool_use_id": tool_use_id}
            )
        except Exception as exc:  # noqa: BLE001 -- the CLI waits on an answer
            return {"behavior": "deny", "message": str(exc)}
        if status >= 400 or not isinstance(body, dict) or "behavior" not in body:
            return {"behavior": "deny", "message": f"kraft {status}: {body}"}
        return body

    return server


def asgi_app(server: MCPServer) -> Starlette:
    """`server` at `/mcp`. Its `session_manager.run()` must be running
    before a request arrives: the daemon's lifespan runs it."""
    return server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=["kraft"]),
    )
