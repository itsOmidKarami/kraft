"""Everything that changes a work item or the repo list: create, the gate
verbs, pause/resume/retry/skip, labels, overrides, connect/disconnect."""

from __future__ import annotations

import os
from pathlib import Path
from types import EllipsisType

from kraft import config
from kraft.client import context, reads, transport
from kraft.vocab import DiffSide


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
    budget_usd: float | None | EllipsisType = ...,
    node_overrides: dict | None = None,
    autostart: bool = False,
) -> dict:
    """Create a work item. It lands paused: an agent files work, a human starts it.

    `autostart` is the human's shortcut past pressing Start (Kraft-s7c04.31).
    The server refuses it (403) from a Kraft session or an MCP client, so a
    worker asking for it files nothing; the default stays paused on purpose.

    `policy` is the item's own policy override (`set_work_item_policy`).

    `repo` defaults to the repo of the work item this session is standing in,
    which is the common case for a worker filing follow-up work.
    `chain` unset is not sent, so the server applies the repo's
    `default_chain` (Kraft-9efnk.11).

    `attachments` are documents that already exist —
    `[{"kind": "spec"|"plan", "path": "..."}]`, at most one of each. Each trims
    the gate it satisfies, so nobody is asked to re-approve what was already
    agreed. `cwd` rides along with them so the server can resolve a path
    against the working tree this call is made from, which for a worker is a
    Kraft worktree and not the registered repo (Kraft-85wk). It is sent only
    when there is a path to resolve: a call with no attachments names no file,
    so it hands over no directory either.

    `base_branch` is the branch the work starts from and its merge request
    targets; unset, the repo's default branch (Kraft-v9gbi).

    `skip_nodes`, `budget_usd` and `node_overrides` are POST /work-items' own
    intake fields (Kraft-s7c04.33), validated there. `budget_usd` keeps the
    API's presence rule: left at `...` it is not sent and the policy default
    applies; `None` is an explicit "no cap".
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
            "repo": context.absolute_path(repo),
            **({"chain_template": chain} if chain else {}),
            "autostart": autostart,
            "auto_gate": auto_gate,
            **({"implements_beads": implements_beads} if implements_beads else {}),
            **({"depends_on": depends_on} if depends_on else {}),
            **({"policy": policy} if policy else {}),
            **({"base_branch": base_branch} if base_branch else {}),
            **({"skip_nodes": skip_nodes} if skip_nodes else {}),
            **({"budget_usd": budget_usd} if budget_usd is not ... else {}),
            **({"node_overrides": node_overrides} if node_overrides else {}),
            **({"description": description} if description else {}),
            **({"attachments": attachments, "cwd": str(Path.cwd())} if attachments else {}),
        },
    )
    if status >= 400:
        raise ValueError(f"kraft {status}: {transport.detail_of(body)}")
    result = {"id": body["id"], "status": body.get("status", "paused"), "title": title}
    # `slots`: an --autostart filed paused because every slot was busy says so.
    for told in ("slots", "waiting_on", "repo_warning", "bead_warning", "duplicate_warning"):
        if body.get(told):
            result[told] = body[told]
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


#: How long a connect may take: the probe's two minutes, its submodules' two
#: more, and room to save. The server finishes a connect the client gave up
#: on, so a shorter wait reports a failure for a repo that was connected.
CONNECT_TIMEOUT_S = 300.0


async def probe_repo(
    path: str | None = None, *, detect: bool = True, test_command: str | None = None
) -> dict:
    """What connecting `path` would propose, changing nothing: its setup and
    test commands, and every candidate the evidence supports. `detect=False`:
    only the resolved path and the facts that need no detector table.
    `test_command`: the root's, as connecting with it would save it."""
    path = context.absolute_path(path or os.getcwd())
    payload = {"path": path, "detect": detect}
    if test_command is not None:
        payload["test_command"] = test_command
    status, body = await transport._post("/repos/probe", payload, timeout=CONNECT_TIMEOUT_S)
    if status >= 400:
        raise ValueError(f"kraft {status}: {transport.detail_of(body)}")
    return body


