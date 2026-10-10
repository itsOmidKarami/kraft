"""The verbs that change a work item: file one, approve or reject its gate,
pause/resume/retry it, or drop it."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import yaml

from kraft import client, render
from kraft.cli import common
from kraft.vocab import DiffSide, ReplyClaim, ReviewOutcome, ThreadLabel, WorkItemStatus

_POLICY_HELP = (
    "the item's own policy override, FIELD=VALUE item-wide or PATH.FIELD=VALUE for one "
    "node, step or task by canonical path (repeatable), e.g. "
    "merge_request_feedback.ci.await_ci.total_time_cap_minutes=60 or max_attempts=4"
)


def _policy(pairs: list[str]) -> dict | None:
    """`--policy` pairs as the API's override: the last dotted segment of the
    key is the field, anything before it the path. A value is read as YAML,
    so `4` is a number and `[Read,Bash]` a list."""
    policy: dict = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep or not key:
            raise ValueError(f"--policy takes FIELD=VALUE or PATH.FIELD=VALUE, not {pair!r}")
        path, _, name = key.rpartition(".")
        scope = policy.setdefault("paths", {}).setdefault(path, {}) if path else policy
        scope[name] = yaml.safe_load(raw)
    return policy or None


def _node_overrides(pairs: list[str]) -> dict | None:
    """`--node-override NODE.FIELD=VALUE` pairs as the API's `node_overrides`,
    each value read as YAML like `--policy`'s, except an `extra_prompt`, which
    is prose and taken as written. The server checks the fields."""
    overrides: dict = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        node, dot, field = key.partition(".")
        if not (sep and dot and node and field):
            raise ValueError(f"--node-override takes NODE.FIELD=VALUE, not {pair!r}")
        value = raw if field == "extra_prompt" else yaml.safe_load(raw)
        overrides.setdefault(node, {})[field] = value
    return overrides or None


def _budget(raw: str) -> float | None:
    """`--budget`: dollars, or `none` for an explicit no-cap."""
    if raw.lower() == "none":
        return None
    try:
        return float(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"dollars or `none`, not {raw!r}") from None


def _cmd_create(ns: argparse.Namespace) -> None:
    # Resolved here rather than sent as typed: the server joins the path onto a
    # candidate root (the repo, or the worktree we are standing in), never onto
    # the cwd, so `--spec specs/x.md` from a subdirectory of either would miss
    # both. Absolute, it is still accepted only if it lands inside one of them.
    attachments = [
        {"kind": kind, "path": str(Path(value).expanduser().resolve())}
        for kind, value in (("spec", ns.spec), ("plan", ns.plan))
        if value
    ]
    common.emit(
        asyncio.run(
            client.create_work_item(
                ns.title,
                common.repo_scope(ns),
                ns.chain,
                ns.description,
                attachments or None,
                auto_gate=ns.auto_gate,
                implements_beads=ns.implements or None,
                depends_on=ns.after or None,
                policy=_policy(ns.policy),
                base_branch=ns.base_branch,
                skip_nodes=[n for v in ns.skip_nodes for n in v.split(",") if n] or None,
                budget_usd=ns.budget,
                node_overrides=_node_overrides(ns.node_override),
                autostart=ns.autostart,
            )
        ),
        _render_created,
        ns.json,
    )


def _render_created(result: dict) -> str:
    """The created item, and, when an --autostart found every slot busy, why:
    what the board's composer says too."""
    slots = result.get("slots")
    shown = common._render_action({k: v for k, v in result.items() if k != "slots"})
    if not slots:
        return shown
    if queued := _held(result):
        return f"{shown}\n{queued}"
    return (
        f"{shown}\nfiled paused: {slots['busy']} of {slots['limit']} slots are busy. "
        f"Start it when one frees: kraft item resume {result['id']}"
    )


async def _gate_of(ns: argparse.Namespace) -> str:
    """The gate `--gate` names, else the one pending, which the summary line
    names: resolved here rather than in the client, the same one read."""
    if ns.gate:
        return ns.gate
    return await client.actions._pending_gate_of(client.context._forbid_self_action(ns.id))


def _cmd_approve(ns: argparse.Namespace) -> None:
    async def go():
        gate = await _gate_of(ns)
        return gate, await client.approve_gate(gate, ns.id, ns.digest)

    gate, result = asyncio.run(go())
    common.emit(result, common.item_action(f"approved {gate} on {{id}}"), ns.json)


