"""Everything that changes a work item or the repo list: create, the gate
verbs, pause/resume/retry/skip, labels, overrides, connect/disconnect."""

from __future__ import annotations

import os
from pathlib import Path

from kraft import config
from kraft.client import context, reads, transport


async def create_work_item(
    title: str,
    repo: str | None = None,
    chain_template: str = "default",
    description: str | None = None,
    attachments: list[dict] | None = None,
    auto_gate: bool = True,
) -> dict:
    """Create a work item. It lands paused: an agent files work, a human starts it.

    `repo` defaults to the repo of the work item this session is standing in,
    which is the common case for a worker filing follow-up work.

    `attachments` are documents that already exist —
    `[{"kind": "spec"|"plan", "path": "..."}]`, at most one of each. Each trims
    the gate it satisfies, so nobody is asked to re-approve what was already
    agreed. `cwd` rides along with them so the server can resolve a path
    against the working tree this call is made from, which for a worker is a
    Kraft worktree and not the registered repo (Kraft-85wk). It is sent only
    when there is a path to resolve: a call with no attachments names no file,
    so it hands over no directory either.
    """
    if repo is None:
        work_item_id, _origin = context.resolve_context()
        if work_item_id is not None:
            repo = (await reads.get_work_item(work_item_id)).get("repo")
    if repo is None:
        # The cwd's connected repo, which is what `_repo_scope` already resolves
        # for the CLI door. Doing it here too means both doors agree, and that
        # the error below only fires when there really is no repo to find.
        repo = await context.resolve_repo()
    if not repo:
        raise ValueError(_no_repo_message())
    status, body = await transport._post(
        "/work-items",
        {
            "title": title,
            "repo": repo,
            "chain_template": chain_template,
            "autostart": False,
            "auto_gate": auto_gate,
            **({"description": description} if description else {}),
            **({"attachments": attachments, "cwd": str(Path.cwd())} if attachments else {}),
        },
    )
    if status >= 400:
        raise ValueError(f"kraft {status}: {body.get('detail', body)}")
    result = {"id": body["id"], "status": body.get("status", "paused"), "title": title}
    if body.get("bead_warning"):
        result["bead_warning"] = body["bead_warning"]
    return result


def _no_repo_message(cwd: Path | None = None) -> str:
    """Advice that matches the situation, not the API's field list.

    Standing in an ordinary git repo that is simply not connected is the common
    way to reach this, and "run from a Kraft worktree" is useless advice there —
    the fix is one `kraft repo connect` away (spec A §8).
    """
    toplevel = config.git_read(
        cwd or Path.cwd(), "rev-parse", "--show-toplevel", expected_failure=True
    )
    if toplevel:
        return (
            f"no repo: {toplevel} is a git repo but is not connected to Kraft — "
            f"connect it with `kraft repo connect {toplevel}`, or name a repo explicitly"
        )
    return (
        "no repo: name one explicitly, or run from a connected repo or a Kraft worktree "
        "so it can be resolved"
    )


async def ensure_repo(path: str | None = None) -> dict:
    """Register a repo with Kraft if it is not already connected.

    Idempotent by construction: `POST /repos` 409s on a path it already holds,
    and "already connected" is the goal state, not a failure.
    """
    path = path or os.getcwd()
    status, body = await transport._post("/repos", {"path": path})
    if status == 409:
        probe_status, probed = await transport._post("/repos/probe", {"path": path})
        if probe_status >= 400:
            raise ValueError(f"kraft {probe_status}: {probed.get('detail', probed)}")
        return {**probed, "already_connected": True}
    if status >= 400:
        raise ValueError(f"kraft {status}: {body.get('detail', body)}")
    return {**body, "already_connected": False}


async def disconnect_repo(path: str | None = None) -> dict:
    """Forget a repo. No repo file is touched and no work item is dropped.

    The literal path wins when it is itself a connected entry; only when it is
    not is the probe consulted. Probing unconditionally is right for the common
    case -- after Kraft-97e `POST /repos` stores the main checkout, so an agent
    standing in a worktree must send the main path -- but it made an entry
    *registered under a worktree path* unaddressable from every CLI door, which
    is the case this verb was added for (Kraft-7qgb). A probe that fails (not a
    git directory) falls back to the path as given, so the 404 still says what
    is wrong rather than being swallowed here.

    The extra `GET /repos` goes through `repos()`, the sanctioned reader (spec
    D §4), and only runs the probe when the literal path is not connected.
    Server-side `_connected` still matches a path against its `resolve()`, so a
    path differing only by `/var` -> `/private/var` behaves as before.
    """
    path = path or os.getcwd()
    if path not in {entry["path"] for entry in await reads.repos()}:
        status, probed = await transport._post("/repos/probe", {"path": path})
        if status < 400:
            path = probed["path"]
    await transport._delete("/repos", path=path)
    return {"path": path}


