"""`kraft admin mcp` — the MCP front door onto `client.py`.

A dispatch table and nothing more. Every tool here is a docstring plus one call
into `client`, because `kraft <verb>` is the same functions behind a different
door and the two must not drift.

Docstrings are the tool descriptions an agent reads to decide whether to call
something, so they are written for that reader, not for a maintainer.
"""

from __future__ import annotations

import functools
import inspect
import os
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from kraft import client
from kraft.update import installed
from kraft.vocab import WorkItemStatus


def _refusals_reach_the_agent(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """`fn`, with Kraft's refusals raised as `ToolError`.

    The MCP SDK treats any other exception as a crash: the agent reads only
    "Error executing tool <name>" and the reason stays in the server log. The
    client raises `ValueError` for every API refusal (a 404, a 409, no work
    item to act on) and `PermissionError` for the worker self-action guard,
    and an agent needs that sentence to recover. So both go out as the line
    `kraft <verb>` prints. Anything else is still a crash, logged with its
    traceback.
    """

    @functools.wraps(fn)
    async def tool(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except (ValueError, PermissionError) as exc:
            raise ToolError(client.refusal(exc)) from exc

    return tool


def _answers_as_get_work_item_does(
    fn: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """`fn`, its answer cut to what `get_work_item` hands an agent when the
    route echoed the item's whole row.

    The gate and lifecycle routes answer with the `work_items` row, which holds
    the frozen chain (`materialized_chain`): some 25 KB, doubled by the JSON
    escaping, that an agent deciding a gate never reads and pays for in
    context every time. The REST answer and `--json` keep the
    row; only this door trims it.
    """

    @functools.wraps(fn)
    async def tool(*args: Any, **kwargs: Any) -> Any:
        answer = await fn(*args, **kwargs)
        if isinstance(answer, dict) and "materialized_chain" in answer:
            return await client.get_work_item(answer["id"])
        return answer

    return tool


class _Server(MCPServer):
    """`MCPServer`, with every tool it registers wrapped by
    `_refusals_reach_the_agent`, so no tool can be added without it."""

    def tool(self, *args: Any, **kwargs: Any) -> Callable[[Callable], Callable]:
        register = super().tool(*args, **kwargs)

        def wrap(fn: Callable) -> Callable:
            # The wrapper awaits `fn`, so a sync tool would fail on every call
            # rather than here, where its author sees it.
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"MCP tool {fn.__name__} must be `async def`")
            return register(_refusals_reach_the_agent(fn))

        return wrap


def build() -> MCPServer:
    """The server, tools registered. Split from `serve_stdio` so a test can list
    the tools without owning a transport."""
    server = _Server("kraft", version=installed())

    @server.tool()
    async def list_work_items(status: WorkItemStatus | None = None) -> list[dict]:
        """List Kraft work items — the board. Optionally filter by one exact
        status: "paused", "active", "queued", "blocked", "waiting", "rate_limited",
        "needs_human", "completed" or "abandoned". Abandoned items, cancelled ones included,
        are listed only when you ask for "abandoned". Returns id, title, repo,
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
    async def get_attachment(kind: str, work_item_id: str | None = None) -> dict:
        """The spec or plan (`kind`) attached when the work item was filed, as
        Kraft stored it then: readable before the item starts, when nothing
        else holds it. 404 when the item has no such attachment."""
        return await client.attachment(kind, work_item_id)

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
        chain: str | None = None,
        description: str | None = None,
        attachments: list[dict] | None = None,
        auto_gate: bool = True,
        implements_beads: list[str] | None = None,
        depends_on: list[str] | None = None,
        policy: dict | None = None,
        base_branch: str | None = None,
        skip_nodes: list[str] | None = None,
        budget_usd: float | None = None,
        node_overrides: dict | None = None,
        chain_template: str | None = None,
    ) -> dict:
        """File a new Kraft work item. It is created **paused** and does not run:
        a human starts it from the board. Use this to hand finished work off to
        Kraft rather than doing it in this session. `repo` defaults to the repo
        of the work item this session is standing in. `chain` defaults to the
        repo's `default_chain`, else `default`.

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

        `depends_on` are work item ids this item comes after. A human still
        starts it; started while one of them is unfinished it is blocked, and
        Kraft starts it when they complete. They cannot be added later.

        `policy` is the item's own policy override, as `set_work_item_policy`
        takes it. Leave it out unless a human asked for one.

        `base_branch` is the branch the work starts from and its merge request
        targets -- a release branch, say. Unset, it is the repo's default
        branch. It must already exist on the repo's origin.

        `skip_nodes` drops named nodes from the item's chain; `budget_usd` caps
        its spend in dollars (unset, the policy's cap applies -- this door can
        set a cap but not lift one); `node_overrides` is `{node_id: {field:
        value}}`, the fields `set_node_overrides` takes. Leave all three out
        unless a human asked for them. `chain_template` is `chain`'s 1.x
        name, still read; pass `chain`."""
        return await client.create_work_item(
            title,
            repo=repo,
            chain=chain or chain_template,
            description=description,
            attachments=attachments,
            auto_gate=auto_gate,
            implements_beads=implements_beads,
            depends_on=depends_on,
            policy=policy,
            base_branch=base_branch,
            skip_nodes=skip_nodes,
            node_overrides=node_overrides,
            **({"budget_usd": budget_usd} if budget_usd is not None else {}),
        )

    @server.tool()
    async def ensure_repo(
        path: str | None = None,
        test_command: str | None = None,
        setup_command: str | None = None,
    ) -> dict:
        """Connect a repository to Kraft if it is not already connected, so work
        items can be created against it. Idempotent — safe to call every time.
        `path` defaults to the current working directory. Kraft proposes a test
        and a setup command from the repo's own files (task runners, CI,
        lockfiles) and returns every candidate it saw; pass `test_command` or
        `setup_command` to use the repo's own documented commands instead
        (`""` declares none). Show the person what it proposes before relying
        on it. `test_command=""` (no tests) saves the repo disabled: work items
        on it would pass verification without running a test, which is the
        person's to decide, by enabling it. A repository with no commit yet is
        refused: a work item's branch starts from one. An already-connected
        repo is left as it is."""
        return await client.ensure_repo(
            path,
            test_command=test_command,
            setup_command=setup_command,
            # Never enabled with no tests on an agent's say-so.
            enabled=False if test_command == "" else None,
        )

    @server.tool()
    @_answers_as_get_work_item_does
    async def approve_gate(
        gate: str | None = None, work_item_id: str | None = None, digest: str | None = None
    ) -> dict:
        """Approve the human gate a Kraft work item is waiting on, letting the
        chain continue. With no gate name, approves whichever gate is pending;
        the shipped chains' gates are spec_approval, plan_approval and
        chain_revision_approval. A chain revision gate also needs the `digest`
        get_gate_artifact returned with the revision the human reviewed. Only a
        human should decide this — ask first."""
        return await client.approve_gate(gate, work_item_id, digest=digest)

    @server.tool()
    @_answers_as_get_work_item_does
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
        talk to a running agent, so steering means pausing and resuming.
        Pausing a queued item takes it out of the queue, back to where it was."""
        return await client.pause(work_item_id)

    @server.tool()
    async def unblock_work_item(
        work_item_id: str | None = None, dependency: str | None = None
    ) -> dict:
        """Drop what a blocked or paused Kraft work item still comes after, or
        only `dependency`. A blocked item with nothing left to wait for then
        starts on its own. Only a human should decide this — ask first."""
        return await client.unblock(work_item_id, dependency)

    @server.tool()
    async def report_progress(task: int, work_item_id: str | None = None) -> dict:
        """Say which task of the plan you are starting while implementing a
        Kraft work item. `task` is the N of the plan's `## Task N` heading.
        Defaults to the work item this session is running in."""
        return await client.report_progress(task, work_item_id)

    @server.tool()
    async def reply_to_thread(thread_id: str, body: str, claim: str | None = None) -> dict:
        """Answer a review thread while working a Kraft work item. `claim` is
        `fixed` (you changed the code), `answered` (a reply, no change) or
        `should_fix` (you agree it needs a change you were not asked to make)."""
        return await client.reply_to_thread(thread_id, body, claim)

    @server.tool()
    @_answers_as_get_work_item_does
    async def resume_work_item(
        steer: str | None = None,
        work_item_id: str | None = None,
        steers: dict[str, str] | None = None,
    ) -> dict:
        """Start or restart a paused Kraft work item. `steer` reaches every
        paused agent task; `steers` gives individual paused agent tasks their
        own, keyed by canonical task path (`node.step.task`). This is also how a
        work item created by create_work_item is started for the first time. With
        every slot busy the item is queued (status "queued") and starts on its
        own when one frees. An item that comes after an unfinished one is blocked
        (status "blocked") and starts when they complete."""
        return await client.resume(steer, work_item_id, steers=steers)

    @server.tool()
    @_answers_as_get_work_item_does
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
        stopped for a human: resume only takes a paused item. With every slot busy
        the item is queued (status "queued") and starts on its own when one
        frees. An item that comes after an unfinished one is blocked (status
        "blocked") and starts when they complete."""
        return await client.retry(steer, work_item_id, path=path, restart=restart)

    @server.tool()
    @_answers_as_get_work_item_does
    async def raise_budget(budget_usd: float | None, work_item_id: str | None = None) -> dict:
        """Raise the dollar cap that stopped a Kraft work item and retry it,
        the board's Raise budget button. `budget_usd` is the new cap in
        dollars, or null for no cap. Takes a stop on the item's own cap or on
        the item-wide `budget_usd` of its policy (merged into the item's
        policy override, its other fields kept). A stop on a node's
        `budget_usd`, a `token_budget` or `budget.daily_usd` is refused.
        With every slot busy the cap is raised and the retry is queued.
        Only a human should decide this — ask first."""
        return await client.raise_budget(budget_usd, work_item_id)

    @server.tool()
    @_answers_as_get_work_item_does
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
    @_answers_as_get_work_item_does
    async def complete_work_item(
        reason: str, work_item_id: str | None = None, close_beads: bool = False
    ) -> dict:
        """Mark a Kraft work item complete by hand, stopping anything running.
        The reason is required and recorded. Its beads stay open unless
        `close_beads` is true. Only a human should decide this — ask first."""
        return await client.complete(reason, work_item_id, close_beads=close_beads)

    @server.tool()
    @_answers_as_get_work_item_does
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
    async def set_chain(chain: str, work_item_id: str | None = None) -> dict:
        """Switch a not-yet-started Kraft work item onto a different chain.
        Only works before the chain has started (no current node set yet) --
        404s on an unknown chain id, 409s once the item is running."""
        return await client.set_chain(chain, work_item_id)

    @server.tool()
    async def set_attachments(
        spec: str | None = None,
        plan: str | None = None,
        drop: list[str] | None = None,
        work_item_id: str | None = None,
    ) -> dict:
        """Revise a not-yet-started Kraft work item's attached spec or plan
        instead of filing it again. `spec`/`plan` is a path, re-copied into
        Kraft's storage; a kind in `drop` ("spec" or "plan") is removed, which
        puts back the gate it had trimmed. A kind not named keeps its copy.
        409s once the item has started."""
        return await client.set_attachments(spec, plan, drop, work_item_id)

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
        model: str | None = None,
        effort: str | None = None,
        extra_prompt: str | None = None,
        clear: bool = False,
        work_item_id: str | None = None,
    ) -> dict:
        """Set or clear one node's per-item override on a Kraft work item --
        its auto-escalate settings, the `model`/`effort` its agent tasks launch
        with (above the item-wide `set_agent_overrides`), and an `extra_prompt`
        appended to each of their instructions -- without touching the Policy
        screen's system defaults or the chain everyone else uses. A
        model/effort the node's harness refuses is refused here. `clear` resets this node back to
        the template's own binding; naming a field sets it and keeps the
        node's other fields. 409s once the node has started."""
        return await client.set_node_overrides(
            node_id,
            auto_escalate,
            auto_escalate_stuck,
            auto_escalate_delay_s,
            model=model,
            effort=effort,
            extra_prompt=extra_prompt,
            clear=clear,
            work_item_id=work_item_id,
        )

    @server.tool()
    async def set_work_item_policy(
        policy: dict | None = None, clear: bool = False, work_item_id: str | None = None
    ) -> dict:
        """Set or clear a Kraft work item's own policy override, for that item
        only -- never its chain or any other item. `policy` holds
        item-wide fields (`max_attempts`, `timeout_minutes`,
        `allowed_harnesses`, `escalation_harness` -- the harnesses.yaml
        profile an escalation turn runs on, or "item" for the one the item's
        own work ran on -- the caps `time_cap_minutes`,
        `total_time_cap_minutes` (a wait's total cap is its timeout),
        `token_budget` and `budget_usd`, which item-wide may exceed the
        chain's up to the work-item maximum and on a path only tighten --
        item-wide `budget_usd` may be "none", no dollar cap, the way past a
        stop on unknown spend -- and
        the fields that only tighten: `allowed_tools`, `deny_tools`,
        `sandbox`) and `paths`, a map from a canonical path
        (`node`, `node.step` or `node.step.task`) to the same fields for that
        scope: `{"paths": {"merge_request_feedback.ci.await_ci":
        {"total_time_cap_minutes": 60}}}`. It replaces the whole stored
        override; `clear` removes it. Refused, naming the field, past an
        administrator maximum. The item's own dollar cap, which stands in for
        `budget.work_item_usd`, is `raise_budget`'s, not this; to raise the
        item-wide `budget_usd` that stopped an item, `raise_budget` keeps the
        override's other fields, where this drops any you leave out. On a running or
        waiting item it binds from the next node entered and the next
        observation of a wait."""
        return await client.set_work_item_policy(policy, clear=clear, work_item_id=work_item_id)

    @server.tool()
    async def list_threads(work_item_id: str | None = None, open_only: bool = False) -> list[dict]:
        """Review threads on a Kraft work item, drafts included, oldest first.
        `open_only` drops resolved threads."""
        return await client.threads(work_item_id, open_only)

    @server.tool()
    async def compare_changes(
        work_item_id: str | None = None,
        from_: str = "base",
        to: str = "latest",
        nodes: str | None = None,
        ignore_whitespace: bool = False,
    ) -> dict:
        """Diff two review targets of a Kraft work item: base, attempt:N (needs a
        pending gate), last_review, or latest (the working tree). `nodes` limits
        the diff to files those node ids touched, comma-separated.
        `ignore_whitespace` leaves out whitespace-only changes."""
        return await client.compare(work_item_id, from_, to, nodes, ignore_whitespace)

    @server.tool()
    async def add_review_comment(
        body: str,
        work_item_id: str | None = None,
        thread_id: str | None = None,
        file_path: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        side: str | None = None,
        label: str | None = None,
        suggestion: str | None = None,
        start_side: str | None = None,
        quote: str | None = None,
    ) -> dict:
        """Leave a draft review comment on a Kraft work item: a new thread, or a
        reply when `thread_id` is given. Drafts reach no agent until
        submit_review sends them. `label` is must_fix, question or nit; a line
        range needs `file_path`, `start_line`, `end_line` and `side` (old or
        new, default new), which is `end_line`'s side. `start_side` is
        `start_line`'s when it differs: old line 3 through new line 2 is
        `start_side` old, `side` new. `quote` is the range's lines, each led by
        its diff mark; left out, Kraft quotes them from the diff. `suggestion`
        replaces new-side lines, so it needs a range on the new side alone."""
        return await client.add_review_comment(
            body,
            work_item_id,
            thread_id,
            file_path,
            start_line,
            end_line,
            side,
            label,
            suggestion,
            start_side=start_side,
            quote=quote,
        )

    @server.tool()
    async def resolve_thread(thread_id: str) -> dict:
        """Mark a Kraft review thread resolved."""
        return await client.resolve_thread(thread_id)

    @server.tool()
    async def reopen_thread(thread_id: str) -> dict:
        """Reopen a resolved Kraft review thread."""
        return await client.reopen_thread(thread_id)

    @server.tool()
    async def submit_review(
        outcome: str,
        work_item_id: str | None = None,
        summary: str | None = None,
        node: str | None = None,
    ) -> dict:
        """Send your drafted review comments on a Kraft work item, with an
        outcome: "comment" (queues them for the next agent; never interrupts),
        "request_changes" (redoes work now, at a pending gate or not), or
        "approve" (needs a pending gate). `node` overrides where
        request_changes re-runs; left out, it is derived from the threads.
        Only a human should decide this — ask first."""
        return await client.submit_review(outcome, work_item_id, summary, node)

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