def _cmd_reject(ns: argparse.Namespace) -> None:
    # Before the gate is looked up: a missing note is the caller's to fix
    # whatever the item is waiting on.
    if not ns.note.strip():
        raise ValueError("a reject note is required: say what is wrong")

    async def go():
        gate = await _gate_of(ns)
        return gate, await client.reject_gate(ns.note, gate, ns.id, ns.node)

    gate, result = asyncio.run(go())
    common.emit(result, common.item_action(f"rejected {gate} on {{id}}"), ns.json)


def _paused(result: dict) -> str:
    stopped = len(result.get("paused_sessions") or [])
    how = f", stopping {stopped} running session{'s' if stopped != 1 else ''}" if stopped else ""
    return f"paused {result['id']}{how}; kraft item resume {result['id']} carries on"


def _cmd_pause(ns: argparse.Namespace) -> None:
    common.emit(
        asyncio.run(client.pause(ns.id)), common.item_action("paused {id}", small=_paused), ns.json
    )


def _unblocked(result: dict) -> str:
    dropped = result.get("dropped") or []
    if not dropped:
        return f"{result['id']} comes after nothing unfinished: nothing was dropped"
    line = f"{result['id']} no longer comes after {', '.join(dropped)}"
    if left := result.get("waiting_on"):
        line += f"; it still waits on {', '.join(d['id'] for d in left)}"
    return line


def _cmd_unblock(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.unblock(ns.id, ns.dependency)), _unblocked, ns.json)


def _is_cancelled(item_id: str | None) -> bool:
    """Whether the item is already cancelled or abandoned: both store
    `abandoned`, and the server cannot tell them apart. A server that does not answer,
    or an id that is not an item, only costs the refusal its detail."""
    try:
        return asyncio.run(client.get_work_item(item_id)).get("status") == WorkItemStatus.ABANDONED
    except Exception:  # noqa: BLE001
        return False


def _cmd_abandon(ns: argparse.Namespace) -> None:
    if not ns.yes:
        # Cancel is already done for an item in `abandoned`, so "cancel it
        # instead" would be advice to do what is done; abandoning it can only
        # reclaim what cancel left (or nothing, after an earlier abandon).
        keep = (
            "This item is already cancelled; abandoning it only reclaims its worktree and branch."
            if _is_cancelled(ns.id)
            else "To keep them, cancel the item instead."
        )
        raise ValueError(
            "abandon deletes the worktree and the item's branch: uncommitted work and "
            f"commits you never pushed are lost. {keep} To go ahead, pass --yes"
        )
    common.emit(asyncio.run(client.abandon(ns.id)), common.item_action("abandoned {id}"), ns.json)


#: What the preview and bulk archive routes accept in one call.
_ARCHIVE_MAX = 200


def _archive_ids(ns: argparse.Namespace) -> tuple[list[str], int]:
    """Who `item archive` takes, and how many it found: the largest
    `_ARCHIVE_MAX` of the items the Storage view marks reclaimable
    (`--reclaimable`, listed largest first), else the IDs given, else the item
    you are standing in. Each explicit one goes through the worker self-action
    guard, as `item abandon` does."""
    if ns.reclaimable:
        if ns.ids:
            raise ValueError("give IDs or --reclaimable, not both")
        found = [i["id"] for i in asyncio.run(client.storage_usage())["items"] if i["reclaimable"]]
        return found[:_ARCHIVE_MAX], len(found)
    ids = [client.context._forbid_self_action(i) for i in ns.ids or [None]]
    return ids, len(ids)


def _cmd_archive(ns: argparse.Namespace) -> None:
    ids, found = _archive_ids(ns)
    if not ids:
        common.emit({"results": []}, lambda _: "nothing to archive", ns.json)
        return
    preview = asyncio.run(client.storage_preview(ids))
    # With --json --yes stdout is the one bulk payload, nothing else.
    if not (ns.json and ns.yes):
        common.emit(preview, render.storage_preview, ns.json)
    if not ns.yes:
        raise ValueError(
            "nothing was archived. Archiving deletes each worktree and the uncommitted "
            "changes in it; a branch with commits nothing else holds stays. "
            "To go ahead, pass --yes"
        )
    # Only what the preview called archivable is sent: the bulk route answers ok
    # for an already-archived id, which would print "archived" over a preview
    # that said it was not.
    refused = {i["id"]: i["refusal"] for i in preview["items"] if not i["archivable"]}
    sent = [i for i in ids if i not in refused]
    out = asyncio.run(client.archive(sent)) if sent else {"results": []}
    out["results"] += [{"id": i, "ok": False, "error": why} for i, why in refused.items()]
    out["results"].sort(key=lambda r: ids.index(r["id"]))
    common.emit(out, render.archive_results, ns.json)
    if found > len(ids):
        # stderr under --json, so stdout stays the one payload.
        print(
            f"archived the {len(ids)} largest of {found} reclaimable items; "
            "run it again for the rest",
            file=sys.stderr if ns.json else sys.stdout,
        )
    failed = sum(1 for r in out["results"] if not r["ok"])
    if failed:
        # A script must be able to tell: the bulk route answers 200 either way.
        raise ValueError(f"{failed} of {len(ids)} not archived")