async def _pending_gate_of(work_item_id: str) -> str:
    gate = (await reads.get_work_item(work_item_id)).get("pending_gate")
    if not gate:
        raise ValueError(f"kraft: no gate is pending on {work_item_id}")
    return gate


async def approve_gate(gate: str | None = None, work_item_id: str | None = None) -> dict:
    """Approve the gate a work item is waiting on."""
    target = context._forbid_self_action(work_item_id)
    gate = gate or await _pending_gate_of(target)
    return await transport._act(f"/work-items/{target}/gates/{gate}/approve")


async def reject_gate(
    note: str,
    gate: str | None = None,
    work_item_id: str | None = None,
    node: str | None = None,
) -> dict:
    """Reject the gate a work item is waiting on. The note is required — a
    rejection with no reason strands whoever picks the work up next.

    `node` overrides where the chain re-enters; the default is the gate node's
    own `reject_to`, and failing that the gate node itself (Kraft-ko7j).
    """
    if not note or not note.strip():
        raise ValueError("a reject note is required: say what is wrong")
    target = context._forbid_self_action(work_item_id)
    gate = gate or await _pending_gate_of(target)
    payload: dict = {"note": note.strip()}
    if node:
        payload["node"] = node
    return await transport._act(f"/work-items/{target}/gates/{gate}/reject", payload)


async def pause(work_item_id: str | None = None) -> dict:
    """Stop the running node's sessions. Only an active item can be paused."""
    target = context._forbid_self_action(work_item_id)
    return await transport._act(f"/work-items/{target}/pause")


async def abandon(work_item_id: str | None = None) -> dict:
    """Terminal. Removes the worktree, destroying anything uncommitted in it."""
    target = context._forbid_self_action(work_item_id)
    return await transport._act(f"/work-items/{target}/abandon")


async def resume(steer: str | None = None, work_item_id: str | None = None) -> dict:
    """Restart a paused item, optionally carrying a steer into the next attempt.

    This is also how a created-paused item is started for the first time: a NULL
    current_node_id resolves to node zero (design §6 rule 1).
    """
    target = context._forbid_self_action(work_item_id)
    payload = {"steer": steer.strip()} if steer and steer.strip() else {}
    return await transport._act(f"/work-items/{target}/resume", payload)


async def retry(steer: str | None = None, work_item_id: str | None = None) -> dict:
    """Re-run the node an item stopped on, optionally with a steer.

    The only door back onto a `needs_human` stop: resume wants `paused`, pause
    wants `running`, and approve/reject want a pending gate. `_forbid_self_action`
    rather than `resolve_work_item`, because a retry restarts the node that is
    running you.
    """
    target = context._forbid_self_action(work_item_id)
    payload = {"steer": steer.strip()} if steer and steer.strip() else {}
    return await transport._act(f"/work-items/{target}/retry", payload)


async def skip(note: str | None = None, work_item_id: str | None = None) -> dict:
    """Advance past the current node or pending gate without running or
    approving it. The one door that bypasses a step outright, rather than
    retrying, resuming, or approving/rejecting it — works whether the item
    is running, paused, or stopped for a human.
    """
    target = context._forbid_self_action(work_item_id)
    payload = {"note": note.strip()} if note and note.strip() else {}
    return await transport._act(f"/work-items/{target}/skip", payload)


async def report_progress(task: int, work_item_id: str | None = None) -> dict:
    """Say which plan task the implementation has started.

    `resolve_work_item`, not `_forbid_self_action`: this verb exists *for* a
    worker's own item. Reporting where you are decides nothing.
    """
    target = await context.resolve_work_item(work_item_id)
    return await transport._act(f"/work-items/{target}/progress", {"task": task})


async def escalate(message: str, work_item_id: str | None = None) -> dict:
    """Send `message` into a work item's escalation thread, starting one if
    none exists yet. Only a `needs_human` item has this door — retry/resume
    cover every other stop.

    Resumes the same underlying agent session on every later call for the
    same item, so whatever it already tried carries forward, kept bounded by
    the CLI's own `--autocompact` rather than anything Kraft does.
    """
    target = context._forbid_self_action(work_item_id)
    return await transport._act(f"/work-items/{target}/escalate", {"message": message})