async def ensure_repo(
    path: str | None = None,
    *,
    test_command: str | None = None,
    setup_command: str | None = None,
    enabled: bool | None = None,
) -> dict:
    """Register a repo with Kraft if it is not already connected.

    Idempotent by construction: `POST /repos` 409s on a path it already holds,
    and "already connected" is the goal state, not a failure. `test_command`
    and `setup_command` replace what the probe would propose (`""` for none);
    an already-connected repo keeps what it has -- edit repos.yaml to change it.
    `enabled` None lets the server decide (enabled when it has a test).
    """
    path = context.absolute_path(path or os.getcwd())
    fields = {"test_command": test_command, "setup_command": setup_command, "enabled": enabled}
    payload = {"path": path, **{k: v for k, v in fields.items() if v is not None}}
    try:
        status, body = await transport._post("/repos", payload, timeout=CONNECT_TIMEOUT_S)
    except ValueError as exc:
        if "in time" not in str(exc):
            raise
        raise ValueError(
            f"connecting {path} is taking longer than {CONNECT_TIMEOUT_S}s; the server may "
            "still save it, so check `kraft repo list` before connecting again"
        ) from exc
    if status == 409:
        # The probe still runs, and still first: it is what resolves the given
        # path to the connected one, which is how `kraft repo list` marks the
        # main checkout from inside a git worktree. What changes is what is
        # returned -- the probe describes what Kraft WOULD configure, and
        # handing that back for an already-connected repo gives the caller
        # authoritative-looking `test_command`/`test_scopes`/`setup_command`
        # values the repo does not run (Kraft-djk08).
        probe_status, probed = await transport._post(
            "/repos/probe", {"path": path, "detect": False}
        )
        if probe_status >= 400:
            raise ValueError(f"kraft {probe_status}: {transport.detail_of(probed)}")
        listing = await transport._get("/repos")
        stored = next(
            (r for r in listing.get("repos", []) if r.get("path") == probed.get("path")),
            None,
        )
        # No match cannot happen after a 409 -- the entry is why it 409'd -- but
        # a stale read must degrade to the old behaviour, not raise.
        return {**(stored or probed), "already_connected": True}
    if status >= 400:
        raise ValueError(f"kraft {status}: {transport.detail_of(body)}")
    return {**body, "already_connected": False}


async def update_repo(path: str, fields: dict) -> dict:
    """Change a connected repo's `fields` (`PATCH /repos`); the entry as saved."""
    response = await transport._send("PATCH", "/repos", params={"path": path}, json=fields)
    body = response.json() if response.content else {}
    if response.status_code >= 400:
        raise ValueError(f"kraft {response.status_code}: {body.get('detail', body)}")
    return body


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
    path = context.absolute_path(path or os.getcwd())
    if path not in {entry["path"] for entry in await reads.repos()}:
        status, probed = await transport._post("/repos/probe", {"path": path, "detect": False})
        if status < 400:
            path = probed["path"]
    await transport._delete("/repos", path=path)
    return {"path": path}


async def _pending_gate_of(work_item_id: str) -> str:
    gate = (await reads.get_work_item(work_item_id)).get("pending_gate")
    if not gate:
        raise ValueError(f"kraft: no gate is pending on {work_item_id}")
    return gate


async def approve_gate(
    gate: str | None = None, work_item_id: str | None = None, digest: str | None = None
) -> dict:
    """Approve the gate a work item is waiting on. A chain revision's approval
    needs the `digest` its artifact carried (Kraft-ec66w)."""
    target = context._forbid_self_action(work_item_id)
    gate = gate or await _pending_gate_of(target)
    return await transport._act(
        f"/work-items/{transport.segment(target)}/gates/{transport.segment(gate)}/approve",
        {"digest": digest} if digest else None,
    )


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
    return await transport._act(
        f"/work-items/{transport.segment(target)}/gates/{transport.segment(gate)}/reject", payload
    )


async def pause(work_item_id: str | None = None) -> dict:
    """Stop the running node's sessions. Only an active item can be paused."""
    target = context._forbid_self_action(work_item_id)
    return await transport._act(f"/work-items/{transport.segment(target)}/pause")


async def unblock(work_item_id: str | None = None, dependency: str | None = None) -> dict:
    """Drop what a blocked or paused item still comes after, or the one
    dependency named, so it no longer waits for it."""
    target = context._forbid_self_action(work_item_id)
    return await transport._act(
        f"/work-items/{transport.segment(target)}/unblock",
        {"dependency": dependency} if dependency else {},
    )


async def abandon(work_item_id: str | None = None) -> dict:
    """Terminal. Removes the worktree, destroying anything uncommitted in it."""
    target = context._forbid_self_action(work_item_id)
    return await transport._act(f"/work-items/{transport.segment(target)}/abandon")


async def storage_preview(ids: list[str]) -> dict:
    """What archiving `ids` would free and lose (`POST /storage/preview`).
    Changes nothing."""
    return await transport._act("/storage/preview", {"ids": ids})


