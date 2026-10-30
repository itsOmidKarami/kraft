"""Agent review of a gate before a human sees it
(docs/superpowers/specs/2026-09-10-auto-escalate-gates-design.md, Kraft-zr3s).

Dispatched through the same `run_agent_task` engine every chain node uses, and
deliberately *as a worker*: `identify_as_worker=True` means
`client.context._forbid_self_action` already refuses to let this session approve or
reject its own item. The agent therefore cannot clear the gate however it is
prompted -- it must report a verdict, and `kraft.executor.gates.review_gates`
applies it.
That is the opposite choice from `escalate.py`, which is not a worker precisely
so that a human's escalation agent *can* act.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

from kraft import caps, events, executor, inplace, item_paths, store
from kraft import harness as _harness
from kraft import overrides as _overrides
from kraft.adapters import agent as _agent
from kraft.adapters import subprocess as _subprocess
from kraft.adapters.profiles import HarnessUnavailable, harness_table
from kraft.executor import read_only
from kraft.executor.fallback import fallback_list
from kraft.remote import placement as _placement
from kraft.templates.models import CHECKS_FIRST, AgentTask, GateNode, ResolvedNode
from kraft.vocab import ADVANCING, GateEvent
from kraft.worker import steering as _steering
from kraft.worker.worktree_read import open_no_symlinks

#: The only strings a deciding reviewer's verdict may be (one that checks
#: first has its own two, in `review`). Anything else -- a typo, a sentence, a
#: missing key -- is `undecided`, which leaves the gate pending for a human.
VERDICTS = frozenset({"approve", "reject", "fixed", "undecided"})

#: The item under review and how to report on it, in both modes.
_ITEM = (
    "Title: {title}\n"
    "{description_line}"
    "{artifact_line}"
    "\n"
    "You are a Kraft worker for this launch, so you cannot approve or reject "
    "the gate yourself -- and must not try. Report your decision by writing "
    "`verdict` into your result file, with your reasoning in `concerns`:\n"
    "\n"
)
_OPENING = (
    "This is a gate review. A Kraft work item has reached the gate "
    "{gate!r} on node {node_id!r}, and is waiting for a decision. "
)
_REJECT = (
    '  "verdict": "reject"    -- send it back to be redone. `concerns` becomes '
    "the steering note for the agent that redoes it, so write it as an "
    "instruction, not a complaint. Required: a rejection with no reasoning is "
    "discarded.\n"
)

_PROMPT = (
    _OPENING
    + "You are deciding whether it needs a person.\n\n"
    + _ITEM
    + '  "verdict": "approve"   -- the gate should clear; you are confident.\n'
    + _REJECT
    + '  "verdict": "fixed"     -- you repaired something in this worktree. You '
    "may edit and commit here, but you may not then approve your own edit: the "
    "node re-runs and is measured again. `concerns` says what you changed. Note "
    "what that costs: the whole node runs again and is reviewed again, so this "
    "is the right call for a defect you would otherwise have rejected for, and "
    "the wrong one for a nit you could have let through.\n"
    '  "verdict": "undecided" -- anything else. A person looks at it, with your '
    "`concerns` in front of them.\n"
    "\n"
    "Prefer `undecided` whenever you are unsure. A gate wrongly cleared is "
    "expensive and a gate wrongly held costs a human one glance."
)

#: For a reviewer that checks first (`auto_review_mode: checks_first`): it
#: cannot approve, and the person decides after it whatever it reports.
_CHECKS_FIRST_PROMPT = (
    _OPENING
    + "You check it before a person does: you cannot approve it.\n\n"
    + _ITEM
    + _REJECT
    + '  "verdict": "pass"      -- hand it to the person. `concerns` is required '
    "here too: it is what they read first, so say what you checked and any "
    "doubts you have.\n"
    "\n"
    "A person decides after you, whatever you report. Report `pass` whenever "
    "you are unsure: the person reads the work next anyway, and a rejection "
    "costs a full re-run of the node.\n"
    "\n"
    "Do not edit anything. Read the worktree; do not change, write or commit "
    "any file in it. If you do, Kraft ignores your verdict."
)

_VOIDED = (
    "The reviewer changed files, so its verdict was ignored; its changes are "
    "still in the worktree: "
)
_VOIDED_IN_COPY = (
    "The reviewer changed files in its copy of the branch, so its verdict was ignored; "
    "Kraft removed the copy and left your working files as they were: "
)
#: Whoever moved it: the reviewer, by a push from its copy, or the person.
_TIP_MOVED = "The item's branch moved during the review, so the verdict was ignored: "


def checks_first(node: ResolvedNode) -> bool:
    """True for a gate whose reviewer only checks first: it may reject, or
    `pass` the gate to the person, and never approve."""
    return isinstance(node.node, GateNode) and node.node.auto_review_mode == CHECKS_FIRST


def _artifact_line(path: Path | None) -> str:
    if path is None:
        return "There is no artifact document for this gate; read the worktree.\n"
    return f"The document this gate is about: {path}\n"


def _document(run_dirs, work_item_id: str, rel: str | None) -> str | None:
    """A hash of the whole of the gate's document as it stands, `"absent"` when
    nothing is there and `"unreadable"` when it is no regular file reached
    without a link; None for a gate with none. The reviewer may write here, so
    it is opened as `read_document` opens it: no link followed, no FIFO waited on.
    Blocking and unbounded: callers run it in a thread, off the event loop."""
    if rel is None:
        return None
    try:
        fd = open_no_symlinks(*item_paths.locate(run_dirs, work_item_id, rel))
    except FileNotFoundError:
        return "absent"
    except (OSError, ValueError, IndexError):
        return "unreadable"
    try:
        # Whole, not `read_document`'s first 1 MB: an edit past it is still an edit.
        with os.fdopen(fd, "rb") as fh:
            return hashlib.file_digest(fh, "sha256").hexdigest()
    except OSError:
        return "unreadable"


def _profile_fallback_refusal(
    task: AgentTask, gate: str, pins=None, repo_id: str | None = None
) -> str | None:
    """None, or the operator-facing reason `task` must not launch (Kraft-t4y8g):
    its `profile:` names an agent profile that itself carries a `fallback:`
    list.

    `GateNode._no_handler` already refuses a gate whose `auto_review` task
    declares its own `fallback:`, at template load -- "a gate review never
    falls back" (this module launches its reviewer once; the loop lives in
    `dispatch.dispatch_node` alone, and stays there: making `gate_review` run
    it is a feature, not this fix). A profile's list is invisible to that
    check: `profile:` is read live from `harnesses.yaml`
    (`kraft.adapters.profiles`, Kraft-ps1ao), not at template load, so a
    profile with no fallback list today can grow one tomorrow without the
    chain that selects it ever being re-validated. That is also why the
    refusal lives here, at the gate's actual launch, rather than in template
    lint or `kraft admin doctor`: both would read `harnesses.yaml` once and
    could go stale the moment an operator edits it, while a launch-time check
    is the one place it is always current.

    Reuses `executor.fallback.fallback_list`, the same function
    `dispatch.dispatch_node` walks -- it already knows the precedence (the
    task's own list wins, else its profile's) and already names which one it
    found.
    """
    if task.profile is None:
        return None
    try:
        # The item's pinned add-on versions: the profile the launch will use.
        table, _path = harness_table(_harness.load(None), pins, repo_id)
    except HarnessUnavailable:
        # The launch a moment later hits the identical problem and reports
        # it; nothing here needs to say it twice.
        return None
    entries, source = fallback_list(task, table)
    if not entries or source == "the task's list":
        # The task's own list is `GateNode._no_handler`'s refusal, already
        # caught before this chain could ever be materialized.
        return None
    return (
        f"gate {gate!r}'s auto_review task selects profile {task.profile!r}, whose "
        f"{source} is not empty; a gate review launches its reviewer once and never "
        "runs the fallback loop, so it is refused rather than silently ignored"
    )


async def review(
    db,
    run_dirs,
    *,
    work_item_id: str,
    gate: str,
    node: ResolvedNode,
    launch: executor.LaunchContext,
    tips: dict | None = None,
) -> tuple[str, str]:
    """Dispatch one gate review. Returns `(verdict, note)`.

    `tips` is the head the reviewer reads, at the final gate
    (`reviewed_head.pin_for`), recorded on its session: a person's approval
    that stops it pins only a head it did not move (`reviewed_head.moved_under`).

    `node` is the gate itself, and **the gate declares the reviewing task**:
    `GateNode.auto_review` is an ordinary `AnyTask`, resolved and launched like
    any other (`gate-auto-review-is-explicit-and-bounded`). This module used to
    hardwire `{"command": "claude", "skill": "gate-review"}` -- a reviewer
    declared nowhere, which no chain could change and no template could see.
    Only the *prompt* is Kraft's now, because the output contract
    (`verdict`/`concerns`) is what the caller applies.

    Never raises for an agent that misbehaved: every degraded outcome is
    `("undecided", note)`, which the caller turns into "leave the gate pending".
    Raises `store.CheckoutBusy`, having started nothing, while another
    in-place item uses the checkout, and, for a deciding reviewer,
    `inplace.CheckoutRefused` when the checkout cannot be switched to the
    item's branch. In place, a reviewer that checks first holds the checkout's
    lease but reads a throwaway copy of the branch (`inplace.copy`), never the
    person's checkout; a copy that cannot be made is `undecided`.
    """
    auto_review = node.auto_review
    if auto_review is None or not isinstance(auto_review.task, AgentTask):
        # Defence in depth, and unreachable from a validated chain:
        # `GateNode.auto_review` is typed `AgentTask | None`, so a non-agent
        # reviewer cannot be represented, and `auto_check_due` refuses an
        # *undeclared* one before this is called. Note what `auto_check_due` does
        # **not** do -- it tests `node.auto_review is None` and never the task's
        # kind, which is why this arm existed at all. It stays for a
        # `MaterializedChain` round-tripped out of a row an older build wrote,
        # which the new type cannot retroactively police.
        #
        # **The event is not optional.** `_gate_review_attempts` counts an
        # `gate_auto_review_skipped` that is not the tail of a
        # `gate_auto_review_started`, and this was the one early return that
        # wrote neither -- so the attempt tally stayed at zero, `auto_check_due`
        # stayed True, and the delay poller re-armed the same dead gate every
        # tick forever: a `max_concurrent` slot burned and a human's `retry`
        # 409'd, which is the exact failure `auto_check_due` exists to prevent.
        await db.write(
            lambda c: events.append(
                c,
                work_item_id,
                GateEvent.AUTO_REVIEW_SKIPPED,
                {"gate": gate, "reason": "unreviewable"},
                node_id=node.id,
            )
        )
        return "undecided", (f"gate {gate!r} declares no agent task to review it; a person decides")
    item = db.read(
        lambda c: c.execute(
            # `run_chain` too: a retried item's pins are the ones its launch reads.
            "SELECT materialized_chain, run_chain FROM work_items WHERE id = ?",
            (work_item_id,),
        ).fetchone()
    )
    if item is not None:
        # Before the profile table is read below: a pinned add-on's profile,
        # and its `fallback:`, are only there once its store is.
        await executor.restore_pins(item)
    pins = executor.frozen_steering(item)["addons"] if item is not None else None
    refusal = _profile_fallback_refusal(auto_review.task, gate, pins, launch.item_repo_id)
    if refusal is not None:
        # Same posture as the kind-check above: an unpaired skip, no
        # `_started`, so `_gate_review_attempts` still counts it as one
        # attempt and the delay poller does not re-arm a dead gate forever.
        await db.write(
            lambda c: events.append(
                c,
                work_item_id,
                GateEvent.AUTO_REVIEW_SKIPPED,
                {"gate": gate, "reason": "profile_fallback"},
                node_id=node.id,
            )
        )
        return "undecided", refusal
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    if row is None:
        raise LookupError(f"unknown work_item {work_item_id!r}")

    session_id = db.read(lambda c: store.new_session_id(c, work_item_id))
    rel = executor.gate_artifact(run_dirs, row, gate)
    doc = item_paths.path_of(run_dirs, work_item_id, rel) if rel is not None else None
    cf = checks_first(node)
    # What a checks-first review must leave as it was: the gate's document, or
    # where it is written while there is none yet, since the reviewer may create it.
    watched = rel or (
        item_paths.document_name(work_item_id, node.node.artifact)
        if cf and node.node.artifact
        else None
    )
    task_instruction = (_CHECKS_FIRST_PROMPT if cf else _PROMPT).format(
        gate=gate,
        node_id=node.id,
        title=row["title"],
        description_line=f"Brief: {row['description']}\n" if row["description"] else "",
        # Absolute: a new document is in the item's folder, not this worktree.
        artifact_line=_artifact_line(doc),
    )

    started = False

    async def start() -> None:
        # The attempt (`_gate_review_attempts`): written once the session holds
        # the checkout, or with the refused session that stands for it, never
        # for a review `CheckoutBusy` refused, which started nothing and waits.
        nonlocal started
        if not started:
            started = True
            await db.write(
                lambda c: events.append(
                    c,
                    work_item_id,
                    GateEvent.AUTO_REVIEW_STARTED,
                    {"gate": gate, "session_id": session_id},
                    node_id=node.id,
                )
            )

    try:
        # The item's sandbox, as for every launch of it (Ruling 189).
        sandbox = executor.item_sandbox(row, launch)
    except executor.SandboxUnresolved as exc:
        # Recorded as the reviewer's own session, so the pending gate names
        # why no review ran (Kraft-9t2dp); nothing launches.
        await start()
        await executor.config_error_session(
            db,
            run_dirs,
            dict(
                session_id=session_id,
                work_item_id=work_item_id,
                node_id=node.id,
                hook_point=auto_review.path,
                round=0,
                head_sha=None,
            ),
            f"{auto_review.path}: {exc}\n",
        )
        return "undecided", str(exc)
    # The gate's time cap bounds its review, as a node's does its tasks
    # (Kraft-8en38): spent refuses the launch; otherwise the one run is killed
    # at the deadline.
    hit = db.read(lambda c: caps.at_launch(c, row, auto_review))
    if hit is not None and hit.remaining_s <= 0:
        await start()
        await executor.time_capped_session(
            db,
            run_dirs,
            dict(
                session_id=session_id,
                work_item_id=work_item_id,
                node_id=node.id,
                hook_point=auto_review.path,
                round=0,
                head_sha=None,
            ),
            hit,
        )
        return "undecided", hit.reason
    time_cap = caps.Deadline(caps.monotonic() + hit.remaining_s, hit) if hit else None
    item_override = json.loads(row["agent_overrides"]) if row["agent_overrides"] else None
    # A model stored before the write doors checked model ids is held to the
    # same rule here, before it reaches the reviewer's command line.
    refusal = _overrides.stored_model_refusal(work_item_id, item_override)
    if refusal is not None:
        await start()
        await executor.config_error_session(
            db,
            run_dirs,
            dict(
                session_id=session_id,
                work_item_id=work_item_id,
                node_id=node.id,
                hook_point=auto_review.path,
                round=0,
                head_sha=None,
            ),
            f"{auto_review.path}: {refusal}\n",
        )
        return "undecided", refusal
    try:
        inv = _agent.resolve_agent_task(
            auto_review.task,
            launch.repo_entry,
            launch.library_steering,
            skills_dir=launch.skills_dir,
            # Item-wide overrides only -- an item marked cheap stays cheap for its
            # gate reviews too (Kraft-ui79: no per-gate floor). Deliberately *not*
            # the per-node `node_overrides` model/effort dial that
            # `dispatch.dispatch_node` also merges (Kraft-df4tc): a node dialed to
            # a different model does not carry that dial into its own gate review.
            item_override=item_override,
            # The reviewer's and the repository's steering, frozen at intake.
            **executor.frozen_steering(row),
            # The item's own repo as recorded at intake (Kraft-jzdyp): a gate
            # review runs in the item's own checkout, never a fanned-out member's.
            item_repo=row["repo"],
            # The gate scope's policy: its tool lists and sandbox, as for any task.
            policy=executor.scope_policy(row, auto_review),
            repo_id=launch.item_repo_id,
        )
    except _steering.SteeringError as exc:
        # Modelled on the `SandboxUnresolved` handler above: recorded as the
        # reviewer's own session, so the pending gate names why no review ran,
        # and `gate_auto_review_started` above is not left dangling with no
        # terminal event for `_gate_review_attempts` to count.
        await start()
        await executor.config_error_session(
            db,
            run_dirs,
            dict(
                session_id=session_id,
                work_item_id=work_item_id,
                node_id=node.id,
                hook_point=auto_review.path,
                round=0,
                head_sha=None,
            ),
            f"{auto_review.path}: {exc}\n",
        )
        return "undecided", str(exc)
    except Exception:
        await start()  # a disabled profile (`HarnessUnavailable`) is an attempt too
        raise
    # A reviewer that checks first must leave the work as it found it (spec
    # "If the reviewer edits the work"): read the checkout before, compare after.
    # The gate's document too, which is outside the checkout and git's sight.
    worktree = inplace.item_folder(run_dirs, row)
    in_place = store.is_in_place(row)
    # In place it reads a copy of the branch, never the person's checkout.
    copy = inplace.copy_path(run_dirs, work_item_id, session_id) if cf and in_place else None
    before = None
    doc_was = None
    if cf and not in_place:
        # Before the launch: once the reviewer's session row exists it counts
        # as live, and a sandboxed item's guard would refuse every read.
        before = read_only.snapshot(db, row, launch, worktree)
        doc_was = await asyncio.to_thread(_document, run_dirs, work_item_id, watched)
        if before is None:
            why = "Kraft could not read the checkout safely, so no reviewer ran: a person decides"
            await start()
            await executor.config_error_session(
                db,
                run_dirs,
                dict(
                    session_id=session_id,
                    work_item_id=work_item_id,
                    node_id=node.id,
                    hook_point=auto_review.path,
                    round=0,
                    head_sha=None,
                ),
                f"{auto_review.path}: {why}\n",
            )
            return "undecided", why

    async def after_checkout() -> None:
        # In place, once the copy is made: the copy whole, and the person's
        # branch tip, which a commit by any other route moves. Never sandboxed,
        # so the live-session guard cannot refuse here; a None is judged below.
        nonlocal before, doc_was
        before = inplace.watch_copy(db, row, launch, copy, worktree)
        doc_was = await asyncio.to_thread(_document, run_dirs, work_item_id, watched)

    try:
        status = await _agent.run_agent_task(
            db,
            run_dirs,
            session_id=session_id,
            work_item_id=work_item_id,
            node_id=node.id,
            hook_point=auto_review.path,
            command=inv.command,
            harness=inv.harness,
            model=inv.model,
            effort=inv.effort,
            # No extra tool denial for a reviewer that checks first: most
            # harnesses cannot hold one and would refuse the launch. The
            # before/after snapshot is what catches an edit.
            deny_tools=inv.deny_tools,
            allowed_tools=inv.allowed_tools,
            grants=inv.grants,
            permission_mode=inv.permission_mode,
            method_text=inv.method_text,
            steering_texts=inv.steering_texts,
            sandbox=sandbox,
            checkout=executor.sandbox_checkout(row, launch, worktree) if sandbox else None,
            task_instruction=task_instruction,
            title=row["title"],
            repo_path=str(copy) if copy else row["repo"],
            cwd=copy or worktree,
            repo_entry=launch.repo_entry,
            time_cap=time_cap,
            harness_id=auto_review.task.harness,
            takes_checkout=True,
            tips=tips,
            on_session=start,
            after_checkout=after_checkout if copy else None,
            in_copy=copy is not None,
            # Its instruction forbids edits: its context must not ask for commits.
            read_only=cf,
            runner=_placement.remote(_placement.runner_for(row, auto_review.runner, node.id)),
        )
    except store.CheckoutBusy:
        raise
    except Exception as exc:
        await start()  # an attempt all the same
        if isinstance(exc, inplace.CopyFailed):
            # Its session already ended `config_error`; never the person's checkout instead.
            return "undecided", str(exc)
        if not isinstance(exc, _agent.LaunchRefused):
            raise
        # Refused before anything started: recorded as the reviewer's own
        # session, so the pending gate names why no review ran.
        await executor.config_error_session(
            db,
            run_dirs,
            dict(
                session_id=session_id,
                work_item_id=work_item_id,
                node_id=node.id,
                hook_point=auto_review.path,
                round=0,
                head_sha=None,
            ),
            f"{auto_review.path}: {exc}\n",
        )
        return "undecided", str(exc)
    else:
        if cf:
            # Even a reject: its reasoning was about work it then changed. A remote
            # run's commits are already in this worktree (`remote.runs._publish`).
            doc_is = await asyncio.to_thread(_document, run_dirs, work_item_id, watched)
            # The document the person would now read: one the reviewer wrote where
            # an older item kept it, or in the new place over that, is an edit too.
            now = await asyncio.to_thread(executor.gate_artifact, run_dirs, row, gate)
            docs = {watched} if doc_is != doc_was else set()
            docs |= {now} if now not in (None, watched) else set()
            if before is None:
                # In place, a launch refused before its copy never read it:
                # that is a run that did not finish, judged below.
                if status in ADVANCING:
                    return "undecided", (
                        "Kraft could not read the checkout before the review, "
                        "so its verdict was ignored"
                    )
            else:
                written = [str(item_paths.path_of(run_dirs, work_item_id, d)) for d in sorted(docs)]
                if copy:
                    edited, moved = before()  # the whole copy, and the person's branch tip
                    notes = [
                        voided + read_only.named(files)
                        for voided, files in (
                            (_VOIDED_IN_COPY, edited + written),
                            (_TIP_MOVED, moved),
                        )
                        if files
                    ]
                else:
                    files = read_only.changed(db, row, launch, worktree, before) + written
                    notes = [_VOIDED + read_only.named(files)] if files else []
                if notes:
                    return "undecided", ". ".join(notes)
    finally:
        if copy is not None:
            await _subprocess.uncancellable(asyncio.to_thread(inplace.remove_copy, copy))
    result_path = item_paths.session_files(run_dirs, work_item_id, session_id).result
    note = _subprocess.read_concerns(result_path) or ""
    verdict = _subprocess.read_verdict(result_path)
    # A verdict counts only from a session that finished thinking (`vocab.ADVANCING`),
    # the same rule the fix-loop judge applies. One written beside any other status
    # (failed, capped_out, paused, unknown, ...) is not a judgement, whatever it says.
    if status not in ADVANCING:
        return "undecided", note
    if cf:
        # Only `reject` or `pass`; an `approve` meant "good", and the person
        # still decides. It may not edit, so it has nothing to have `fixed`.
        verdict = "pass" if verdict == "approve" else verdict
        if verdict not in ("reject", "pass"):
            return "undecided", note
    elif verdict not in VERDICTS:
        return "undecided", note
    if verdict in ("reject", "fixed") and not note.strip():
        # The note is the steering instruction for the re-run. Without one there
        # is nothing to send back with, so it goes to a human instead.
        return "undecided", note
    return verdict, note