async def open_worktree(work_item_id: str | None = None, editor: str | None = None) -> dict:
    """Open the item's worktree in an editor on the server's machine — the same
    launch as the UI's "Open worktree", including its 501 when headless."""
    target = await context.resolve_work_item(work_item_id)
    return await transport._act(
        f"/work-items/{target}/open-worktree", {"editor": editor} if editor else {}
    )


async def mr_labels(labels: list[str], work_item_id: str | None = None) -> dict:
    """Label this item's merge request and re-create its pipeline (Kraft-xh0q).

    `resolve_work_item`, not `_forbid_self_action`: the caller this exists for
    is an `on_failure` repair agent fixing its own item's merge request, the
    same shape as `open_mr`/`ci_poll` acting on it from inside the executor —
    not a gate decision, which is the thing the self-action guard protects.
    """
    target = await context.resolve_work_item(work_item_id)
    return await transport._act(f"/work-items/{target}/mr-labels", {"labels": labels})


async def set_chain_template(template: str, work_item_id: str | None = None) -> dict:
    """Switch a not-yet-started Kraft work item onto a different chain
    template (Kraft-gwn6). 404s on an unknown template name; 409s once the
    item has a `current_node_id` -- the template is fixed for the life of a
    started item.

    `resolve_work_item`, not `_forbid_self_action`: this is not a gate
    decision, the same reasoning `mr_labels` above gives for its own door.
    """
    target = await context.resolve_work_item(work_item_id)
    return await transport._patch(f"/work-items/{target}", {"chain_template": template})


async def set_agent_overrides(
    model: str | None = None,
    escalate_model: str | None = None,
    effort: str | None = None,
    *,
    clear: bool = False,
    work_item_id: str | None = None,
) -> dict:
    """Set or clear a Kraft work item's own model/effort override
    (Kraft-4k6l), read fresh at every dispatch rather than baked into the
    chain. `clear` sends `{}`, resetting every field to the template's own
    binding; naming any of `model`/`escalate_model`/`effort` *replaces* the
    whole stored override, it does not merge with what is already there.
    `_forbid_self_action`, not `resolve_work_item` (Kraft-g1ebw): a running
    worker dialing its own model/effort mid-run is exactly the kind of
    self-action the other verbs already refuse.
    """
    target = context._forbid_self_action(work_item_id)
    if clear:
        overrides: dict = {}
    else:
        overrides = {
            k: v
            for k, v in {
                "model": model,
                "escalate_model": escalate_model,
                "effort": effort,
            }.items()
            if v is not None
        }
        if not overrides:
            raise ValueError(
                "kraft: set-overrides needs --model, --escalate-model, --effort, or --clear"
            )
    return await transport._patch(f"/work-items/{target}", {"agent_overrides": overrides})


async def set_node_overrides(
    node_id: str,
    auto_escalate: bool | None = None,
    auto_escalate_stuck: bool | None = None,
    auto_escalate_delay_s: int | None = None,
    *,
    clear: bool = False,
    work_item_id: str | None = None,
) -> dict:
    """Set or clear one node's per-item override on a Kraft work item
    (Kraft-uxm3), the door onto the same `auto_escalate`/`auto_escalate_stuck`/
    `auto_escalate_delay_s` fields the Policy screen sets system-wide and a
    chain template sets per node -- without touching either of those. `clear`
    sends `{}` for this node, dropping its overrides back to the template;
    naming a field *replaces* that node's whole stored override, it does not
    merge with what is already there. 409s once the node has started.
    `_forbid_self_action`, not `resolve_work_item` (Kraft-g1ebw): same
    self-action door every other mutating verb here already goes through.
    """
    target = context._forbid_self_action(work_item_id)
    if clear:
        fields: dict = {}
    else:
        fields = {
            k: v
            for k, v in {
                "auto_escalate": auto_escalate,
                "auto_escalate_stuck": auto_escalate_stuck,
                "auto_escalate_delay_s": auto_escalate_delay_s,
            }.items()
            if v is not None
        }
        if not fields:
            raise ValueError(
                "kraft: set-node-override needs --auto-escalate, --auto-escalate-stuck, "
                "--auto-escalate-delay-s, or --clear"
            )
    return await transport._patch(f"/work-items/{target}", {"node_overrides": {node_id: fields}})


async def reload_templates() -> dict:
    """Reread every chain template and the hook registry from disk into the
    running server, no restart."""
    return await transport._act("/templates/reload")