async def archive(ids: list[str]) -> dict:
    """Archive completed or abandoned items, each in its own write
    (`POST /work-items/bulk`): one `{id, ok, ...}` result per id. Removes each
    worktree, uncommitted changes included. The caller resolves `ids` through
    `_forbid_self_action` first, as `cli.item._archive_ids` does."""
    return await transport._act("/work-items/bulk", {"action": "archive", "ids": ids})


async def restore(work_item_id: str | None = None) -> dict:
    """Put an archived item back under Done. The worktree archive removed does
    not come back."""
    target = context._forbid_self_action(work_item_id)
    return await transport._act(f"/work-items/{transport.segment(target)}/restore")


async def resume(
    steer: str | None = None,
    work_item_id: str | None = None,
    steers: dict[str, str] | None = None,
) -> dict:
    """Restart a paused item. `steer` reaches every paused agent task;
    `steers` gives individual paused agent tasks their own, by canonical task
    path (`node.step.task`).

    This is also how a created-paused item is started for the first time: a NULL
    current_node_id resolves to node zero (design §6 rule 1).
    """
    target = context._forbid_self_action(work_item_id)
    payload: dict = {"steer": steer.strip()} if steer and steer.strip() else {}
    if steers:
        payload["steers"] = steers
    return await transport._act(f"/work-items/{transport.segment(target)}/resume", payload)


async def retry(
    steer: str | None = None,
    work_item_id: str | None = None,
    path: str | None = None,
    restart: bool = False,
) -> dict:
    """Rerun work on a new run fork, optionally with a steer: the node an item
    stopped on, or `path` (`node`, `node.step`, `node.step.task`) and
    everything after it; `restart` reruns the whole chain.

    The only door back onto a `needs_human` stop: resume wants `paused`, pause
    wants `running`, and approve/reject want a pending gate. `_forbid_self_action`
    rather than `resolve_work_item`, because a retry restarts the node that is
    running you.
    """
    target = context._forbid_self_action(work_item_id)
    payload: dict = {"steer": steer.strip()} if steer and steer.strip() else {}
    if path:
        payload["path"] = path
    if restart:
        payload["restart"] = True
    return await transport._act(f"/work-items/{transport.segment(target)}/retry", payload)


async def raise_budget(budget_usd: float | None, work_item_id: str | None = None) -> dict:
    """Raise the dollar cap that stopped an item and retry it, the board's
    Raise budget button. `None` means no cap. The route takes a `needs_human`
    stop the item's own cap made, or the item-wide `budget_usd` of its
    policy, which it merges into the item's policy override."""
    target = context._forbid_self_action(work_item_id)
    return await transport._act(
        f"/work-items/{transport.segment(target)}/budget/raise", {"budget_usd": budget_usd}
    )


async def skip(
    note: str | None = None, work_item_id: str | None = None, path: str | None = None
) -> dict:
    """Advance past the current node or pending gate without running or
    approving it -- or, with `path`, skip only a `node.step` or
    `node.step.task` inside the current node, stopping nothing beside it.
    Works whether the item is running, paused, or stopped for a human.
    """
    target = context._forbid_self_action(work_item_id)
    payload: dict = {"note": note.strip()} if note and note.strip() else {}
    if path:
        payload["path"] = path
    return await transport._act(f"/work-items/{transport.segment(target)}/skip", payload)


async def complete(reason: str, work_item_id: str | None = None, close_beads: bool = False) -> dict:
    """End the item as completed by hand. A reason is required and recorded;
    its beads close only with `close_beads`."""
    target = context._forbid_self_action(work_item_id)
    payload: dict = {"reason": reason}
    if close_beads:
        payload["close_beads"] = True
    return await transport._act(f"/work-items/{transport.segment(target)}/complete", payload)


async def cancel(reason: str, work_item_id: str | None = None) -> dict:
    """Cancel the item. A reason is required and recorded; the worktree stays."""
    target = context._forbid_self_action(work_item_id)
    return await transport._act(
        f"/work-items/{transport.segment(target)}/cancel", {"reason": reason}
    )


async def report_progress(task: int, work_item_id: str | None = None) -> dict:
    """Say which plan task the implementation has started.

    `resolve_work_item`, not `_forbid_self_action`: this verb exists *for* a
    worker's own item. Reporting where you are decides nothing.
    """
    target = await context.resolve_work_item(work_item_id)
    return await transport._act(f"/work-items/{transport.segment(target)}/progress", {"task": task})