def _cmd_restore(ns: argparse.Namespace) -> None:
    common.emit(
        asyncio.run(client.restore(ns.id)),
        lambda r: f"restored {r['id']}: back under Done, without its worktree",
        ns.json,
    )


def _cmd_resume(ns: argparse.Namespace) -> None:
    steers = {}
    for pair in ns.steer_task or []:
        path, sep, text = pair.partition("=")
        if not sep:
            raise ValueError(f"--steer-task takes PATH=TEXT, not {pair!r}")
        steers[path] = text
    common.emit(
        asyncio.run(client.resume(ns.steer, ns.id, steers=steers or None)),
        common.item_action("resumed {id}", small=_resumed),
        ns.json,
    )


def _queued(result: dict) -> str | None:
    """A start the board had no slot for, or None for any other answer."""
    if result.get("status") != WorkItemStatus.QUEUED:
        return None
    slots = result.get("slots") or {}
    return (
        f"queued {result['id']}: {slots.get('busy')} of {slots.get('limit')} slots are busy. "
        f"It starts when one frees; kraft item pause {result['id']} takes it out of the queue"
    )


def _blocked(result: dict) -> str | None:
    """A start held behind unfinished items, or None for any other answer."""
    if result.get("status") != WorkItemStatus.BLOCKED:
        return None
    on = ", ".join(f"{d['id']} ({d['status']})" for d in result.get("waiting_on") or [])
    return (
        f"blocked {result['id']}: it comes after {on}. It starts when they complete; "
        f"kraft item unblock {result['id']} drops them"
    )


def _held(result: dict) -> str | None:
    """A start that did not start: queued for a slot, or blocked."""
    return _queued(result) or _blocked(result)


def _resumed(result: dict) -> str:
    """resume's usual answer: the node it went on from, and the agent tasks
    a steer reached. No node is an item that had never started."""
    if queued := _held(result):
        return queued
    node = result.get("node_id")
    line = f"resumed {result['id']} at {node}" if node else f"started {result['id']}"
    if steered := result.get("steered"):
        line += f"; steered {', '.join(steered)}"
    return line


def _rerun_at(result: dict) -> str:
    """Where a retry's run starts, from its usual answer, and its attempt."""
    where = result.get("path") or result.get("node_id") or "its first node"
    attempt = result.get("attempt")
    return f"{where}, attempt {attempt}" if attempt else str(where)


def _cmd_retry(ns: argparse.Namespace) -> None:
    common.emit(
        asyncio.run(client.retry(ns.steer, ns.id, path=ns.path, restart=ns.restart)),
        common.item_action(
            "retried {id}",
            small=lambda r: _held(r) or f"retried {r['id']} at {_rerun_at(r)}",
        ),
        ns.json,
    )


def _cmd_raise_budget(ns: argparse.Namespace) -> None:
    cap = "no cap" if ns.usd is None else f"${ns.usd:g}"
    common.emit(
        asyncio.run(client.raise_budget(ns.usd, ns.id)),
        common.item_action(
            f"raised the cap on {{id}} to {cap} and retried it",
            small=lambda r: (
                f"raised the cap on {r['id']} to {cap}; "
                + (_held(r) or f"retried at {_rerun_at(r)}")
            ),
        ),
        ns.json,
    )


def _skipping(ns: argparse.Namespace) -> str:
    """What `skip` passes over, read before it runs, as the server picks it:
    `--path`, else the pending gate, else the current node. An item that
    cannot be read here costs the line its name, not the skip."""
    if ns.path:
        return ns.path
    try:
        item = asyncio.run(client.get_work_item(ns.id))
    except Exception:  # noqa: BLE001
        return "the current node"
    return item.get("pending_gate") or item.get("current_node_id") or "the current node"


