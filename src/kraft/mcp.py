"""`kraft admin mcp` — the MCP front door onto `client.py`.

A dispatch table and nothing more. Every tool here is a docstring plus one call
into `client`, because `kraft <verb>` is the same functions behind a different
door and the two must not drift.

Docstrings are the tool descriptions an agent reads to decide whether to call
something, so they are written for that reader, not for a maintainer.
"""

from __future__ import annotations

import os

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
        attachments: list[dict] | None = None,
        auto_gate: bool = True,
        implements_beads: list[str] | None = None,
        policy: dict | None = None,
    ) -> dict:
        """File a new Kraft work item. It is created **paused** and does not run:
        a human starts it from the board. Use this to hand finished work off to
        Kraft rather than doing it in this session. `repo` defaults to the repo
        of the work item this session is standing in.

        `description` is the brief — what the work actually is, in prose. The
        title is only a label; the spec node writes its design from the
        description, so put the intent there rather than packing it into the
        title.

        `attachments` hands over documents that already exist:
        `[{"kind": "spec", "path": ".engineering/specs/x.md"}]`, kind `spec` or
        `plan`, at most one of each. An attached document trims the node whose
        gate it satisfies, so nobody re-approves what you already agreed, and
        the implementing agent is told to follow it rather than guess. A path
        is resolved against the repo and against the working tree you are
        standing in, so a spec you just wrote in a worktree can be attached as
        it is — relative to that tree, or absolute.

        `implements_beads` are bead ids this item implements; they are closed
        when it completes. Ids mentioned in the description are not parsed —
        naming a bead in prose promises nothing.

        `policy` is the item's own policy override, as `set_work_item_policy`
        takes it. Leave it out unless a human asked for one."""
        return await client.create_work_item(
            title,
            repo=repo,
            chain_template=chain_template,
            description=description,
            attachments=attachments,
            auto_gate=auto_gate,
            implements_beads=implements_beads,
            policy=policy,
        )

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
        note: str,
        gate: str | None = None,
        work_item_id: str | None = None,
        node: str | None = None,
    ) -> dict:
        """Reject the human gate a Kraft work item is waiting on, sending the
        chain back to the node that can address the note. `note` says what is
        wrong and is required. `node` overrides where the chain re-enters and
        must name a node at or before the gate's own; the default is the one
        the chain declares. Only a human should decide this — ask first."""
        return await client.reject_gate(note, gate, work_item_id, node)

    @server.tool()
    async def pause_work_item(work_item_id: str | None = None) -> dict:
        """Stop a running Kraft work item's current attempt. Pair with
        resume_work_item to redirect work that is going wrong: there is no way to
        talk to a running agent, so steering means pausing and resuming."""
        return await client.pause(work_item_id)

    @server.tool()
    async def report_progress(task: int, work_item_id: str | None = None) -> dict:
        """Say which task of the plan you are starting while implementing a
        Kraft work item. `task` is the N of the plan's `## Task N` heading.
        Defaults to the work item this session is running in."""
        return await client.report_progress(task, work_item_id)

    @server.tool()
    async def resume_work_item(
        steer: str | None = None,
        work_item_id: str | None = None,
        steers: dict[str, str] | None = None,
    ) -> dict:
        """Start or restart a paused Kraft work item. `steer` reaches every
        paused agent task; `steers` gives individual paused agent tasks their
        own, keyed by canonical task path (`node.step.task`). This is also how a
        work item created by create_work_item is started for the first time."""
        return await client.resume(steer, work_item_id, steers=steers)

    @server.tool()
    async def retry_work_item(
        steer: str | None = None,
        work_item_id: str | None = None,
        path: str | None = None,
        restart: bool = False,
    ) -> dict:
        """Rerun work on a stopped Kraft work item, with `steer` carried into the
        retry's prompt: the node it stopped on, or `path` (canonical: `node`,
        `node.step` or `node.step.task`) and everything after it; `restart`
        reruns the whole chain. This is the only way back onto an item that
        stopped for a human: resume only takes a paused item."""
        return await client.retry(steer, work_item_id, path=path, restart=restart)

    @server.tool()
    async def skip_work_item(
        note: str | None = None, work_item_id: str | None = None, path: str | None = None
    ) -> dict:
        """Advance a Kraft work item past its current node or pending gate,
        without running or approving it -- or, with `path` (`node.step` or
        `node.step.task` inside the current node), skip only that, stopping
        nothing beside it. Works while active, paused, or stopped for a human.
        Only a human should decide this — ask first."""
        return await client.skip(note, work_item_id, path=path)

    @server.tool()
    async def complete_work_item(
        reason: str, work_item_id: str | None = None, close_beads: bool = False
    ) -> dict:
        """Mark a Kraft work item complete by hand, stopping anything running.
        The reason is required and recorded. Its beads stay open unless
        `close_beads` is true. Only a human should decide this — ask first."""
        return await client.complete(reason, work_item_id, close_beads=close_beads)

    @server.tool()
    async def cancel_work_item(reason: str, work_item_id: str | None = None) -> dict:
        """Cancel a Kraft work item, stopping anything running; its worktree
        stays. The reason is required and recorded. Only a human should decide
        this — ask first."""
        return await client.cancel(reason, work_item_id)

    @server.tool()
    async def escalate_work_item(message: str, work_item_id: str | None = None) -> dict:
        """Send a message into a Kraft work item's escalation thread — the
        door onto a needs_human stop that wants back-and-forth with an agent
        rather than a one-shot retry. Resumes the same thread on every later
        call for the same item, so whatever was already tried carries
        forward."""
        return await client.escalate(message, work_item_id)

    @server.tool()
    async def set_mr_labels(labels: list[str], work_item_id: str | None = None) -> dict:
        """Label this Kraft work item's merge request and re-create its
        pipeline. For an `on_failure` repair task that has read a red
        `on.ci.poll` and worked out which labels the pipeline wants — this
        applies that decision, it does not make it."""
        return await client.mr_labels(labels, work_item_id)

    @server.tool()
    async def set_chain_template(template: str, work_item_id: str | None = None) -> dict:
        """Switch a not-yet-started Kraft work item onto a different chain
        template. Only works before the chain has started (no current node
        set yet) -- 404s on an unknown template name, 409s once the item is
        running."""
        return await client.set_chain_template(template, work_item_id)

    @server.tool()
    async def set_agent_overrides(
        model: str | None = None,
        escalate_model: str | None = None,
        effort: str | None = None,
        clear: bool = False,
        work_item_id: str | None = None,
    ) -> dict:
        """Set or clear a Kraft work item's own model/effort override, applied
        to every agent node in its chain without changing the chain itself.
        `clear` resets every field back to the template's own binding; naming
        a field replaces the whole stored override rather than merging with
        it."""
        return await client.set_agent_overrides(
            model, escalate_model, effort, clear=clear, work_item_id=work_item_id
        )

    @server.tool()
    async def set_node_overrides(
        node_id: str,
        auto_escalate: bool | None = None,
        auto_escalate_stuck: bool | None = None,
        auto_escalate_delay_s: int | None = None,
        clear: bool = False,
        work_item_id: str | None = None,
    ) -> dict:
        """Set or clear one node's per-item auto-escalate override on a Kraft
        work item, without touching the Policy screen's system defaults or the
        chain template everyone else uses. `clear` resets this node back to
        the template's own binding; naming a field replaces the whole stored
        override for that node rather than merging with it. 409s once the
        node has started."""
        return await client.set_node_overrides(
            node_id,
            auto_escalate,
            auto_escalate_stuck,
            auto_escalate_delay_s,
            clear=clear,
            work_item_id=work_item_id,
        )

    @server.tool()
    async def set_work_item_policy(
        policy: dict | None = None, clear: bool = False, work_item_id: str | None = None
    ) -> dict:
        """Set or clear a Kraft work item's own policy override, for that item
        only -- never its chain template or any other item. `policy` holds
        item-wide fields (`max_attempts`, `timeout_minutes`,
        `wait_timeout_minutes`, `allowed_harnesses`, and the safety fields
        `allowed_tools`, `deny_tools`, `token_budget`, `sandbox`, which can
        only tighten) and `paths`, a map from a canonical path (`node`,
        `node.step` or `node.step.task`) to the same fields for that scope:
        `{"paths": {"merge_request_feedback.ci.await_ci":
        {"wait_timeout_minutes": 180}}}`. It replaces the whole stored
        override; `clear` removes it. Refused, naming the field, past an
        administrator maximum. On a running or waiting item it binds from the
        next node entered and the next observation of a wait."""
        return await client.set_work_item_policy(policy, clear=clear, work_item_id=work_item_id)

    @server.tool()
    async def permission_request(
        tool_name: str, input: dict, tool_use_id: str | None = None
    ) -> dict:
        """Not for you to call directly. Kraft passes this tool to the agent CLI
        as `--permission-prompt-tool`, and the CLI calls it when it wants to ask
        whether a tool use is allowed. It answers from the permission grant on
        the node this session is running, and records the decision on the work
        item."""
        return await client.permission_request(tool_name, input, tool_use_id)

    return server


def serve_stdio() -> None:
    # Tag every call this process makes through `kraft.client` as MCP's
    # (Kraft-s7c04.43). `setdefault`, so an embedder that has already named
    # itself keeps its own answer.
    os.environ.setdefault("KRAFT_CLIENT", "mcp")
    build().run(transport="stdio")
