"""`kraft admin mcp` — the MCP front door onto `client.py`.

A dispatch table and nothing more. Every tool here is a docstring plus one call
into `client`, because `kraft <verb>` is the same functions behind a different
door and the two must not drift.

Docstrings are the tool descriptions an agent reads to decide whether to call
something, so they are written for that reader, not for a maintainer.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from kraft import client


def build() -> MCPServer:
    """The server, tools registered. Split from `serve_stdio` so a test can list
    the tools without owning a transport."""
    server = MCPServer("kraft")

    @server.tool()
    async def list_work_items(status: str | None = None) -> list[dict]:
        """List Kraft work items — the board. Optionally filter by status
        ("active", "paused", "completed", "failed"). Returns id, title, repo,
        status, current node, and any gate waiting on a human."""
        return await client.list_work_items(status)

    @server.tool()
    async def get_work_item(work_item_id: str | None = None) -> dict:
        """Get one Kraft work item. With no id, resolves the work item this
        session is standing in — correct when the cwd is a Kraft worktree or the
        session was started by Kraft itself."""
        return await client.get_work_item(work_item_id)

    @server.tool()
    async def get_gate_artifact(work_item_id: str | None = None) -> dict:
        """The spec or plan the work item's pending gate is a decision about."""
        return await client.artifact(work_item_id)

    @server.tool()
    async def search(q: str, limit: int = 20) -> dict:
        """Search Kraft's cross-repo index of specs, plans, and session
        summaries. Use this before writing a spec, to find whether the decision
        was already made somewhere else."""
        return await client.search(q, limit)

    @server.tool()
    async def create_work_item(
        title: str,
        repo: str | None = None,
        chain_template: str = "default",
        description: str | None = None,
    ) -> dict:
        """File a new Kraft work item. It is created **paused** and does not run:
        a human starts it from the board. Use this to hand finished work off to
        Kraft rather than doing it in this session. `repo` defaults to the repo
        of the work item this session is standing in.

        `description` is the brief — what the work actually is, in prose. The
        title is only a label; the spec node writes its design from the
        description, so put the intent there rather than packing it into the
        title."""
        return await client.create_work_item(title, repo, chain_template, description)

    @server.tool()
    async def ensure_repo(path: str | None = None) -> dict:
        """Connect a repository to Kraft if it is not already connected, so work
        items can be created against it. Idempotent — safe to call every time.
        `path` defaults to the current working directory."""
        return await client.ensure_repo(path)

    @server.tool()
    async def approve_gate(gate: str | None = None, work_item_id: str | None = None) -> dict:
        """Approve the human gate a Kraft work item is waiting on, letting the
        chain continue. With no gate name, approves whichever gate is pending.
        Gates are spec_approval, plan_approval, chain_finalized, and
        human_review_approval. Only a human should decide this — ask first."""
        return await client.approve_gate(gate, work_item_id)

    @server.tool()
    async def reject_gate(
        note: str, gate: str | None = None, work_item_id: str | None = None
    ) -> dict:
        """Reject the human gate a Kraft work item is waiting on, sending it back
        to be re-planned. `note` says what is wrong and is required. Only a human
        should decide this — ask first."""
        return await client.reject_gate(note, gate, work_item_id)

    @server.tool()
    async def pause_work_item(work_item_id: str | None = None) -> dict:
        """Stop a running Kraft work item's current attempt. Pair with
        resume_work_item to redirect work that is going wrong: there is no way to
        talk to a running agent, so steering means pausing and resuming."""
        return await client.pause(work_item_id)

    @server.tool()
    async def resume_work_item(steer: str | None = None, work_item_id: str | None = None) -> dict:
        """Start or restart a paused Kraft work item. `steer` is carried into the
        next attempt's prompt. This is also how a work item created by
        create_work_item is started for the first time."""
        return await client.resume(steer, work_item_id)

    @server.tool()
    async def retry_work_item(steer: str | None = None, work_item_id: str | None = None) -> dict:
        """Re-run the node a stopped Kraft work item stopped on, with `steer`
        carried into the retry's prompt. This is the only way back onto an item
        that stopped for a human: resume only takes a paused item."""
        return await client.retry(steer, work_item_id)

    return server


def serve_stdio() -> None:
    build().run(transport="stdio")