def _cmd_skip(ns: argparse.Namespace) -> None:
    what = "" if ns.json else _skipping(ns)
    common.emit(
        asyncio.run(client.skip(ns.note, ns.id, path=ns.path)),
        common.item_action(f"skipped {what} on {{id}}"),
        ns.json,
    )


def _cmd_complete(ns: argparse.Namespace) -> None:
    common.emit(
        asyncio.run(client.complete(ns.reason, ns.id, close_beads=ns.close_beads)),
        common.item_action(
            "marked {id} complete" + ("; its beads are closed" if ns.close_beads else ""),
            status=False,
        ),
        ns.json,
    )


def _cmd_cancel(ns: argparse.Namespace) -> None:
    common.emit(
        asyncio.run(client.cancel(ns.reason, ns.id)),
        common.item_action(
            "cancelled {id}; its worktree and branch stay (kraft item abandon {id} --yes "
            "deletes them)",
            status=False,
        ),
        ns.json,
    )


def _cmd_progress(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.report_progress(ns.task, ns.id)), common._render_action, ns.json)


def _cmd_reply(ns: argparse.Namespace) -> None:
    common.emit(
        asyncio.run(client.reply_to_thread(ns.thread, ns.body, ns.claim)),
        common._render_action,
        ns.json,
    )


def _cmd_escalate(ns: argparse.Namespace) -> None:
    common.emit(
        asyncio.run(client.escalate(ns.message, ns.id, new_thread=ns.new_thread)),
        common.item_action(
            "asked an agent about {id}",
            small=lambda r: (
                f"asked an agent about {r['id']}'s stop; kraft view show {r['id']} follows it"
            ),
        ),
        ns.json,
    )


def _cmd_mr_label(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.mr_labels(ns.labels, ns.id)), common._render_action, ns.json)


def _set_chain(result: dict) -> str:
    """The PATCH answers `{id, chain_template}`, the 1.x key; it is a chain."""
    return f"{result['id']} now runs the {result.get('chain_template')} chain"


def _cmd_set_chain(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.set_chain(ns.chain, ns.id)), _set_chain, ns.json)


def _cmd_set_attachments(ns: argparse.Namespace) -> None:
    # Absolute for the same reason `_cmd_create` gives.
    spec, plan = (str(Path(v).expanduser().resolve()) if v else None for v in (ns.spec, ns.plan))
    common.emit(
        asyncio.run(client.set_attachments(spec, plan, ns.drop, ns.id)),
        common._render_action,
        ns.json,
    )


def _cmd_set_overrides(ns: argparse.Namespace) -> None:
    common.emit(
        asyncio.run(
            client.set_agent_overrides(
                ns.model, ns.escalate_model, ns.effort, clear=ns.clear, work_item_id=ns.id
            )
        ),
        common._render_action,
        ns.json,
    )


def _cmd_set_node_override(ns: argparse.Namespace) -> None:
    common.emit(
        asyncio.run(
            client.set_node_overrides(
                ns.node,
                ns.auto_escalate,
                ns.auto_escalate_stuck,
                ns.auto_escalate_delay_s,
                model=ns.model,
                effort=ns.effort,
                extra_prompt=ns.extra_prompt,
                clear=ns.clear,
                work_item_id=ns.id,
            )
        ),
        common._render_action,
        ns.json,
    )


#: `--lines A-B`: `A` alone is `A-A`.
def _lines(raw: str) -> tuple[int, int]:
    a, sep, b = raw.partition("-")
    try:
        start = int(a)
        end = int(b) if sep else start
    except ValueError:
        raise argparse.ArgumentTypeError(f"--lines takes A or A-B, not {raw!r}") from None
    return start, end


#: The CLI spells a label with a dash; the API takes it with an underscore.
_LABELS = {m.value.replace("_", "-"): m for m in ThreadLabel}
_SIDES = [m.value for m in DiffSide]


def _cmd_comment(ns: argparse.Namespace) -> None:
    if ns.suggest is not None and ns.lines is None:
        ns._parser.error("--suggest needs --lines")
    if ns.start_side is not None and ns.lines is None:
        ns._parser.error("--start-side needs --lines")
    if ns.suggest is not None and DiffSide.OLD in (ns.side, ns.start_side):
        ns._parser.error("--suggest replaces new-side lines, so it takes no old-side range")
    start, end = ns.lines if ns.lines else (None, None)
    common.emit(
        asyncio.run(
            client.add_review_comment(
                ns.body,
                work_item_id=ns.id,
                thread_id=ns.reply,
                file_path=ns.file_path,
                start_line=start,
                end_line=end,
                side=ns.side,
                label=_LABELS[ns.label] if ns.label else None,
                suggestion=ns.suggest,
                start_side=ns.start_side,
            )
        ),
        _render_comment,
        ns.json,
    )