async def reply_to_thread(thread_id: str, body: str, claim: str | None = None) -> dict:
    """Answer a review thread as the worker this session is. Replies only:
    resolving is the reviewer's, and the server takes the author from the
    session, not from anything passed here."""
    payload = {"body": body, **({"claim": claim} if claim else {})}
    return await transport._act(f"/threads/{transport.segment(thread_id)}/replies", payload)


async def escalate(message: str, work_item_id: str | None = None, new_thread: bool = False) -> dict:
    """Send `message` into a work item's escalation thread, starting one if
    none exists yet. Only a `needs_human` or `paused` item has this door —
    retry/resume cover every other stop.

    Resumes the same underlying agent session on every later call for the
    same item, so whatever it already tried carries forward, kept bounded by
    the CLI's own `--autocompact` rather than anything Kraft does -- unless
    `new_thread` starts a fresh one instead (Kraft-dkb6g).
    """
    target = context._forbid_self_action(work_item_id)
    return await transport._act(
        f"/work-items/{transport.segment(target)}/escalate",
        {"message": message, "new_thread": new_thread},
    )


async def open_worktree(work_item_id: str | None = None, editor: str | None = None) -> dict:
    """Open the item's worktree in an editor on the server's machine — the same
    launch as the UI's "Open worktree", including its 501 when headless."""
    target = await context.resolve_work_item(work_item_id)
    return await transport._act(
        f"/work-items/{transport.segment(target)}/open-worktree",
        {"editor": editor} if editor else {},
    )


async def mr_labels(labels: list[str], work_item_id: str | None = None) -> dict:
    """Label this item's merge request and re-create its pipeline (Kraft-xh0q).

    `resolve_work_item`, not `_forbid_self_action`: the caller this exists for
    is an `on_failure` repair agent fixing its own item's merge request, the
    same shape as `open_mr`/`ci_poll` acting on it from inside the executor —
    not a gate decision, which is the thing the self-action guard protects.
    """
    target = await context.resolve_work_item(work_item_id)
    return await transport._act(
        f"/work-items/{transport.segment(target)}/mr-labels", {"labels": labels}
    )


async def set_chain(chain: str, work_item_id: str | None = None) -> dict:
    """Switch a not-yet-started Kraft work item onto a different chain
    (Kraft-gwn6). 404s on an unknown chain id; 409s once the item has a
    `current_node_id` -- the chain is fixed for the life of a started item.

    `resolve_work_item`, not `_forbid_self_action`: this is not a gate
    decision, the same reasoning `mr_labels` above gives for its own door.
    """
    target = await context.resolve_work_item(work_item_id)
    return await transport._patch(
        f"/work-items/{transport.segment(target)}", {"chain_template": chain}
    )


async def set_attachments(
    spec: str | None = None,
    plan: str | None = None,
    drop: list[str] | None = None,
    work_item_id: str | None = None,
) -> dict:
    """Replace or drop a not-yet-started work item's spec/plan attachment
    (Kraft-s7c04.28), instead of abandoning it and filing it again. A path is
    re-copied into Kraft's own storage and resolved the way `create_work_item`
    resolves one; a kind in `drop` is removed, which puts back the gate it
    trimmed. A kind not named keeps its copy. 409s once the item has started.
    `_forbid_self_action`, like the other setters: a worker does not revise the
    documents it was handed.
    """
    changes: dict = {k: v for k, v in (("spec", spec), ("plan", plan)) if v}
    changes |= dict.fromkeys(drop or [])
    if not changes:
        raise ValueError("kraft: set-attachments needs --spec, --plan or --drop")
    target = context._forbid_self_action(work_item_id)
    return await transport._patch(
        f"/work-items/{transport.segment(target)}", {"attachments": changes, "cwd": str(Path.cwd())}
    )


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
    return await transport._patch(
        f"/work-items/{transport.segment(target)}", {"agent_overrides": overrides}
    )


