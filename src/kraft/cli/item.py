"""The verbs that change a work item: file one, approve or reject its gate,
pause/resume/retry it, or drop it."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from kraft import client
from kraft.cli import common


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
            )
        ),
        common._render_action,
        ns.json,
    )


def _cmd_approve(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.approve_gate(ns.gate, ns.id)), common._render_action, ns.json)


def _cmd_reject(ns: argparse.Namespace) -> None:
    common.emit(
        asyncio.run(client.reject_gate(ns.note, ns.gate, ns.id, ns.node)),
        common._render_action,
        ns.json,
    )


def _cmd_pause(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.pause(ns.id)), common._render_action, ns.json)


def _cmd_abandon(ns: argparse.Namespace) -> None:
    if not ns.yes:
        raise ValueError("abandon destroys the worktree and anything uncommitted in it; pass --yes")
    common.emit(asyncio.run(client.abandon(ns.id)), common._render_action, ns.json)


def _cmd_resume(ns: argparse.Namespace) -> None:
    steers = {}
    for pair in ns.steer_task or []:
        path, sep, text = pair.partition("=")
        if not sep:
            raise ValueError(f"--steer-task takes PATH=TEXT, not {pair!r}")
        steers[path] = text
    common.emit(
        asyncio.run(client.resume(ns.steer, ns.id, steers=steers or None)),
        common._render_action,
        ns.json,
    )


def _cmd_retry(ns: argparse.Namespace) -> None:
    common.emit(
        asyncio.run(client.retry(ns.steer, ns.id, path=ns.path, restart=ns.restart)),
        common._render_action,
        ns.json,
    )


def _cmd_skip(ns: argparse.Namespace) -> None:
    common.emit(
        asyncio.run(client.skip(ns.note, ns.id, path=ns.path)), common._render_action, ns.json
    )


def _cmd_complete(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.complete(ns.reason, ns.id)), common._render_action, ns.json)


def _cmd_cancel(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.cancel(ns.reason, ns.id)), common._render_action, ns.json)


def _cmd_progress(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.report_progress(ns.task, ns.id)), common._render_action, ns.json)


def _cmd_escalate(ns: argparse.Namespace) -> None:
    common.emit(
        asyncio.run(client.escalate(ns.message, ns.id, new_thread=ns.new_thread)),
        common._render_action,
        ns.json,
    )


def _cmd_mr_label(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.mr_labels(ns.labels, ns.id)), common._render_action, ns.json)


def _cmd_set_chain(ns: argparse.Namespace) -> None:
    common.emit(
        asyncio.run(client.set_chain_template(ns.template, ns.id)), common._render_action, ns.json
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
                clear=ns.clear,
                work_item_id=ns.id,
            )
        ),
        common._render_action,
        ns.json,
    )


def _add_item(subs, common: argparse.ArgumentParser) -> None:
    """The verbs that change a work item."""
    create = subs.add_parser("create", parents=[common], help="file a work item (starts paused)")
    create.add_argument("title")
    create.add_argument(
        "--description",
        help="the brief: what the work actually is, which the spec is written from",
    )
    create.add_argument("--repo", help="default: the repo you are standing in")
    create.add_argument("--chain", default="default", help="chain template (default `default`)")
    # Two flags rather than a repeatable `--attach kind=path`: there are exactly
    # two kinds, the server refuses a duplicate kind, and these document
    # themselves in --help.
    create.add_argument("--spec", help="attach a spec that already exists; skips the spec node")
    create.add_argument("--plan", help="attach a plan that already exists; skips the plan node")
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
    create.set_defaults(func=_cmd_create, all=False)

    approve = subs.add_parser("approve", parents=[common], help="approve the pending gate")
    approve.add_argument("id", nargs="?")
    approve.add_argument("--gate", help="default: whichever gate is pending")
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
        ending.set_defaults(func=fn)

    progress = subs.add_parser(
        "progress", parents=[common], help="say which plan task the implementation has started"
    )
    progress.add_argument("task", type=int, help="the N of the plan's '## Task N' heading")
    progress.add_argument("id", nargs="?")
    progress.set_defaults(func=_cmd_progress)

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
        "set-chain", parents=[common], help="switch a not-yet-started item's chain template"
    )
    # A plain optional positional, like pause/resume/retry -- there is only
    # one positional-shaped argument here (`--template` is a flag either way),
    # unlike `mr-label`, which needs `--id` because a bare positional ahead of
    # its own `nargs="+"` labels would be ambiguous the moment two labels are
    # given with no id.
    set_chain.add_argument("id", nargs="?")
    set_chain.add_argument("--template", required=True, help="a chain template name")
    set_chain.set_defaults(func=_cmd_set_chain)

    set_overrides = subs.add_parser(
        "set-overrides",
        parents=[common],
        help="per-item model/effort override, without changing the chain",
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
        help="per-item auto-escalate override for one node, without touching the template",
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
        "--clear", action="store_true", help="reset this node to the template's own binding"
    )
    set_node_override.set_defaults(func=_cmd_set_node_override)

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
        "abandon", parents=[common], help="drop an item and reclaim its worktree"
    )
    abandon.add_argument("id", nargs="?")
    abandon.add_argument(
        "--yes", action="store_true", help="required: this destroys uncommitted work"
    )
    abandon.set_defaults(func=_cmd_abandon)