def _render_comment(result: dict) -> str:
    """A new thread as `kraft view threads` names it, with the lines it
    quotes, which is what the agent will read; a reply as its fields."""
    if "comments" not in result:
        return common._render_action(result)
    lines = [f"drafted thread {result['id']} on {render.thread_where(result)}"]
    lines += ["    | " + line for line in (result.get("quote") or "").splitlines()]
    lines.append("it goes out with your next kraft item review")
    return "\n".join(lines)


def _thread_state(done: str):
    """`resolved thread T on calc.py:+9 to +11`: one line, as `comment` names a
    new thread, not the whole thread as `key  value` lines (R11a)."""

    def render_state(result: dict) -> str:
        if "comments" not in result:
            return common._render_action(result)
        return f"{done} thread {result['id']} on {render.thread_where(result)}"

    return render_state


def _cmd_resolve(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.resolve_thread(ns.thread)), _thread_state("resolved"), ns.json)


def _cmd_reopen(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.reopen_thread(ns.thread)), _thread_state("reopened"), ns.json)


def _cmd_review(ns: argparse.Namespace) -> None:
    outcome = ReviewOutcome(ns.outcome.replace("-", "_"))
    result = asyncio.run(client.submit_review(outcome, ns.id, ns.summary, ns.node))
    if not ns.json and "target" in result:
        print(
            f"request-changes -> {result['target']} ({result['target_reason']}), {result['action']}"
        )
    common.emit(result, common.item_action(f"sent your review ({ns.outcome}) on {{id}}"), ns.json)


def _cmd_set_policy(ns: argparse.Namespace) -> None:
    common.emit(
        asyncio.run(
            client.set_work_item_policy(_policy(ns.policy), clear=ns.clear, work_item_id=ns.id)
        ),
        common._render_action,
        ns.json,
    )