async def set_node_overrides(
    node_id: str,
    auto_escalate: bool | None = None,
    auto_escalate_stuck: bool | None = None,
    auto_escalate_delay_s: int | None = None,
    *,
    model: str | None = None,
    effort: str | None = None,
    extra_prompt: str | None = None,
    clear: bool = False,
    work_item_id: str | None = None,
) -> dict:
    """Set or clear one node's per-item override on a Kraft work item
    (Kraft-uxm3), the door onto the same `auto_escalate`/`auto_escalate_stuck`/
    `auto_escalate_delay_s` fields the Policy screen sets system-wide and a
    chain template sets per node -- without touching either of those -- plus
    the `model`/`effort` its agent tasks launch with and an `extra_prompt`
    appended to each of their instructions (Kraft-a7ers). `clear`
    sends `{}` for this node, dropping its overrides back to the template;
    naming a field sets it and keeps the node's other fields, as it always
    has (the PATCH route merges a node's fields). 409s once the node has
    started.
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
                "model": model,
                "effort": effort,
                "extra_prompt": extra_prompt,
            }.items()
            if v is not None
        }
        if not fields:
            raise ValueError(
                "kraft: set-node-override needs --auto-escalate, --auto-escalate-stuck, "
                "--auto-escalate-delay-s, --model, --effort, --extra-prompt, or --clear"
            )
    return await transport._patch(
        f"/work-items/{transport.segment(target)}", {"node_overrides": {node_id: fields}}
    )


async def set_work_item_policy(
    policy: dict | None = None, *, clear: bool = False, work_item_id: str | None = None
) -> dict:
    """Set or clear a work item's own policy override (Kraft-ab1bh): item-wide
    fields (`max_attempts`, `timeout_minutes`, `allowed_harnesses`; the time
    caps `time_cap_minutes` and `total_time_cap_minutes`, and the safety
    fields, which only tighten) plus
    `paths: {canonical path: {field: value}}` for one node, step or task. It
    *replaces* the whole stored override; `clear` sends `{}`. Held to the
    same bounds as every policy layer, and 422s naming the field it refuses.
    On a running or waiting item it binds from the next node entered and the
    next observation of a wait. `_forbid_self_action`: a worker raising its
    own caps is exactly the self-action the other verbs refuse."""
    target = context._forbid_self_action(work_item_id)
    if not clear and not policy:
        raise ValueError("kraft: set-policy needs --policy FIELD=VALUE or --clear")
    return await transport._patch(
        f"/work-items/{transport.segment(target)}", {"policy": {} if clear else policy}
    )


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
    *,
    start_side: str | None = None,
    quote: str | None = None,
) -> dict:
    """A draft review comment: a new thread, or a reply when `thread_id` is given.
    Drafts go out with the next `submit_review`. `side` is `end_line`'s, and
    `start_side` `start_line`'s when it differs: a range across sides. With no
    `quote`, the server quotes the range from the diff.

    A reply needs no client-side self-action guard: the server already refuses
    a worker session on `POST /threads/{id}/comments` with 403, and a human
    replying to their own item's thread is not a gate decision.
    """
    if thread_id:
        payload = {"body": body}
        return await transport._act(f"/threads/{transport.segment(thread_id)}/comments", payload)
    target = context._forbid_self_action(work_item_id)
    payload: dict = {"body": body, "file_path": file_path, "label": label}
    if start_line is not None:
        end = end_line or start_line
        payload.update(
            side=side or DiffSide.NEW,
            start_line=start_line,
            end_line=end,
            start_side=start_side,
            quote=quote,
        )
        if suggestion is not None:
            payload["suggestion"] = {
                "start_line": start_line,
                "end_line": end,
                "replacement": suggestion,
            }
    return await transport._act(
        f"/work-items/{transport.segment(target)}/threads",
        {k: v for k, v in payload.items() if v is not None},
    )


async def resolve_thread(thread_id: str) -> dict:
    """Mark a review thread resolved."""
    return await transport._act(f"/threads/{transport.segment(thread_id)}/resolve")


async def reopen_thread(thread_id: str) -> dict:
    """Reopen a resolved review thread."""
    return await transport._act(f"/threads/{transport.segment(thread_id)}/reopen")


async def submit_review(
    outcome: str,
    work_item_id: str | None = None,
    summary: str | None = None,
    node: str | None = None,
) -> dict:
    """Send your drafts with an outcome: comment, request_changes, or approve
    (approve needs a pending gate). Only a person should decide this."""
    target = context._forbid_self_action(work_item_id)
    payload = {"outcome": outcome, "summary": summary, "node": node}
    return await transport._act(
        f"/work-items/{transport.segment(target)}/review",
        {k: v for k, v in payload.items() if v is not None},
    )


async def reload_templates() -> dict:
    """Reread the template library from disk into the running server, no
    restart."""
    return await transport._act("/templates/reload")


async def reload_plugins() -> dict:
    """Rebuild the running server's library from `plugins.lock`, leaving any
    pending edit of the other config files pending."""
    return await transport._act("/templates/reload", {"only": ["plugins.yaml", "plugins.lock"]})