def _add_item(subs, common: argparse.ArgumentParser) -> None:
    """The verbs that change a work item."""
    create = subs.add_parser(
        "create",
        parents=[common],
        help="file a work item (paused unless --autostart; queued if every slot is busy)",
    )
    create.add_argument("title", help="the item's title: one line, as the board shows it")
    create.add_argument(
        "--description",
        help="the brief: what the work actually is, which the spec is written from",
    )
    create.add_argument("--repo", help="default: the repo you are standing in")
    create.add_argument(
        "--chain",
        help="the chain to run (default: the repo's default_chain, else `default`)",
    )
    create.add_argument(
        "--base-branch",
        metavar="BRANCH",
        help="the branch the work starts from and its merge request targets "
        "(default: the repo's default branch); must exist on origin",
    )
    # Two flags rather than a repeatable `--attach kind=path`: there are exactly
    # two kinds, the server refuses a duplicate kind, and these document
    # themselves in --help.
    # Inside the repo: the copy is committed on the item's branch (R10a-02).
    create.add_argument(
        "--spec",
        metavar="PATH",
        help="attach a spec that already exists, a file inside the repo (e.g. under "
        ".engineering/specs/); skips the spec node",
    )
    create.add_argument(
        "--plan",
        metavar="PATH",
        help="attach a plan that already exists, a file inside the repo (e.g. under "
        ".engineering/plans/); skips the plan node",
    )
    create.add_argument(
        "--auto-gate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="let an agent review this item's auto-escalate gates before a human does",
    )
    create.add_argument(
        "--implements",
        action="append",
        default=[],
        metavar="BEAD",
        help="a bead this item implements, closed on completion (repeatable); "
        "ids in --description are not parsed",
    )
    create.add_argument(
        "--after",
        action="append",
        default=[],
        metavar="ID",
        help="a work item this one comes after (repeatable): started while it is "
        "unfinished, this item is blocked and starts when it completes",
    )
    create.add_argument(
        "--policy", action="append", default=[], metavar="KEY=VALUE", help=_POLICY_HELP
    )
    create.add_argument(
        "--skip-nodes",
        action="append",
        default=[],
        metavar="NODE[,NODE]",
        help="drop these nodes from the item's chain at intake (repeatable or comma-separated)",
    )
    create.add_argument(
        "--budget",
        type=_budget,
        default=...,
        metavar="USD|none",
        help="the item's spend cap in dollars; `none` for no cap (default: the policy's)",
    )
    create.add_argument(
        "--node-override",
        action="append",
        default=[],
        metavar="NODE.FIELD=VALUE",
        help="a per-node override at intake, the fields set-node-override takes "
        "(repeatable), e.g. plan.auto_escalate=true or implementation.attempts=2",
    )
    create.add_argument(
        "--autostart",
        action="store_true",
        help="start it now instead of leaving it paused for a human; queued when every slot "
        "is busy; refused from a Kraft worker",
    )
    create.set_defaults(func=_cmd_create, all=False)

    approve = subs.add_parser("approve", parents=[common], help="approve the pending gate")
    approve.add_argument("id", nargs="?")
    approve.add_argument("--gate", help="default: whichever gate is pending")
    approve.add_argument("--digest", help="a chain revision's, as `kraft view artifact` printed it")
    approve.set_defaults(func=_cmd_approve)

    reject = subs.add_parser("reject", parents=[common], help="reject the pending gate")
    reject.add_argument("id", nargs="?")
    reject.add_argument("--note", required=True, help="what is wrong; a rejection needs a reason")
    reject.add_argument("--gate", help="default: whichever gate is pending")
    reject.add_argument(
        "--node", help="re-enter the chain at this node; default: the chain's own reject_to"
    )
    reject.set_defaults(func=_cmd_reject)

    pause = subs.add_parser("pause", parents=[common], help="stop the running attempt")
    pause.add_argument("id", nargs="?")
    pause.set_defaults(func=_cmd_pause)

    unblock = subs.add_parser(
        "unblock", parents=[common], help="drop what a blocked or paused item still comes after"
    )
    unblock.add_argument("id", nargs="?")
    unblock.add_argument("--dependency", metavar="ID", help="drop only this one")
    unblock.set_defaults(func=_cmd_unblock)

    resume = subs.add_parser("resume", parents=[common], help="start or restart a paused item")
    resume.add_argument("id", nargs="?")
    resume.add_argument("--steer", help="reaches every paused agent task")
    resume.add_argument(
        "--steer-task",
        action="append",
        metavar="PATH=TEXT",
        help="a steer for one paused agent task, by canonical path (node.step.task); repeatable",
    )
    resume.set_defaults(func=_cmd_resume)

    retry = subs.add_parser(
        "retry", parents=[common], help="re-run the node a stopped item stopped on"
    )
    retry.add_argument("id", nargs="?")
    retry.add_argument("--steer", help="carried into the retry's prompt")
    retry_what = retry.add_mutually_exclusive_group()
    retry_what.add_argument(
        "--path",
        help="what to rerun, with everything after it: node, node.step or node.step.task",
    )
    retry_what.add_argument(
        "--restart", action="store_true", help="rerun the whole chain from its first node"
    )
    retry.set_defaults(func=_cmd_retry)

    raise_budget = subs.add_parser(
        "raise-budget",
        parents=[common],
        help="raise the dollar cap that stopped an item, its own (create --budget) or its "
        "policy's item-wide budget_usd, and retry it",
    )
    raise_budget.add_argument("id", nargs="?")
    raise_budget.add_argument(
        "--usd", type=_budget, required=True, help="the new cap in dollars, or `none` for no cap"
    )
    raise_budget.set_defaults(func=_cmd_raise_budget)

    skip = subs.add_parser(
        "skip", parents=[common], help="advance past the current node or gate without running it"
    )
    skip.add_argument("id", nargs="?")
    skip.add_argument("--note", help="optional reason, recorded on the skip's event")
    skip.add_argument(
        "--path",
        help="skip only this node.step or node.step.task of the current node",
    )
    skip.set_defaults(func=_cmd_skip)

    for verb, fn, what in (
        ("complete", _cmd_complete, "mark the item complete by hand"),
        ("cancel", _cmd_cancel, "cancel the item (its worktree stays)"),
    ):
        ending = subs.add_parser(verb, parents=[common], help=what)
        ending.add_argument("id", nargs="?")
        ending.add_argument("--reason", required=True, help="why; recorded on the audit event")
        if verb == "complete":
            ending.add_argument(
                "--close-beads",
                action="store_true",
                help="also close the item's beads (off: work done by hand may live elsewhere)",
            )
        ending.set_defaults(func=fn)

    progress = subs.add_parser(
        "progress", parents=[common], help="say which plan task the implementation has started"
    )
    progress.add_argument("task", type=int, help="the N of the plan's '## Task N' heading")
    progress.add_argument("id", nargs="?")
    progress.set_defaults(func=_cmd_progress)

    reply = subs.add_parser(
        "reply", parents=[common], help="answer a review thread (worker sessions only)"
    )
    reply.add_argument("thread", help="the thread id from the review note")
    reply.add_argument("--body", required=True)
    reply.add_argument("--claim", choices=[m.value for m in ReplyClaim])
    reply.set_defaults(func=_cmd_reply)

    comment = subs.add_parser(
        "comment", parents=[common], help="a draft review comment, or a reply with --reply"
    )
    comment.add_argument("id", nargs="?")
    comment.add_argument("--body", required=True)
    comment.add_argument("--reply", metavar="THREAD", help="reply to this thread instead")
    comment.add_argument("--file", dest="file_path", help="the file this comment is about")
    comment.add_argument("--lines", type=_lines, metavar="A[-B]", help="a line range in --file")
    comment.add_argument(
        "--side",
        choices=_SIDES,
        help="the side B is on (and A, unless --start-side); default: new",
    )
    comment.add_argument(
        "--start-side",
        choices=_SIDES,
        help="the side A is on, for a range across sides: --lines 3-2 --start-side old "
        "--side new is old line 3 through new line 2",
    )
    comment.add_argument("--label", choices=list(_LABELS))
    comment.add_argument("--suggest", metavar="TEXT", help="a suggested replacement for --lines")
    comment.set_defaults(func=_cmd_comment, _parser=comment)

    resolve = subs.add_parser("resolve", parents=[common], help="mark a review thread resolved")
    resolve.add_argument("thread")
    resolve.set_defaults(func=_cmd_resolve)

    reopen = subs.add_parser("reopen", parents=[common], help="reopen a resolved review thread")
    reopen.add_argument("thread")
    reopen.set_defaults(func=_cmd_reopen)

    review = subs.add_parser("review", parents=[common], help="send your drafted review comments")
    review.add_argument("id", nargs="?")
    review.add_argument(
        "outcome",
        choices=[
            o.value.replace("_", "-")
            for o in (ReviewOutcome.COMMENT, ReviewOutcome.APPROVE, ReviewOutcome.REQUEST_CHANGES)
        ],
    )
    review.add_argument("--summary")
    review.add_argument(
        "--node", help="where request-changes re-runs; default: derived from threads"
    )
    review.set_defaults(func=_cmd_review)

    escalate = subs.add_parser(
        "escalate", parents=[common], help="ask an agent to help resolve a needs_human stop"
    )
    escalate.add_argument("id", nargs="?")
    escalate.add_argument("--message", required=True, help="what to tell the agent")
    escalate.add_argument(
        "--new-thread",
        action="store_true",
        help="start a fresh agent session instead of continuing the latest escalation thread",
    )
    escalate.set_defaults(func=_cmd_escalate)

    set_chain = subs.add_parser(
        "set-chain", parents=[common], help="switch a not-yet-started item's chain"
    )
    # A plain optional positional, like pause/resume/retry -- there is only
    # one positional-shaped argument here (`--chain` is a flag either way),
    # unlike `mr-label`, which needs `--id` because a bare positional ahead of
    # its own `nargs="+"` labels would be ambiguous the moment two labels are
    # given with no id.
    set_chain.add_argument("id", nargs="?")
    # `--template` was the 1.x spelling; still accepted, with no warning.
    set_chain.add_argument(
        "--chain", "--template", dest="chain", required=True, help="a chain id", metavar="CHAIN"
    )
    set_chain.set_defaults(func=_cmd_set_chain)

    set_attachments = subs.add_parser(
        "set-attachments",
        parents=[common],
        help="replace or drop a not-yet-started item's spec/plan, instead of re-filing it",
    )
    set_attachments.add_argument("id", nargs="?")
    set_attachments.add_argument(
        "--spec",
        metavar="PATH",
        help="the revised spec, a file inside the repo, re-copied into Kraft",
    )
    set_attachments.add_argument(
        "--plan",
        metavar="PATH",
        help="the revised plan, a file inside the repo, re-copied into Kraft",
    )
    set_attachments.add_argument(
        "--drop",
        action="append",
        choices=["spec", "plan"],
        help="remove that attachment, which puts back the gate it trimmed (repeatable)",
    )
    set_attachments.set_defaults(func=_cmd_set_attachments)

    set_overrides = subs.add_parser(
        "set-overrides",
        parents=[common],
        help="per-item model/effort override, without changing the chain; "
        "replaces the whole override",
        epilog="Each call replaces the item-wide override: a flag you leave out goes back "
        "to the template's own binding. To change one node alone, use set-node-override.",
    )
    set_overrides.add_argument("id", nargs="?")
    set_overrides.add_argument("--model", help="plain model override")
    set_overrides.add_argument(
        "--escalate-model", help="override for the fix loop's escalation model"
    )
    set_overrides.add_argument("--effort", help="low, medium, high, xhigh, or max")
    set_overrides.add_argument(
        "--clear", action="store_true", help="reset every field to the template's own binding"
    )
    set_overrides.set_defaults(func=_cmd_set_overrides)

    set_node_override = subs.add_parser(
        "set-node-override",
        parents=[common],
        help="per-item override for one node (auto-escalate, model, effort, extra prompt), "
        "without touching the template",
        epilog="Each call changes only the flags you give and keeps the node's other "
        "overrides; --clear resets the node. Unlike set-overrides, it merges.",
    )
    set_node_override.add_argument("id", nargs="?")
    set_node_override.add_argument("--node", required=True, help="a node id in the item's chain")
    set_node_override.add_argument(
        "--auto-escalate",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="arm/disarm agent review of this node's auto-escalate gates",
    )
    set_node_override.add_argument(
        "--auto-escalate-stuck",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="arm/disarm auto-escalate-on-stuck for this node",
    )
    set_node_override.add_argument(
        "--auto-escalate-delay-s", type=int, help="delay before this node's auto-escalate fires"
    )
    set_node_override.add_argument(
        "--model", help="the model this node's agent tasks run on, over set-overrides"
    )
    set_node_override.add_argument(
        "--effort", help="the effort this node's agent tasks run at, over set-overrides"
    )
    set_node_override.add_argument(
        "--extra-prompt", help="text appended to every agent task's instruction in this node"
    )
    set_node_override.add_argument(
        "--clear", action="store_true", help="reset this node to the template's own binding"
    )
    set_node_override.set_defaults(func=_cmd_set_node_override)

    set_policy = subs.add_parser(
        "set-policy",
        parents=[common],
        help="replace the item's own policy override; binds from its next node or wait check",
    )
    set_policy.add_argument("id", nargs="?")
    set_policy.add_argument(
        "--policy", action="append", default=[], metavar="KEY=VALUE", help=_POLICY_HELP
    )
    set_policy.add_argument("--clear", action="store_true", help="drop the item's own override")
    set_policy.set_defaults(func=_cmd_set_policy)

    mr_label = subs.add_parser(
        "mr-label",
        parents=[common],
        help="label this item's merge request and re-create its pipeline",
    )
    # `--id`, not a positional: a positional `id` ahead of `labels` (nargs="+")
    # is ambiguous the moment two labels are given with no id — argparse's
    # greedy match eats the first label as the id.
    mr_label.add_argument("--id", help="default: the work item you are standing in")
    mr_label.add_argument("labels", nargs="+", help="e.g. release::patch")
    mr_label.set_defaults(func=_cmd_mr_label)

    abandon = subs.add_parser(
        "abandon", parents=[common], help="drop an item, deleting its worktree and branch"
    )
    abandon.add_argument("id", nargs="?")
    abandon.add_argument(
        "--yes",
        action="store_true",
        help="required: uncommitted work and unpushed commits are lost",
    )
    abandon.set_defaults(func=_cmd_abandon)

    archive = subs.add_parser(
        "archive",
        parents=[common],
        help="archive finished items, deleting their worktrees (previews first)",
    )
    archive.add_argument("ids", nargs="*", metavar="ID", help="default: the item you are in")
    archive.add_argument(
        "--reclaimable",
        action="store_true",
        help="the 200 largest completed or abandoned items not archived yet (run again for more)",
    )
    archive.add_argument(
        "--yes",
        action="store_true",
        help="required: uncommitted changes in each worktree are lost",
    )
    archive.set_defaults(func=_cmd_archive)

    restore = subs.add_parser(
        "restore", parents=[common], help="put an archived item back under Done"
    )
    restore.add_argument("id", nargs="?")
    restore.set_defaults(func=_cmd_restore)
