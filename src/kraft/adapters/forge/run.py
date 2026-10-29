"""Choosing a backend, and running one forge node against one or several
repos.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from kraft import builtins as _builtins
from kraft import events, inplace, item_paths, reviewed_head, store, waits
from kraft.adapters import beads
from kraft.adapters.forge import ci, git, settle
from kraft.adapters.forge import mr as mr_ops
from kraft.adapters.forge.gh import GhCli
from kraft.adapters.forge.glab import GlabCli
from kraft.adapters.forge.models import (
    MR,
    CIStatus,
    FailedJob,
    FakeForge,
    ForeignCommits,
    Forge,
    ForgeError,
    HeadMoved,
    MRRef,
    QueueRead,
)
from kraft.automated_review import Reviewers
from kraft.templates.environment import RootPointerPolicy
from kraft.templates.models import DEFAULT_GRACE, DEFAULT_WAIT, WaitBounds
from kraft.vocab import RUNNING, ForgeEvent
from kraft.vocab.sql import in_list

logger = logging.getLogger(__name__)

#: The empty metadata a node with no `on.mr.describe` artifact, or a run
#: before Task 4's node exists, falls back to -- one shared instance so this
#: stays a lint-clean default rather than a fresh call per signature.
_EMPTY_META = mr_ops.MRMeta()

#: Which internal handler each V1 `ForgeAction` runs. The vocabulary a chain
#: author writes (`target: mr.open_draft`) is the schema's; which code path it
#: reaches is this adapter's, so the mapping lives here rather than in the
#: executor -- `dispatch` hands over the typed target and nothing else.
V1_HANDLERS: dict[str, str] = {
    "mr.open_draft": "open_mr",
    "mr.sync": "sync_mr",
    "mr.ci": "ci_poll",
    "mr.automated_review": "automated_review",
    "mr.mark_ready": "mark_ready",
    "mr.external_approval": "external_approval",
    "mr.merge": "merge",
    "mr.post_merge_ci": "merge_watch",
}

#: The handlers that are external waits (`ForgeAction.waits`): each makes one
#: observation per dispatch, recorded by `kraft.waits.observe`, and never
#: sleeps in-process. Handler -> (its target, the condition it observes).
_WAITS: dict[str, tuple[str, str]] = {
    "ci_poll": ("mr.ci", "ci"),
    "automated_review": ("mr.automated_review", "automated_review"),
    "external_approval": ("mr.external_approval", "external_approval"),
    "merge": ("mr.merge", "merge"),
    "merge_watch": ("mr.post_merge_ci", "post_merge_ci"),
}

#: `_run_one`'s statuses for a pending observation, each naming what is still
#: awaited (`None`: the handler's own condition). Internal only, like
#: "rebased": `run_task` records the condition and reports plain `waiting`.
_PENDING: dict[str, str | None] = {
    "waiting": None,
    "ci_pending": "ci",
    "approval_pending": "external_approval",
    "merge_pending": "merge",
}


def handler_for(target: str) -> str:
    """The handler name for a V1 `ForgeAction` value, or the target itself when
    nothing maps it -- `_run_one` then stops on it by the name the chain
    actually wrote."""
    return V1_HANDLERS.get(target, target)


def resolve(name: str) -> Forge:
    """Named, never probed.

    Auto-detecting an available CLI would mean the same work item takes a
    different path on a laptop than in a container, and a bug that reproduces on
    one and not the other. A `glab` that is installed but unauthenticated also
    looks available and then fails deep inside a node.

    Every caller names the backend through `backend_for` from the repo's
    recorded `forge`; nothing pins one per template or registry.
    """
    match name:
        case "glab":
            return GlabCli()
        case "gh":
            return GhCli()
        case "fake":
            return _DEV_FAKE
        case _:
            raise ForgeError(f"unknown forge backend {name!r}; known: gh, glab, fake")


#: repos.yaml's `forge` (config._FORGES) -> the CLI that talks to it. Two
#: vocabularies on purpose: `forge` is a fact about the remote, the backend is
#: a fact about this machine, and a self-hosted GitLab is `gitlab` with `glab`.
#: `fake` is dev-only (Ruling 147): the in-process `FakeForge`, so `just dev`
#: reaches the merge-request half of a chain. It opens nothing anywhere.
_FORGE_CLI = {"gitlab": "glab", "github": "gh", "fake": "fake"}

#: The one `FakeForge` a `fake` repo gets, for the life of the process: every
#: forge node resolves afresh, and a per-call instance forgot the draft MR
#: `open_mr` made before `sync_mr`/`mark_ready`/`merge` could find it.
#: ponytail: in memory only, so a server restart forgets every fake MR --
#: persist it if a dev walk ever needs to span a restart.
_DEV_FAKE = FakeForge()


def backend_for(backend: str, repo_forge: str | None) -> str:
    """`auto` means the forge recorded for this repo; any other name is itself.

    The registry is per install and the forge is a property of the repo, so one
    backend name in one file cannot be right for two `kraft repo connect`s. This
    is the only place that gap is closed; `resolve` stays named and never sees
    `auto`.
    """
    if backend != "auto":
        return backend
    cli = _FORGE_CLI.get(repo_forge or "")
    if cli is None:
        raise ForgeError(
            "backend: auto, but no forge is recorded for this repo — set "
            "`forge: gitlab` or `forge: github` on it in Settings › Repos "
            "(or repos.yaml); `forge: fake` is a dev-only in-process forge "
            "that opens nothing"
        )
    return cli


async def _record_push(
    db, *, work_item_id: str, node_id: str | None, orig_repo: Path, repo: Path, branch: str
) -> None:
    """Record that Kraft has pushed `branch`, as `branch_pushed`, when its head
    is not the one the newest such event for `orig_repo` names. By head, not by
    what the forge reports: `git push` is a no-op on a branch that is up to
    date, and a `FakeForge` pushes nothing. The review wait counts a bot's
    activity from this moment (`waits.pushed_at`)."""
    sha = await git._head_sha(repo, branch)
    if not sha:
        return

    def _write(c) -> None:
        newest = c.execute(
            "SELECT json_extract(payload, '$.sha') AS sha FROM events "
            "WHERE work_item_id = ? AND type = ? AND json_extract(payload, '$.repo') = ? "
            "ORDER BY seq DESC LIMIT 1",
            (work_item_id, ForgeEvent.BRANCH_PUSHED, str(orig_repo)),
        ).fetchone()
        if newest is None or newest["sha"] != sha:
            events.append(
                c,
                work_item_id,
                ForgeEvent.BRANCH_PUSHED,
                {"node_id": node_id, "repo": str(orig_repo), "sha": sha},
            )

    await db.write(_write)


async def _rebase_conflict_away(
    db,
    forge: Forge,
    *,
    repo: Path,
    orig_repo: Path,
    branch: str,
    base: str,
    work_item_id: str,
    moved: list[Path] | None = None,
    run_dirs=None,
    node_id: str | None = None,
    #: The node a recorded push (`branch_pushed`) is for, when `node_id` is
    #: unset: only the merge handler's `node_id` changes how a refused push ends.
    push_node_id: str | None = None,
) -> tuple[str, str]:
    """Force-rebase onto `base`'s current tip -- the item's base branch, the one
    thing that can turn a real conflict into nothing left to fix, since a
    conflict against wherever main was when this branch was cut may not
    exist against main's current tip. Shared by `ci_poll` (Kraft-9h7v, the
    original) and `merge` (draft-MR workflow spec): same rebase, same
    "done, not conflict" report that lets the node's `on_base_changed`
    restart see the moved `base_ref` and re-run its declared span in the
    same walk, before either node ever calls `forge.merge`.

    Returns ("<why it didn't take>\\n", "conflict") unchanged on a rebase
    that could not resolve it (a real conflict, `git rebase --abort`ed, or
    nothing to rebase) -- the caller appends this to its own log and keeps
    its own status. Returns ("<what changed>\\n", "done") when it worked,
    with the branch already rebased, `base_ref` persisted, and pushed.

    `moved` is given for a workspace member: `base_ref` is the root's, so a
    member's move is appended there for `run_task` to report, never persisted
    (Kraft-puqxq).

    An in-place item (`run_dirs` given, not a member) is rebased through
    `inplace.rebase`; a checkout it could not take, or left clashing, is
    not a conflict either.

    Given `node_id` (the merge handler's), a push the forge refuses because it
    holds commits Kraft did not push is not a failure after a final review
    pinned the head: it returns `unreviewed`, with those commits brought home.
    """
    row = None
    if run_dirs is not None and moved is None:
        row = db.read(
            lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
        )
    # Kraft's own rebase, with no agent and nothing to resolve: a head the
    # final review approved stays the approved one, rebased (`repin`).
    before = reviewed_head.branch_tip(repo, branch)
    try:
        new_head = await _builtins.mr_rebase_forced(
            repo,
            orig_repo,
            branch,
            base,
            identity=_builtins.item_identity(db, work_item_id),
            run_dirs=run_dirs,
            row=row,
        )
    except (git.UnsafeWorktree, inplace.AutostashClash, inplace.CheckoutRefused):
        raise  # not a conflict: `run_task` stops the item for a person
    except RuntimeError as exc:
        return f"rebase onto {base} failed: {exc}\n", "conflict"
    if not new_head:
        return "", "conflict"
    after = reviewed_head.branch_tip(repo, branch)
    await reviewed_head.repin(
        db, work_item_id, {str(repo): before}, {str(repo): after}, cause="rebase"
    )
    if moved is None:
        await db.write(lambda c, h=new_head: store.set_base_ref(c, work_item_id, h))
    else:
        moved.append(repo)
    try:
        await forge.push(repo=repo, branch=branch)
    except ForeignCommits as exc:
        if node_id is None or not _pinned(db, work_item_id):
            raise
        held = await _refuse_forge_head(db, work_item_id, node_id, repo, branch, exc.remote)
        return f"rebased {branch} onto {new_head}, but not pushed: {held}", "unreviewed"
    await _record_push(
        db,
        work_item_id=work_item_id,
        node_id=node_id or push_node_id,
        orig_repo=orig_repo,
        repo=repo,
        branch=branch,
    )
    return f"rebased {branch} onto {new_head} and re-pushed\n", "done"


async def _refuse_unreviewed(
    db, work_item_id: str, node_id: str, repo: Path, branch: str
) -> str | None:
    """Why `merge` must not ask the forge to merge `branch` in `repo` -- its tip
    is not the head the final review approved (`reviewed_head.unreviewed`),
    recorded as `merge_refused_unreviewed` -- or None when it may. The walk
    then holds the item before that review again (`walk._stop_unreviewed`)."""
    found = reviewed_head.unreviewed(db, work_item_id, repo, branch)
    if found is None:
        return None
    await db.write(
        lambda c: events.append(
            c,
            work_item_id,
            ForgeEvent.MERGE_REFUSED_UNREVIEWED,
            {"node_id": node_id, **found, "source": "local"},
        )
    )
    current = found["current"][:12] if found["current"] else "a tip that cannot be read"
    reviewed = found["reviewed"][:12] if found["reviewed"] else "nothing"
    return (
        f"not merging {branch}: its tip in {repo} is {current}, but the final review "
        f"approved {reviewed}\n"
    )


def _pinned(db, work_item_id: str) -> bool:
    """Whether a final review pinned this item's head (`kraft.reviewed_head`):
    only then is a head the forge holds that Kraft did not push code no
    person reviewed, and sent back for review rather than failed."""
    return db.read(lambda c: reviewed_head.pinned(c, work_item_id)) is not None


async def _push_or_hold(
    forge: Forge, db, work_item_id: str, node_id: str, repo: Path, orig_repo: Path, branch: str
) -> str | None:
    """Push `branch` for the merge. None once it is pushed. When the forge holds
    commits Kraft did not push and a final review pinned the head (`git.push`
    refuses to overwrite them), the log of the hold that sends the item back for
    review (`_refuse_forge_head`); an item with no pin keeps the failure."""
    try:
        await forge.push(repo=repo, branch=branch)
    except ForeignCommits as exc:
        if not _pinned(db, work_item_id):
            raise
        return await _refuse_forge_head(db, work_item_id, node_id, repo, branch, exc.remote)
    await _record_push(
        db,
        work_item_id=work_item_id,
        node_id=node_id,
        orig_repo=orig_repo,
        repo=repo,
        branch=branch,
    )
    return None


def _moved_head(db, work_item_id: str, repo: Path, found: MRRef) -> tuple[str, str] | None:
    """`(approved, the forge's)` when a merge Kraft asked for is on a head other
    than the one the final review approved for `repo`; None when they agree, the
    forge did not say, or nothing is pinned."""
    reviewed = reviewed_head.reviewed_tip(db, work_item_id, repo)
    if reviewed and found.head_sha and found.head_sha != reviewed:
        return reviewed, found.head_sha
    return None


async def _landed_unreviewed(
    db, work_item_id: str, node_id: str, repo: Path, found: MRRef, requested: bool
) -> str | None:
    """The log of a stop when the merged request `found` is code the final
    review did not approve, else None. Two ways: a merge this wait asked for
    (`requested`) landed on a head other than the approved one -- recorded as
    `merge_landed_unreviewed`, which Kraft cannot undo -- or one already
    recorded for this merge request, which a plain retry must not clear: only
    `kraft item complete` or `skip` on the node ends it. A merge Kraft never
    asked for (a person's, on the forge) is not checked."""
    recorded = db.read(
        lambda c: c.execute(
            "SELECT payload FROM events WHERE work_item_id = ? AND type = ? "
            "AND json_extract(payload, '$.repo') = ? AND json_extract(payload, '$.number') = ? "
            "ORDER BY seq DESC LIMIT 1",
            (work_item_id, ForgeEvent.MERGE_LANDED_UNREVIEWED, str(repo), found.number),
        ).fetchone()
    )
    if recorded is not None:
        landed = json.loads(recorded["payload"])
    else:
        moved = _moved_head(db, work_item_id, repo, found) if requested else None
        if moved is None:
            return None
        landed = {
            "node_id": node_id,
            "repo": str(repo),
            "reviewed": moved[0],
            "merged_head": moved[1],
            "number": found.number,
        }
        await db.write(
            lambda c: events.append(c, work_item_id, ForgeEvent.MERGE_LANDED_UNREVIEWED, landed)
        )
    return (
        f"!{found.number} merged {landed['merged_head'][:12]}, not {landed['reviewed'][:12]}, "
        "the head the final review approved; the code is on the base branch\n"
    )


async def _refuse_forge_head(
    db, work_item_id: str, node_id: str, repo: Path, branch: str, current: str
) -> str:
    """The forge holds a head of `branch` that the final review did not approve
    (`current`, "" when unread): a commit that reached the merge request by
    some road other than Kraft's push, found by a refused push (`ForeignCommits`)
    or merge (`HeadMoved`). Records `merge_refused_unreviewed` (`source:
    "forge"`) and returns the log line; the caller then reports `unreviewed`,
    and the walk holds the item before the final review again.

    First the forge's commits are brought home (`reviewed_head.bring_home`), so
    that review shows them and approving it pushes nothing over them. The pin
    does not move: they are unreviewed until a person approves again."""
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    stopped = None
    try:
        fast_forwarded = await reviewed_head.bring_home(
            repo, branch, in_place=inplace.is_in_place(row)
        )
    except git.UnsafeWorktree as exc:
        # Recorded first, so the stop does not hide that the forge's head moved.
        fast_forwarded, stopped = False, exc
    reviewed = reviewed_head.reviewed_tip(db, work_item_id, repo)
    await db.write(
        lambda c: events.append(
            c,
            work_item_id,
            ForgeEvent.MERGE_REFUSED_UNREVIEWED,
            {
                "node_id": node_id,
                "repo": str(repo),
                "reviewed": reviewed,
                "current": current or None,
                "source": "forge",
                "fast_forwarded": fast_forwarded,
            },
        )
    )
    if stopped is not None:
        raise stopped
    held = f"it is now {current[:12]}" if current else "it holds commits"
    return (
        f"not merging {branch}: the forge's {branch} in {repo} changed after the final review "
        f"({held}, not {reviewed[:12] if reviewed else 'an approved head'})"
        + ("; brought into the item's branch\n" if fast_forwarded else "\n")
    )


async def _infra_budget_spent(db, work_item_id: str, node_id: str, reason: str) -> bool:
    """Spend one infra retry on `ci_infra:<node_id>`; True once the cap is
    breached, with `CI_INFRA_EXHAUSTED` recorded (the caller then returns
    `infra_stop`). Shared by `ci_poll`, `merge_watch` and a merge train or
    queue that dropped the merge request on its own runners: one counter per
    node, so each spends its own budget.

    `infra_stop` is a plain literal at every caller, matching
    `executor.context.INFRA_STOP` without importing that package here --
    `kraft.executor` imports `kraft.adapters.forge` (through `dispatch.py`),
    so the reverse import would cycle. `walk.py`'s node loop already turns it
    into `stops.stop_for_infra` -> `needs_human` for any node."""
    # Local imports: the policy and store helpers the counter needs.
    from kraft import policy as _policy
    from kraft.store import _now as _now

    count, started_at, cap = await db.write(
        lambda c: store.bump_counter(
            c,
            work_item_id,
            f"ci_infra:{node_id}",
            _policy.Cap(attempts=ci._INFRA_RETRY_CAP, wall_clock_s=ci._INFRA_WALL_CLOCK_S),
        )
    )
    if _policy.check(count=count, started_at=started_at, cap=cap, now=_now()) != "breached":
        return False
    await db.write(
        lambda c: events.append(
            c,
            work_item_id,
            ForgeEvent.CI_INFRA_EXHAUSTED,
            {"node_id": node_id, "reason": reason},
        )
    )
    return True


async def _merge_dropped(
    db,
    forge: Forge,
    *,
    repo: Path,
    orig_repo: Path,
    branch: str,
    base: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    queue: QueueRead,
    moved: list[Path] | None,
    run_dirs,
) -> tuple[str, str, list[dict] | None]:
    """The forge took the merge request off its merge train or queue after
    Kraft asked for it to merge: classify why, and say what `merge` reports.

    - Its own runners broke (`ci.is_infra_red`): ask it to queue the same
      head again on the `ci_infra:<node_id>` budget, `infra_stop` once spent.
      Asking again does not break Kraft-l98h6: the forge holds nothing now.
    - Taken off by a person, or for a reason this cannot read:
      `merge_rejected` with no finding, never re-queued and never rebased.
      Kraft never fights a person who took it off; the walk stops for one
      (`walk._unrepaired_drop`), no recovery answering it.
    - Gone for a reason the forge has not written yet (cause `other`, no
      reason): `merge_pending`, nothing recorded, read again at the next
      observation -- GitLab writes its system note, and GitHub its removal
      event or the pull request's merge, a moment after the entry goes. The
      merge wait's own timeout bounds it.
    - Red code, a conflict with the target, or never added at all: rebased
      onto the target first (a train fails against the target as it is now),
      then `merge_rejected` with one finding per failed job, for the node's
      merge-rejection recovery.

    Every drop is recorded as `merge_queue_dropped`, `cause` already
    classified (`infra` decided here, never by a backend)."""
    which = "queue" if queue.kind == "queue" else "train"
    cause = queue.cause or "other"
    if cause == "code" and queue.failure is not None and ci.is_infra_red(queue.failure):
        cause = "infra"
    if cause == "other" and not queue.reason:
        return (
            f"gone from the merge {which}, but the forge has recorded no reason for it yet; "
            "reading it again at the next poll\n",
            "merge_pending",
            None,
        )
    reason = queue.reason or "no reason given"
    log = f"dropped from the merge {which}: {reason}\n"
    before = await git._head_sha(repo, branch)
    if cause in ("code", "conflict", "not_added"):
        rebase_log, _ = await _rebase_conflict_away(
            db,
            forge,
            repo=repo,
            orig_repo=orig_repo,
            branch=branch,
            base=base,
            work_item_id=work_item_id,
            moved=moved,
            run_dirs=run_dirs,
        )
        log += rebase_log
    head_sha = await git._head_sha(repo, branch)
    # Whether the head actually moved: `_rebase_conflict_away` also answers
    # "done" for a branch that already held the target's tip, which changed
    # nothing a gate saw.
    rebased = head_sha != before
    failed_jobs = queue.failure.failed_jobs if queue.failure is not None else ()
    await db.write(
        lambda c: events.append(
            c,
            work_item_id,
            ForgeEvent.MERGE_QUEUE_DROPPED,
            {
                "node_id": node_id,
                "repo": str(orig_repo),
                "kind": queue.kind,
                "cause": cause,
                "reason": queue.reason,
                "url": queue.url,
                "failed_jobs": [j.name for j in failed_jobs],
                "head_sha": head_sha,
                "rebased": rebased,
            },
        )
    )
    if cause == "infra":
        if await _infra_budget_spent(db, work_item_id, node_id, log):
            return log, "infra_stop", None
        refused = await _refuse_unreviewed(db, work_item_id, node_id, repo, branch)
        if refused is not None:
            return log + refused, "unreviewed", None
        head = reviewed_head.branch_tip(repo, branch) or ""
        try:
            await forge.merge(repo=repo, branch=branch, mr=MR(number=0, url=""), head=head)
        except HeadMoved as exc:
            if not _pinned(db, work_item_id):
                raise
            held = await _refuse_forge_head(db, work_item_id, node_id, repo, branch, exc.current)
            return log + held, "unreviewed", None
        log += f"the {which}'s own runners failed; asked the forge to queue the same head again\n"
        return log, "merge_pending", None
    if cause == "other":
        return (
            f"dropped from the merge {which}: {reason}; Kraft does not re-queue a merge "
            "request that was taken off by hand or for a reason it cannot read\n",
            "merge_rejected",
            None,
        )
    if failed_jobs:
        findings = _job_findings(queue.failure, "\n".join(queue.failure.jobs), hook_point)
    else:
        findings = [{"severity": "important", "message": reason, "source_plugin": hook_point}]
    return log, "merge_rejected", findings


async def _skip_ci(db, work_item_id: str, node_id: str, hook_point: str, orig_repo: Path):
    """A CI wait on a repo with `ci_checks: false` (Kraft-9efnk.12): passes at
    once, before or after the merge, and records why."""
    await db.write(
        lambda c: events.append(
            c,
            work_item_id,
            ForgeEvent.CI_NOT_CONFIGURED,
            {"node_id": node_id, "task": hook_point, "repo": str(orig_repo)},
        )
    )
    return "no CI configured for this repo (ci_checks: false); not waiting\n", "done", None


#: How long the merge commit may have no CI run at all before `merge_watch`
#: takes it that its branch runs none (Kraft-rbjo3): a forge registers a
#: push's runs within seconds, so minutes of nothing means none are coming --
#: a workflow on `pull_request` only, say, merged into a non-default branch.
NO_POST_MERGE_CI_GRACE = timedelta(minutes=5)


def _no_run_past_grace(db, work_item_id: str, task: str) -> bool:
    """Whether `task`'s wait, or its watch of the commit it last moved onto,
    started `NO_POST_MERGE_CI_GRACE` ago or more. A first look has no wait
    yet, so it never is."""
    wait = db.read(lambda c: waits.open_wait(c, work_item_id, task))
    if wait is None:
        return False
    started = datetime.fromisoformat(
        (wait.last or {}).get("grace_from") or wait.started["started_at"]
    )
    return waits.now() - started >= NO_POST_MERGE_CI_GRACE


async def _no_post_merge_ci(
    db, work_item_id: str, node_id: str, hook_point: str, orig_repo: Path, base: str, sha: str
):
    """The post-merge wait on a merge commit no CI ever ran for: passes, and
    says it checked nothing -- never "CI passed"."""
    payload = {
        "node_id": node_id,
        "task": hook_point,
        "repo": str(orig_repo),
        "branch": base,
        "sha": sha,
    }
    await db.write(
        lambda c: events.append(c, work_item_id, ForgeEvent.POST_MERGE_CI_ABSENT, payload)
    )
    minutes = int(NO_POST_MERGE_CI_GRACE.total_seconds() // 60)
    return (
        f"no post-merge CI on {base}: no run started for {sha[:7]} in {minutes} minutes; "
        "not waiting, and nothing was checked\n",
        "done",
        None,
    )


@dataclass(frozen=True)
class WaitNote:
    """What the review wait's collect path decided that `waits.observe` has to
    record with its observation."""

    #: When to look again, because a bot's grace period ends then; None leaves
    #: it to the backoff.
    next_at: datetime | None
    #: Kept in the observation, and read back at the next check.
    extra: dict
    #: Every reviewer was settled by the wait's deadline, not by being quiet.
    settled_by_timeout: bool
    #: The instant the decision was made at.
    now: datetime


async def _collect_review(
    forge: Forge,
    db,
    *,
    repo: Path,
    orig_repo: Path,
    branch: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    reviewers: Reviewers,
    bounds: WaitBounds,
    grace: timedelta,
    run_dirs,
    session_id: str,
    round: int,
) -> tuple[str, str, WaitNote | None]:
    """One check of the review wait on its collect path: read every reviewer,
    decide whether each has finished with the head (`settle.decide`), and once
    all have, write what they said for the task that reads it
    (`bot_feedback.json`). Pending is `waiting`; settled is `done` whatever the
    reviewers said -- the feedback is that task's to judge."""
    folder = item_paths.session_files(run_dirs, work_item_id, session_id).folder
    destination = folder / "bot_feedback.json"
    head = await git._head_sha(repo, branch)
    if not reviewers:
        await db.write(
            lambda c: events.append(
                c,
                work_item_id,
                ForgeEvent.AUTOMATED_REVIEW_NOT_CONFIGURED,
                {"node_id": node_id, "task": hook_point, "repo": str(orig_repo)},
            )
        )
        _write_collection(destination, head, round, [])
        return "automated review: no reviewer configured; nothing to collect\n", "done", None
    log, status, note, collection = await _settle_review(
        forge, db, repo=repo, orig_repo=orig_repo, branch=branch, head=head,
        work_item_id=work_item_id, hook_point=hook_point, reviewers=reviewers,
        bounds=bounds, grace=grace,
    )  # fmt: skip
    if collection is not None:
        _write_collection(destination, head, round, collection)
    return log, status, note


async def _settle_review(
    forge: Forge,
    db,
    *,
    repo: Path,
    orig_repo: Path,
    branch: str,
    head: str,
    work_item_id: str,
    hook_point: str,
    reviewers: Reviewers,
    bounds: WaitBounds,
    grace: timedelta,
    scope: str | None = None,
) -> tuple[str, str, WaitNote, list[dict] | None]:
    """One check of whether every reviewer has finished with `head`
    (`settle.decide`): `waiting` with no collection yet, or `done` with what
    each reviewer said. A multi-repo item's repositories each keep their own
    memory in the wait, under `scope`."""
    # One reading of the clock, so the deadline this tests and the one
    # `waits.observe` tests are the same instant.
    now = waits.now()
    wait = db.read(lambda c: waits.open_wait(c, work_item_id, hook_point))
    deadline = (
        datetime.fromisoformat(wait.started["started_at"]) + bounds.timeout
        if wait is not None
        else now + bounds.timeout
    )
    timed_out = now >= deadline
    if not head:
        raise ForgeError(f"cannot read the head of {branch} in {repo}")
    since = db.read(lambda c: waits.pushed_at(c, work_item_id, repo=str(orig_repo), sha=head))
    if since is None:
        since = await git.committed_at(repo, head)
    feedback = await forge.reviewer_feedback(
        repo=repo, branch=branch, reviewers=reviewers, since=since
    )
    memory = ((wait.last if wait is not None else None) or {}).get("memory", {})
    if scope is not None:
        memory = memory.get(scope, {})
    decided = settle.decide(
        feedback, since=since, now=now, grace=grace, memory=memory, timed_out=timed_out
    )
    if decided.state == "pending":
        log = f"automated review: waiting for the reviewers to finish with {head[:7]}\n"
        note = WaitNote(decided.next_at, {"memory": decided.memory}, False, now)
        return log, "waiting", note, None
    log = f"automated review: {len(reviewers)} reviewer(s) finished with {head[:7]}\n"
    note = WaitNote(None, {"memory": decided.memory}, timed_out, now)
    return log, "done", note, decided.collection


def _verdict(collection: list[dict]) -> tuple[list[str], list[str], list[str]]:
    """What a settled review asks to change, with nothing in the node to read
    it: each bot's unresolved threads, its newest review when that requests
    changes and left no thread, and a check that finished without passing.
    Also the reviewers whose summaries went unread (only an agent can tell
    what one says), and the checks the deadline settled unfinished."""
    findings: list[str] = []
    unread: list[str] = []
    unfinished: list[str] = []
    for entry in collection:
        for t in entry["threads"]:
            where = ":".join(str(x) for x in (t.get("file"), t.get("line")) if x)
            findings.append(f"{where}: {t['body']}" if where else t["body"])
        for s in entry["summaries"]:
            if s.get("state") == "CHANGES_REQUESTED":
                if not entry["threads"]:
                    findings.append(s["body"] or f"{entry['reviewer']} requested changes")
            elif s["body"].strip():
                unread.append(entry["reviewer"])
        check = entry["check"]
        if check is not None and check.get("finished"):
            findings.append(f"{check['name']} {check['conclusion']}: {check.get('output') or ''}")
        elif check is not None:
            unfinished.append(check["name"])
    return findings, sorted(set(unread)), unfinished


def _as_findings(messages: Iterable[str]) -> list[dict]:
    """An automated review's findings, as the node's recovery reads them."""
    return [
        {"severity": "important", "message": m, "source_plugin": "mr.automated_review"}
        for m in messages
    ]


def _write_collection(destination: Path, head: str, round: int, collection: list[dict]) -> None:
    """The collection the reading task is handed (`AgentInput.BOT_FEEDBACK`)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps({"head": head, "round": round, "reviewers": collection}, indent=2) + "\n"
    )


async def _run_one(
    forge: Forge,
    db,
    *,
    repo: Path,
    orig_repo: Path,
    branch: str,
    #: The branch `repo`'s merge request targets (`builtins.base_branch`):
    #: the item's base branch for its own repository or a workspace root, a
    #: member's own default branch for a member.
    base: str,
    title: str,
    work_item_id: str,
    node_id: str,
    handler: str,
    hook_point: str,
    meta: mr_ops.MRMeta = _EMPTY_META,
    has_rebase_bounce: bool = False,
    reviewers: Reviewers = (),
    #: The repository's `ci_checks:`; False skips both CI waits.
    ci_checks: bool = True,
    merge_requested: bool = False,
    #: This repo's `work_item_repos` row, for a multi-repo item: `open_mr`
    #: records the merge request it opened or reused there (Kraft-mjsf).
    repo_row_id: int | None = None,
    #: A workspace member's list for `_rebase_conflict_away`; None otherwise.
    moved: list[Path] | None = None,
    #: For `_rebase_conflict_away`'s in-place rebase.
    run_dirs=None,
    #: `run_task`'s, for `open_mr`.
    check_clean: bool = True,
    #: The review wait's collect path (`run_task`'s `reads_feedback` and not a
    #: multi-repo item), with the grace period, wait bounds, session and round
    #: it records its collection under.
    collect: bool = False,
    #: A review wait that does not collect (nothing in the node reads it, or a
    #: multi-repo item): a bot reviewer is still waited for until it has
    #: finished with the head, as on the collect path, and Kraft reads the
    #: verdict itself.
    settle_bots: bool = False,
    #: Where a multi-repo item keeps this repository's review memory.
    scope: str | None = None,
    grace: timedelta = DEFAULT_GRACE,
    bounds: WaitBounds = DEFAULT_WAIT,
    session_id: str = "",
    round: int = 0,
) -> tuple[str, str, list[dict] | None, WaitNote | None]:
    """One forge handler against one repo. Extracted from `run_task` so the
    multi-repo loop there can call it once per `work_item_repos` row; a
    single-repo item's behaviour is unchanged -- one call, same log and status
    strings as before this split.

    The third element is None for every handler but a code-red `ci_poll`: one
    finding per failed job, for the fix-loop plumbing `on.ci.poll` now feeds
    (Kraft-cbr §3). The fourth is None for every handler but a review wait
    that settles on its bots (`collect` or `settle_bots`): what it decided
    about when to look again and what to remember (`WaitNote`).

    `meta` is resolved once by the caller against the item's own worktree,
    not `repo` -- a multi-repo item's submodule targets are not where the
    agent's `on.mr.describe` artifact lives.

    `has_rebase_bounce` is whether *this* node declares `on_base_changed`
    -- read from the frozen chain, not from whatever the library says
    today. Both conflict rebases rely on it: without a base-change restart
    nothing will ever re-verify the rebased head, so `merge` reads its
    pipeline again before merging (code-review) and `ci_poll` waits for it
    rather than reporting the check green (Kraft-tx0dz).

    `merge_requested` is whether the merge wait's last observation already
    asked for *this* repo's merge (`run_task` works out which repo that was).
    """
    findings: list[dict] | None = None
    note: WaitNote | None = None
    body = mr_ops.mr_body(work_item_id, branch, await git.commits_on(repo, branch, base), meta)
    # Kraft-vz8e: an MR with no commits runs no CI, so `mr_checks` would wait
    # out its cap on a pipeline that never starts. Refused before it is
    # opened or readied, as a `config_error`: a fix loop has nothing to fix.
    # Read after the "already merged" shortcuts, which a merged branch --
    # empty against its base -- must still take.
    verb = "open" if handler == "open_mr" else "ready"
    empty = (
        f"refusing to {verb} a merge request for {branch}: it has no commits beyond "
        f"origin/{base} -- the implementation committed nothing\n"
    )
    match handler:
        case "open_mr":
            if await git.commits_ahead(repo, branch, base) == 0:
                return empty, "config_error", findings, None
            # Ask first: a rejected review re-entering the chain at
            # implementation walks back through this node, and a retry of
            # an open_mr that crashed after the create lands here too. A
            # second `mr create` for one branch is an error on both CLIs
            # (Kraft-xron).
            existing = await forge.find_mr(repo=repo, branch=branch)
            if existing is not None and existing.state == "open":
                # The same two calls `sync_mr` makes, for the same reason:
                # the description has to describe the head a reviewer will
                # see, and the head has to be pushed. Retitling an existing
                # MR is not something either CLI does in this call, so the
                # reused path keeps today's title.
                await forge.push(repo=repo, branch=branch)
                await _record_push(
                    db,
                    work_item_id=work_item_id,
                    node_id=node_id,
                    orig_repo=orig_repo,
                    repo=repo,
                    branch=branch,
                )
                await forge.update_mr(repo=repo, branch=branch, body=body)
                number, url = existing.number, existing.url
                log, status = f"reusing !{number}: {url}\n", "done"
            else:
                # ponytail: the description predates any commit `mr_checks`'
                # fix loop adds -- `sync_mr` rebuilds the body from this same
                # artifact, not from the newer head. Re-run on.mr.describe
                # from the fix loop if CI-fix commits start changing the
                # story.
                mr = await forge.open_mr(
                    repo=repo,
                    branch=branch,
                    base=base,
                    title=mr_ops.mr_title_for(title, meta),
                    body=body,
                    meta=meta,
                    check_clean=check_clean,
                )
                # `open_mr` pushes the branch before it creates the request.
                await _record_push(
                    db,
                    work_item_id=work_item_id,
                    node_id=node_id,
                    orig_repo=orig_repo,
                    repo=repo,
                    branch=branch,
                )
                number, url = mr.number, mr.url
                log, status = f"opened {url}\n", "done"

            # An event, not a work-item column (Kraft-d2sq): no migration,
            # it reaches the UI through the stream that already exists, and
            # it is timestamped, which a column is not. Both paths emit it,
            # so an item whose open_mr is re-entered after a rejected
            # review still carries a current record.
            # A multi-repo item's event carries no repo, so each repo's own
            # merge request is recorded on its row too (Kraft-mjsf).
            def _record(c, n=number, u=url):
                events.append(
                    c, work_item_id, ForgeEvent.MR_OPENED, {"number": n, "url": u}, node_id=node_id
                )
                if repo_row_id is not None:
                    store.update_repo_state(
                        c, repo_row_id, merge_state="open", mr_ref={"number": n, "url": u}
                    )

            await db.write(_record)
        case "ci_poll":
            # An MR merged outside Kraft -- a human merging by hand, or an
            # auto-merge racing this node's own poll -- usually has its
            # source branch deleted with it (`force_remove_source_branch`),
            # and the push below then dies `! [rejected] ... (stale info)`
            # against a remote ref that no longer exists (Kraft-7itv): the
            # lease expects the sha Kraft last pushed, and origin has no ref
            # at all to compare it to. Same shortcut `sync_mr` and
            # `merge` already take for the identical race -- nothing here is
            # left to check once the forge itself says merged.
            existing = await forge.find_mr(repo=repo, branch=branch)
            if existing is not None and existing.state == "merged":
                return (
                    f"already merged (!{existing.number}); nothing to check\n",
                    "done",
                    findings,
                    None,
                )
            # The worker commits in the worktree and is told not to push
            # (adapters/agent.py:45), and only open_mr and sync_mr ever
            # pushed — sync_mr *after* human review. Without this, a commit
            # made after open_mr is local, so the pipeline polled below is
            # the previous head's: the same id on every retry, green or
            # red, forever (Kraft-bxj8). `git push` is a no-op when the
            # branch is up to date, so this costs one git call.
            await forge.push(repo=repo, branch=branch)
            await _record_push(
                db,
                work_item_id=work_item_id,
                node_id=node_id,
                orig_repo=orig_repo,
                repo=repo,
                branch=branch,
            )
            if not ci_checks:
                return (*await _skip_ci(db, work_item_id, node_id, hook_point, orig_repo), None)
            # One check, not a wait. A pipeline that has not settled hands the
            # wait back to the scheduler (Kraft-ru98, `kraft.waits`); a
            # coroutine that sat here held an intake slot and could not be
            # cancelled.
            head_sha = await git._head_sha(repo, branch)
            # Pin to the pipeline the last poll of this same head already
            # saw, instead of re-resolving "latest on branch" every re-entry
            # (Kraft-ivh1). A rebase or a repair's push moves head_sha, which
            # invalidates the stored ref on its own -- no separate signal
            # for "something changed the branch" needed.
            stored = db.read(
                lambda c: c.execute(
                    "SELECT ci_pipeline_ref FROM work_items WHERE id = ?", (work_item_id,)
                ).fetchone()
            )
            stored_ref = stored["ci_pipeline_ref"] if stored else None
            pipeline_id = ""
            if stored_ref and ":" in stored_ref:
                stored_sha, stored_pipeline_id = stored_ref.split(":", 1)
                if stored_sha == head_sha:
                    pipeline_id = stored_pipeline_id
            ci_status = await forge.ci_status(
                repo=repo, mr=MR(number=0, url=""), branch=branch, pipeline_id=pipeline_id
            )
            if ci_status.pipeline_ref:
                await db.write(
                    lambda c, ref=f"{head_sha}:{ci_status.pipeline_ref}": store.set_ci_pipeline_ref(
                        c, work_item_id, ref
                    )
                )
            log, status = await ci.render_ci(
                ci_status, forge=forge, repo=repo, branch=branch, head_sha=head_sha
            )
            if status == "abandoned":
                status = await _stop_for_abandoned_run(db, work_item_id, node_id, log)
            if status == "infra":
                # `forge.retry_jobs` only *starts* the job again; the pipeline
                # is not settled the instant it returns. See `ci.retry_infra_
                # once`'s docstring for why the retry budget is a persisted
                # counter, bumped once per `ci_poll` entry, rather than a loop
                # inside this one call.
                if await _infra_budget_spent(db, work_item_id, node_id, log):
                    status = "infra_stop"
                else:
                    log, status = await ci.retry_infra_once(
                        forge, repo=repo, branch=branch, head_sha=head_sha, first=ci_status
                    )
                    # `retry_infra_once`'s re-read is almost always "waiting"
                    # (a kick only starts the job, it does not settle it), but
                    # an instantly-red re-read is not impossible -- "infra" is
                    # not a sentinel `dispatch.measure_node` recognises, and
                    # would otherwise fall through neither "failed" nor "ok"
                    # and silently read as a clean node. The counter above
                    # already recorded this attempt; the next separate
                    # ci_poll entry re-checks it against the cap, so parking
                    # as "waiting" here costs nothing.
                    if status == "infra":
                        status = "waiting"
            if status == "conflict":
                rebase_log, status = await _rebase_conflict_away(
                    db,
                    forge,
                    repo=repo,
                    orig_repo=orig_repo,
                    branch=branch,
                    base=base,
                    work_item_id=work_item_id,
                    moved=moved,
                    run_dirs=run_dirs,
                    push_node_id=node_id,
                )
                log += rebase_log
                if status == "done" and not has_rebase_bounce:
                    # No base-change restart will check the rebased head, and
                    # no pipeline for it has been read: wait for its own, as
                    # `merge` does below (Kraft-tx0dz).
                    log += "waiting for the rebased head's pipeline\n"
                    status = "waiting"
            if status == "failed" and ci_status.failed_jobs:
                findings = _job_findings(ci_status, log, hook_point)
        case "automated_review" if collect:
            log, status, note = await _collect_review(
                forge,
                db,
                repo=repo,
                orig_repo=orig_repo,
                branch=branch,
                work_item_id=work_item_id,
                node_id=node_id,
                hook_point=hook_point,
                reviewers=reviewers,
                bounds=bounds,
                grace=grace,
                run_dirs=run_dirs,
                session_id=session_id,
                round=round,
            )
        case "automated_review" if settle_bots and any(r.bot is not None for r in reviewers):
            head = await git._head_sha(repo, branch)
            log, status, note, collection = await _settle_review(
                forge, db, repo=repo, orig_repo=orig_repo, branch=branch, head=head,
                work_item_id=work_item_id, hook_point=hook_point, reviewers=reviewers,
                bounds=bounds, grace=grace, scope=scope,
            )  # fmt: skip
            if collection is not None:
                found, unread, unfinished = _verdict(collection)
                log += "".join(f"  {f}\n" for f in found)
                if unread:
                    log += (
                        f"not read: the summary from {', '.join(unread)}; nothing in this node "
                        "reads bot_feedback (see `kraft admin doctor`, triage_review_feedback)\n"
                    )
                if found:
                    status, findings = "failed", _as_findings(found)
                elif unfinished:
                    # A check is never skipped: its timeout stops for a person,
                    # as it does with no bot beside it.
                    log += f"not finished by the deadline: {', '.join(unfinished)}\n"
                    status = "waiting"
        case "automated_review":
            # One read. Which reviewer, which webhook or check, is the
            # backend's (`automated-review-implementation-is-not-template-
            # configuration`); what comes back is only pending, clean,
            # actionable or error (`automated-review-task-uses-ordinary-task-
            # results`).
            review = await forge.automated_review(repo=repo, branch=branch, reviewers=reviewers)
            if not review.configured:
                # No layer names a reviewer, so none is expected: the
                # wait settles, and the record says why rather than reading as
                # a clean review (Ruling 171).
                await db.write(
                    lambda c: events.append(
                        c,
                        work_item_id,
                        ForgeEvent.AUTOMATED_REVIEW_NOT_CONFIGURED,
                        {"node_id": node_id, "task": hook_point, "repo": str(orig_repo)},
                    )
                )
            log = f"automated review {review.state}" + (
                f": {review.detail}\n" if review.detail else "\n"
            )
            log += "".join(f"  {f}\n" for f in review.findings)
            if review.state == "pending":
                status = "waiting"
            elif review.state == "clean":
                status = "done"
            elif review.state == "actionable":
                # Actionable feedback is a failed task *with findings*, which is
                # what enters the node's recovery and fix loop
                # (`post-draft-feedback-uses-node-recovery-controls`).
                status, findings = "failed", _as_findings(review.findings)
            else:
                # The reviewer itself errored. That says nothing about the
                # code, so it must not spend a repair or a fix cycle (Ruling
                # 170, Kraft-sm2r2, 7a's "only a genuine repair outcome spends
                # an attempt"): the same stop as a pipeline broken by the
                # forge's own infrastructure, under its own named cause.
                reason = f"the automated reviewer errored on {hook_point}: " + (
                    review.detail or "no detail given"
                )
                await db.write(
                    lambda c, r=reason: events.append(
                        c,
                        work_item_id,
                        ForgeEvent.AUTOMATED_REVIEW_ERRORED,
                        {"node_id": node_id, "reason": r},
                    )
                )
                status = "infra_stop"
        case "external_approval":
            # A missing approval is an ordinary pending state, never a failure
            # (`missing-external-approval-is-normal-pending-state`).
            if await forge.approval_state(repo=repo, branch=branch) == "pending":
                log, status = "waiting for the merge request's required approval\n", "waiting"
            else:
                log, status = "the merge request is approved\n", "done"
        case "sync_mr":
            # An MR merged outside Kraft -- a human merging by hand and
            # skipping `human_review` -- usually has its source branch
            # deleted with it, and then this node's push dies `! [rejected]
            # ... (stale info)`: the lease expects the sha Kraft last pushed,
            # origin has no ref at all, and the item stops one node from
            # the end with the work already on main (Kraft-7itv). Same
            # shortcut `merge` takes, for the same reason: pushing to and
            # re-describing a merged MR has nothing left to accomplish.
            existing = await forge.find_mr(repo=repo, branch=branch)
            if existing is not None and existing.state == "merged":
                return (
                    f"already merged (!{existing.number}); nothing to sync\n",
                    "done",
                    findings,
                    None,
                )
            if await git.commits_ahead(repo, branch, base) == 0:
                return empty, "config_error", findings, None
            # Stays a draft: publication is `mark_ready`'s, after the chain's final
            # gate. sync also runs in on_failure and fix_loop, i.e. before that
            # gate, so undrafting here published the MR mid-repair.
            # Push first: every commit after `open_mr` -- verify's fixes,
            # mr_checks' findings -- is local only until this runs, and
            # `merge` refuses a branch ahead of its remote (Kraft-nh5m).
            await forge.push(repo=repo, branch=branch)
            await _record_push(
                db,
                work_item_id=work_item_id,
                node_id=node_id,
                orig_repo=orig_repo,
                repo=repo,
                branch=branch,
            )
            # The description `open_mr` wrote predates every commit verify
            # and mr_checks added, so it is rewritten from the branch head
            # before a human is asked to read it (Kraft-c09h).
            try:
                live_body: str | None = await forge.mr_body(repo=repo, branch=branch)
            except ForgeError:
                live_body = None  # shortcut: best-effort read, the sync itself goes on
            await forge.update_mr(repo=repo, branch=branch, body=body)
            log, status = "pushed and synced the merge request description\n", "done"
            # Editing a description triggers no workflow, so a check that read
            # the old one (a required section, say) stays red until its jobs
            # run again. Only when the body changed AND the current head's
            # checks are red: a sync after new commits sees a pending pipeline.
            # shortcut: re-runs whenever the live description differs, including
            # edits by a bot or a person, not only a repair; key it on the body
            # Kraft last wrote if that costs CI.
            if live_body is not None and live_body.strip() != body.strip():
                try:
                    head_ci = await forge.ci_status(
                        repo=repo, mr=MR(number=0, url=""), branch=branch
                    )
                    head_sha = await git._head_sha(repo, branch)
                    if head_ci.state == "failed" and head_ci.sha in ("", head_sha):
                        if await forge.rerun_after_description_change(
                            repo=repo, branch=branch, ci=head_ci
                        ):
                            # A new pipeline: the CI wait pins `head:pipeline`,
                            # and would keep reading the old one.
                            await db.write(lambda c: store.set_ci_pipeline_ref(c, work_item_id, ""))
                        log += "description changed: re-ran the failed jobs\n"
                except ForgeError:
                    pass  # shortcut: best-effort re-run, the CI wait still reports the truth
        case "mark_ready":
            # `mr.mark_ready` in V1: publication, split out of `sync_mr`'s
            # push-and-describe so a chain can put its final human gate between
            # the two. Not an external wait -- both `gh` and `glab` undraft in
            # one call -- so it needs no scheduler and no persisted condition.
            #
            # Idempotent on both CLIs (their own docs: marking a ready MR ready
            # is a no-op), which is what makes a re-entered walk safe here. An
            # MR already merged has nothing left to publish: same shortcut
            # `sync_mr` and `merge` take, for the same reason -- its source
            # branch is usually deleted with the merge, and the node's stated
            # end state is already true.
            existing = await forge.find_mr(repo=repo, branch=branch)
            if existing is not None and existing.state == "merged":
                return (
                    f"already merged (!{existing.number}); nothing to mark ready\n",
                    "done",
                    findings,
                    None,
                )
            if await git.commits_ahead(repo, branch, base) == 0:
                return empty, "config_error", findings, None
            await forge.mark_ready(
                repo=repo,
                branch=branch,
                mr=MR(number=existing.number if existing else 0, url=""),
            )
            log, status = "marked the merge request ready for review\n", "done"
        case "merge":
            # "Someone merged it first" and "the merge was refused" were
            # indistinguishable while this only ever shelled out: both
            # arrived as a non-zero exit and stopped the item at the last
            # node with the work already on main (Kraft-xron). Auto-merge
            # reaching the branch first is an ordinary event, and this
            # node's stated end state is already true.
            #
            # Every wait in here is one observation handed back to the
            # scheduler, never a sleep (Kraft-7jja): the pipeline a late push
            # re-armed, a missing approval, and the merge landing.
            existing = await forge.find_mr(repo=repo, branch=branch)
            # Asked for already: by this wait's own last observation, or --
            # read off the forge, so a retry, run fork or base-change restart
            # that ended the wait does not forget it -- a merge the forge
            # still holds queued (Kraft-l98h6). Every merge request goes
            # through here, so a merge request is asked to merge at most once
            # per head: a new head the forge dropped the queue for is asked
            # again.
            requested = merge_requested or (
                existing is not None and existing.state == "open" and existing.merge_queued
            )
            if existing is not None and existing.state == "merged":
                unseen = await _landed_unreviewed(
                    db, work_item_id, node_id, repo, existing, requested
                )
                if unseen is not None:
                    log, status = unseen, "landed_unreviewed"
                else:
                    log = f"already merged (!{existing.number}); nothing to do\n"
                    status = "done"
            elif (existing is None or existing.state != "open") and requested:
                gone = existing.state if existing else "gone"
                log = f"the merge request is {gone}, not merged; nothing landed\n"
                status = "failed"
            elif existing is None or existing.state != "open":
                raise ForgeError(
                    f"no open merge request for {branch!r}"
                    + (f": !{existing.number} is {existing.state}" if existing else "")
                )
            elif requested and (moved := _moved_head(db, work_item_id, repo, existing)):
                # Asked, and since then the forge's head is not the approved one: a
                # commit reached the merge request, which the forge would merge
                # when its checks pass. Cancelled while it still can be (a failed
                # cancel fails the node: the forge may still merge), then held.
                await forge.cancel_merge(repo=repo, branch=branch, mr=existing)
                log = f"cancelled the merge of !{existing.number}, now at {moved[1][:12]}\n"
                log += await _refuse_forge_head(db, work_item_id, node_id, repo, branch, moved[1])
                status = "unreviewed"
            elif requested:
                # This wait already asked for the merge; it is only reading
                # whether it has landed. Asking again would merge twice on a
                # forge that queues merges.
                #
                # Unless the forge let go of it: asked, still open, and no
                # longer held for merging is what both forges show once a
                # merge train or queue drops a merge request. Only then is the
                # train or queue read for why -- the cheap signal first.
                queue, unread = None, ""
                if not existing.merge_queued:
                    try:
                        queue = await forge.merge_queue(repo=repo, branch=branch, base=base)
                    except ForgeError as exc:
                        if isinstance(exc, git.UnsafeWorktree):
                            raise
                        # A read that failed (a GraphQL 5xx, a rate limit, a
                        # GHES with no merge queue) says nothing about the
                        # merge: keep waiting, as before this read existed.
                        unread = f"could not read the merge train or queue: {exc}\n"
                if queue is not None and queue.state == "dropped":
                    dropped = await _merge_dropped(
                        db,
                        forge,
                        repo=repo,
                        orig_repo=orig_repo,
                        branch=branch,
                        base=base,
                        work_item_id=work_item_id,
                        node_id=node_id,
                        hook_point=hook_point,
                        queue=queue,
                        moved=moved,
                        run_dirs=run_dirs,
                    )
                    return (*dropped, None)
                where = (
                    f" (merge {queue.kind}: {queue.state})"
                    if queue is not None and queue.kind != "none"
                    else ""
                )
                log = f"!{existing.number} is still open; waiting for the merge to land{where}\n"
                log += unread
                status = "merge_pending"
            elif (
                refused := await _refuse_unreviewed(db, work_item_id, node_id, repo, branch)
            ) is not None:
                # A head the final review did not approve is never pushed for
                # merging, let alone merged (`kraft.reviewed_head`).
                log, status = refused, "unreviewed"
            elif (
                held := await _push_or_hold(
                    forge, db, work_item_id, node_id, repo, orig_repo, branch
                )
            ) is not None:
                # The forge holds commits Kraft did not push, which the final
                # review did not see: brought home, and held for another look.
                log, status = held, "unreviewed"
            else:
                # The same push ci_poll makes, for the same reason: a
                # commit made after the last sync is local only, and
                # `_assert_pushed` inside forge.merge refuses a head origin
                # has never seen (Kraft-bxj8). Pushed just above.
                # A push after the last CI read -- the review brief, anything a
                # human committed while reviewing -- re-arms a required
                # pipeline for a head nothing has seen finish (Kraft-266b,
                # Kraft-x10m). Read it before handing forge.merge() that head.
                head_sha = await git._head_sha(repo, branch)
                ci_status = await forge.ci_status(repo=repo, mr=MR(number=0, url=""), branch=branch)
                log, gate_status = await ci.render_ci(
                    ci_status, forge=forge, repo=repo, branch=branch, head_sha=head_sha
                )
                rebased = False
                if gate_status == "conflict":
                    # A conflict only merge ever sees gets the same rebase
                    # mr_checks has (draft-MR workflow spec).
                    rebase_log, gate_status = await _rebase_conflict_away(
                        db,
                        forge,
                        repo=repo,
                        orig_repo=orig_repo,
                        branch=branch,
                        base=base,
                        work_item_id=work_item_id,
                        moved=moved,
                        run_dirs=run_dirs,
                        node_id=node_id,
                    )
                    log += rebase_log
                    rebased = gate_status == "done"
                    if rebased and not has_rebase_bounce:
                        # Nothing will re-verify the rebased head, so this
                        # node reads its pipeline again itself and merges only
                        # if it is green (code-review).
                        head_sha = await git._head_sha(repo, branch)
                        ci_status = await forge.ci_status(
                            repo=repo, mr=MR(number=0, url=""), branch=branch
                        )
                        recheck_log, gate_status = await ci.render_ci(
                            ci_status, forge=forge, repo=repo, branch=branch, head_sha=head_sha
                        )
                        log += recheck_log
                        rebased = False
                        if gate_status == "conflict" and ci_status.no_checks:
                            # The forge has not recomputed mergeability for
                            # the head just pushed; its checks are on the way.
                            gate_status = "waiting"
                if rebased:
                    # Rebased the conflict away, not merged: this node's
                    # `on_base_changed` restart re-verifies the rebased head
                    # before anything is merged. "rebased", not "done" --
                    # `run_task`'s multi-repo loop must not count this target
                    # as merged (code-review); it normalizes it itself.
                    status = "rebased"
                elif gate_status == "waiting":
                    status = "ci_pending"
                elif gate_status == "conflict":
                    status = "conflict"
                elif gate_status == "unreviewed":
                    status = "unreviewed"
                elif gate_status == "abandoned":
                    status = await _stop_for_abandoned_run(db, work_item_id, node_id, log)
                elif gate_status != "done":
                    status = "failed"
                elif ci_status.block_reason == "not_approved":
                    # An approval rule the pipeline cannot see: an ordinary
                    # pending state, here as at `external_approval`
                    # (`missing-external-approval-is-normal-pending-state`).
                    log += (
                        "waiting for the merge request's required approval: "
                        f"{ci_status.merge_detail}\n"
                    )
                    status = "approval_pending"
                elif (
                    refused := await _refuse_unreviewed(db, work_item_id, node_id, repo, branch)
                ) is not None:
                    # Read again at the last moment: only Kraft's own clean
                    # rebase above, which repins, may have moved it since.
                    log += refused
                    status = "unreviewed"
                else:
                    # A genuine refusal still raises inside forge.merge and
                    # fails the node.
                    head = reviewed_head.branch_tip(repo, branch) or ""
                    try:
                        await forge.merge(
                            repo=repo, branch=branch, mr=MR(number=0, url=""), head=head
                        )
                    except HeadMoved as exc:
                        # The forge refused: the head it holds is not the one named.
                        if not _pinned(db, work_item_id):
                            raise
                        log += await _refuse_forge_head(
                            db, work_item_id, node_id, repo, branch, exc.current
                        )
                        status = "unreviewed"
                    else:
                        # And a refusal the CLI reported as success does not get
                        # through either: `glab mr merge` exits 0 for "merge when
                        # checks pass" and merges nothing (Kraft-79x3). The landing
                        # is read off the forge, now and at every later
                        # observation.
                        landed = await forge.find_mr(repo=repo, branch=branch)
                        state = landed.state if landed is not None else "gone"
                        if state == "merged":
                            log += f"merged !{landed.number}\n"
                            status = "done"
                        elif state == "open":
                            log += (
                                f"!{landed.number} is still open after the merge command "
                                "returned; the forge may merge it when its checks pass -- "
                                "waiting for it to land\n"
                            )
                            status = "merge_pending"
                        else:
                            log += f"the merge request is {state}, not merged; nothing landed\n"
                            status = "failed"
        case "merge_watch":
            if not ci_checks:
                return (*await _skip_ci(db, work_item_id, node_id, hook_point, orig_repo), None)
            # The commit this item merged, kept from the first observation on
            # (Kraft-h6wi2): what it asks is whether *this* merge broke `base`, and
            # a base that keeps moving must not keep the answer waiting.
            wait = db.read(lambda c: waits.open_wait(c, work_item_id, hook_point))
            seen = wait.last if wait is not None and wait.last else {}
            head_sha = seen.get("watched_sha")
            retry = ""
            if head_sha is None:
                try:
                    merged = await forge.find_mr(repo=repo, branch=branch)
                except ForgeError as exc:
                    retry = f"the merge commit is not known yet ({exc}), will retry\n"
                else:
                    if merged is not None and merged.state != "merged":
                        retry = f"!{merged.number} is {merged.state}, not merged yet, will retry\n"
                    else:
                        # `base`: the item's base branch, what its merge landed on.
                        # The forge may report no commit, or no merge request at
                        # all: the tip as first read is then the nearest thing,
                        # and kept.
                        head_sha = (
                            merged.merged_sha if merged is not None else ""
                        ) or await _builtins.upstream_head(orig_repo, base)
                        retry = f"rev-parse HEAD failed in {orig_repo}, will retry\n"
            if head_sha is None:
                # Peer callers (`ensure_worktree`, `refresh_worktree_base`) treat a
                # failed `rev-parse HEAD` as "nothing to report yet", not a value to
                # hand onward -- `git_read`'s 10s timeout or a transient fetch failure
                # both land here. Passing None through would crash
                # `GhCli.branch_ci_status` (`head_sha[:7]`) or, on GitLab, silently pin
                # the previous commit's pipeline to this merge's head (code-review).
                log, status = retry, "waiting"
            else:
                note = WaitNote(
                    None,
                    {"watched_sha": head_sha, "grace_from": seen.get("grace_from")},
                    False,
                    waits.now(),
                )
                stored = db.read(
                    lambda c: c.execute(
                        "SELECT ci_pipeline_ref FROM work_items WHERE id = ?", (work_item_id,)
                    ).fetchone()
                )
                stored_ref = stored["ci_pipeline_ref"] if stored else None
                pipeline_id = ""
                if stored_ref and ":" in stored_ref:
                    stored_sha, stored_pipeline_id = stored_ref.split(":", 1)
                    if stored_sha == head_sha:
                        pipeline_id = stored_pipeline_id
                # `branch_ci_status`, not `ci_status`: the checked-out branch's own
                # MR is already merged, so `ci_status`'s `gh pr view`/`glab mr
                # view` has nothing left to resolve and would raise instead of
                # reading the target branch's pipeline at all (code-review). It
                # also takes `head_sha` explicitly -- the freshly-fetched upstream
                # head above, not whatever this checkout's local HEAD happens to
                # be if it has not been pulled (code-review, second finding).
                ci_status = await forge.branch_ci_status(
                    repo=orig_repo, branch=base, head_sha=head_sha, pipeline_id=pipeline_id
                )
                # Only pin a read that is actually *for* head_sha (plan-review
                # finding 2). Right after a merge, the target branch usually has
                # no pipeline for the new head yet, so this first read is almost
                # always the *previous* commit's settled pipeline -- pinning it
                # unconditionally would map head_sha to that wrong, already-done
                # id, and every re-entry would poll it forever instead of ever
                # finding the real one. `""` covers a backend that reports no sha
                # at all -- same as an unset pipeline_ref, nothing to pin either
                # way.
                if ci_status.pipeline_ref and ci_status.sha in ("", head_sha):
                    await db.write(
                        lambda c, ref=f"{head_sha}:{ci_status.pipeline_ref}": (
                            store.set_ci_pipeline_ref(c, work_item_id, ref)
                        )
                    )
                if ci_status.no_checks and _no_run_past_grace(db, work_item_id, hook_point):
                    return (
                        *await _no_post_merge_ci(
                            db, work_item_id, node_id, hook_point, orig_repo, base, head_sha
                        ),
                        None,
                    )
                log, status = await ci.render_ci(
                    ci_status, forge=forge, repo=orig_repo, branch=base, head_sha=head_sha
                )
                if status == "abandoned":
                    # A run cancelled for a newer push to the base (they share a
                    # concurrency group, and the forge keeps one pending run) has
                    # a successor: the tip holds this merge, and its CI answers
                    # too. With the watched commit still the tip, a person did it.
                    tip = await _builtins.upstream_head(orig_repo, base)
                    if tip is None or tip == head_sha:
                        return (
                            log,
                            await _stop_for_abandoned_run(db, work_item_id, node_id, log),
                            None,
                            None,
                        )
                    payload = {
                        "node_id": node_id,
                        "task": hook_point,
                        "repo": str(orig_repo),
                        "branch": base,
                        "from": head_sha,
                        "to": tip,
                        "why": log.rstrip().splitlines()[-1],
                    }
                    await db.write(
                        lambda c: events.append(
                            c, work_item_id, ForgeEvent.POST_MERGE_CI_MOVED, payload
                        )
                    )
                    moved = waits.now()
                    note = WaitNote(
                        None, {"watched_sha": tip, "grace_from": moved.isoformat()}, False, moved
                    )
                    return (
                        f"{log.rstrip()}\nwatching {tip[:7]} on {base} instead, which holds "
                        f"{head_sha[:7]}\n",
                        "waiting",
                        findings,
                        note,
                    )
                if status == "infra":
                    # Same counter, same key format, as `ci_poll`'s own
                    # `ci_infra:<node_id>` (plan-review finding 3, first half):
                    # `retry_infra_once` only starts the job again, it does not
                    # settle it, so an unbounded number of *separate* scheduler
                    # re-entries could each see "infra" again and kick
                    # `retry_jobs` again forever against a persistently broken
                    # runner. `node_id` already namespaces this from `mr_checks`'
                    # own counter row, so this needs no new policy.yaml entry.
                    if await _infra_budget_spent(db, work_item_id, node_id, log):
                        # `walk.py`'s node loop already turns this into
                        # `stops.stop_for_infra` -> `needs_human` for any node
                        # (`walk.py:245,340,394`) -- no new plumbing needed here.
                        return log, "infra_stop", findings, None
                    log, status = await ci.retry_infra_once(
                        forge,
                        repo=orig_repo,
                        branch=base,
                        head_sha=head_sha,
                        first=ci_status,
                        # Same reason the first read above uses
                        # `branch_ci_status`: this item's merge request is merged
                        # and gone, so the re-read after the kick must not try to
                        # resolve one either (judge finding, rounds 0/2/3).
                        branch_only=True,
                    )
                if status in ("infra", "failed", "conflict"):
                    # Settled red after at most one infra kick: nothing here is
                    # this item's own code to fix loop over (Kraft-43kw). File
                    # what a human needs, warn whoever else is sitting on the
                    # break, and still report "done" -- this node's job is
                    # reporting what happened to the branch, not fixing it.
                    job = ci_status.failed_jobs[0] if ci_status.failed_jobs else None
                    trace = (
                        "\n\n".join(
                            _job_finding_message(ci_status.jobs, j) for j in ci_status.failed_jobs
                        )
                        or log
                    )
                    title = (
                        f"post-merge pipeline broke on {base}: {job.name if job else 'unknown job'}"
                    )[: beads.MAX_TITLE]
                    info = db.read(
                        lambda c: c.execute(
                            "SELECT bead_cwd FROM work_items WHERE id = ?", (work_item_id,)
                        ).fetchone()
                    )
                    # `bead_cwd` wins, `orig_repo` (already in scope -- it is
                    # `row["repo"]`, the same column, read once at `run_task`'s
                    # call site) otherwise: the item's own recorded workspace
                    # always wins, same precedence `entry.close_beads` uses
                    # (plan-review finding 4) -- see this task's design note
                    # above for why the fallback value itself differs from
                    # `close_beads`'s `bd_cwd`.
                    cwd = (info["bead_cwd"] if info else None) or str(orig_repo)
                    follow_up: str | None = None
                    try:
                        follow_up = await beads.intake(title, description=trace, cwd=cwd)
                    except Exception as exc:  # noqa: BLE001 -- a bd failure must not block "done"
                        logger.warning(
                            "merge_watch: follow-up bead not filed for %r: %r", title, exc
                        )
                    broken = db.read(
                        lambda c: [
                            r["id"]
                            for r in c.execute(
                                # `waiting` as well as `active` (gate review):
                                # an item parked on its own pipeline sits on
                                # this same broken commit and is the one most
                                # likely to re-discover the break in its own
                                # fix loop -- exactly what this warning
                                # exists to prevent. No other status can
                                # usefully be warned: `paused`/`needs_human`
                                # are already stopped, and a completed item
                                # has nothing left to rebase.
                                f"SELECT id FROM work_items WHERE status IN ({in_list(RUNNING)}) "
                                "AND base_ref = ? AND id != ?",
                                (head_sha, work_item_id),
                            ).fetchall()
                        ]
                    )
                    for other_id in broken:
                        stopped = await db.write(
                            lambda c, oid=other_id: _pause_for_broken_base(
                                c, oid, broken_by=work_item_id, follow_up_bead=follow_up
                            )
                        )
                        # mark first, then signal, as `pause_work_item` does
                        from kraft.worker import reattach  # the executor imports this module

                        for s in stopped:
                            reattach.interrupt_group(s["pid"], started=s["pid_start_time"])
                    log += f"filed {follow_up or '(no bead filed)'}, paused {len(broken)} item(s)\n"
                    status = "done"
        case _:
            # A target with no handler is a configuration limit, not a bug in
            # this run: name it and stop for a person (Ruling 48). Every V1
            # `ForgeAction` maps to one (`V1_HANDLERS`), so only a name that
            # never passed the schema reaches this. `config_error` is terminal
            # at every tier, so `walk_node` turns it straight into
            # `needs_human` with this reason instead of a retry.
            log = (
                f"forge target {handler!r} has no handler in Kraft. Nothing is wrong with "
                "the merge request; this node cannot run.\n"
            )
            status = "config_error"
    return log, status, findings, note


def _pause_for_broken_base(c, work_item_id: str, **why) -> list:
    """The live sessions the pause marked, for the caller to signal."""
    sessions = store.running_sessions_for_node(c, work_item_id)
    ids = [s["id"] for s in sessions]
    return sessions if store.pause_for_broken_base(c, work_item_id, ids, **why) else []


async def _stop_for_abandoned_run(db, work_item_id: str, node_id: str, log: str) -> str:
    """A cancelled run nobody followed up (`ci.render_ci`'s "abandoned",
    Kraft-kbqmk): straight to a person, as for infra-red -- nothing for a fix
    loop to fix -- and the stop names it (`stops.stop_for_infra`)."""
    reason = log.rstrip().splitlines()[-1]
    await db.write(
        lambda c: events.append(
            c, work_item_id, ForgeEvent.CI_RUN_ABANDONED, {"node_id": node_id, "reason": reason}
        )
    )
    return "infra_stop"


def _job_finding_message(jobs: tuple[str, ...], job: FailedJob) -> str:
    """The finding message for one failed job: its own trace tail when the
    backend attached one to `ci_status.jobs` (glab's `_failure_detail`
    interleaves each failed job's header line -- `"job {name}: failed
    (...)"` -- with that job's own indented trace lines, in job order), the
    bare name-and-reason otherwise (gh's one-liner `"{name}: {conclusion}"`
    has no trace to give).

    Carrying whatever per-failure content is available, rather than just the
    job name, is what lets two successive code-reds on the same job carry
    different `findings.Finding.fingerprint`s when the underlying failure
    actually changed, and the identical fingerprint when it did not --
    fingerprint hashes the normalised message (`findings.py`), so the message
    is the only thing that can tell those two cases apart.
    """
    prefix = f"job {job.name}:"
    block: list[str] = []
    capturing = False
    for line in jobs:
        if line.startswith(prefix):
            capturing = True
            block.append(line)
            continue
        if capturing:
            if line.startswith("  "):
                block.append(line)
                continue
            break
    if block:
        return "\n".join(block)
    return f"job {job.name} failed" + (f": {job.failure_reason}" if job.failure_reason else "")


def _job_findings(failure: CIStatus, trace: str, hook_point: str) -> list[dict]:
    """One finding per failed job, for the fix loop the measuring task feeds
    (Kraft-cbr §3): `ci_poll`'s red pipeline and a merge train's or queue's
    alike. `trace` is the text a file and line are read from."""
    return [
        {
            "severity": "important",
            "message": _job_finding_message(failure.jobs, j),
            "file": _trace_file(trace),
            "line": _trace_line(trace),
            "source_plugin": hook_point,
        }
        for j in failure.failed_jobs
    ]


_TRACE_LOC = re.compile(r"([^\s:]+\.\w+):(\d+)")


def _trace_file(log: str) -> str | None:
    m = _TRACE_LOC.search(log)
    return m.group(1) if m else None


def _trace_line(log: str) -> int | None:
    m = _TRACE_LOC.search(log)
    return int(m.group(2)) if m else None


async def run_task(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    handler: str,
    backend: str,
    #: The repo entry's `forge` (`repos.yaml`), for `backend: auto`. None means
    #: nothing recorded, which `backend_for` turns into a failed node.
    repo_forge: str | None = None,
    repo: Path,
    #: The original repo, not the worktree -- `ci_poll`'s confirmed-conflict
    #: rebase (Kraft-9h7v) needs origin's current base branch tip, which
    #: `refresh_worktree_base` fetches from here. None (every test call site
    #: that predates this, and any future one that never exercises the
    #: conflict path) falls back to `repo` -- harmless, since that path is
    #: the only reader. A workspace's root; each member reads its own
    #: checkout's origin instead (Kraft-puqxq).
    orig_repo: Path | None = None,
    branch: str,
    title: str,
    round: int = 0,
    #: A wait handler's resolved bounds (`ForgeTask.wait_bounds`). None -- a
    #: caller with no task, a test -- runs under `DEFAULT_WAIT`.
    wait: WaitBounds | None = None,
    #: The reviewers `mr.automated_review` waits for (Ruling 171), already
    #: resolved across the item, repository and task layers; empty names none.
    reviewers: Reviewers = (),
    #: The repository's `ci_checks:`. Read by `mr.ci` and `mr.post_merge_ci`.
    ci_checks: bool = True,
    head_sha: str | None = None,
    #: Whether this node declares `on_base_changed` -- see `_run_one`'s
    #: docstring. Defaults to False,
    #: the safe assumption for any caller (a test, a future handler) that
    #: does not know any better.
    has_rebase_bounce: bool = False,
    #: False for an in-place item: `open_mr` does not refuse the person's
    #: own uncommitted edits in their checkout.
    check_clean: bool = True,
    #: Whether a later task of this node reads what the review bots said
    #: (`AgentInput.BOT_FEEDBACK`): the review wait then settles and collects
    #: rather than failing on feedback. A multi-repo item never collects, so
    #: feedback is never collected and then dropped: Kraft judges it itself.
    reads_feedback: bool = False,
    #: The review wait's grace period (`ForgeTask.grace`).
    grace: timedelta = DEFAULT_GRACE,
) -> str:
    """One forge node -- against every repo `work_item_repos` names for this
    item, deepest submodule first, root last (design 3a), or just `repo` when
    the table has no rows for it (every single-repo item, unchanged from
    before this function went multi-repo).

    Records a session the way a builtin does rather than the way the subprocess
    adapter does: the work happens in this process, so there is no child to
    supervise and no log fd to hand over. The row exists from the start, not
    just at the end: pause/abandon/reattach all key off `worker_sessions`, and
    recording nothing until a node finished broke all three (Kraft-41b,
    Kraft-7xt).

    A wait handler (`_WAITS`) makes one observation and records it
    (`kraft.waits.observe`): a pending one reports `waiting` and reuses its
    session on the next dispatch, one past its deadline reports `capped_out`
    -- a stop for a person, never a code failure.
    """
    actual_session_id, log_path, result_path = await _builtins.start_session(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=node_id,
        hook_point=hook_point,
        round=round,
        head_sha=head_sha,
        # A pipeline still pending is the same wait episode, not a new
        # attempt (Kraft-ivh1) -- every other handler keeps minting a fresh
        # row every dispatch.
        reuse_if_waiting=handler in _WAITS,
    )
    reused = actual_session_id != session_id
    session_id = actual_session_id
    rows = db.read(
        lambda c: c.execute(
            "SELECT * FROM work_item_repos WHERE work_item_id = ? ORDER BY merge_rank",
            (work_item_id,),
        ).fetchall()
    )
    multi = bool(rows)
    targets = [(r["id"], Path(r["repo_path"]), r["role"]) for r in rows] or [(None, repo, "root")]
    root_has_changes = True
    # The frozen target's, never a live setting: membership and the pointer
    # policy were captured when the item was filed
    # (`work-item-target-selection-is-immutable`).
    item = db.read(
        lambda c: c.execute(
            "SELECT materialized_chain, root_merge_policy FROM work_items WHERE id = ?",
            (work_item_id,),
        ).fetchone()
    )
    snapshot = store.materialized_chain_of(item) if item is not None else None
    workspace = snapshot is not None and snapshot.target.kind == "workspace"
    if workspace:
        root_policy = snapshot.target.root_pointer_policy
    else:
        # Kraft-zvqwl: an item filed before workspaces keeps the pointer
        # policy it was filed with, in its column: legacy `bump`/`bump_no_mr`
        # bump, `skip` (or none) leaves the root alone.
        legacy = item["root_merge_policy"] if item is not None else None
        bumps = legacy in ("bump", "bump_no_mr")
        root_policy = RootPointerPolicy.BUMP if bumps else RootPointerPolicy.IGNORE
    # The item's base branch (Kraft-v9gbi): its own repository's, or a
    # workspace root's. A member's merge request targets the member's own
    # default branch (`builtins.base_branch(member=True)`, per target below).
    item_base = await _builtins.base_branch(db, work_item_id, orig_repo or repo)
    if multi:
        root_repo = next(t for _, t, role in targets if role == "root")
        mounts = {r["submodule_path"] for r in rows if r["role"] == "submodule"}
        root_has_changes = await git.source_changed(
            root_repo, branch, base=item_base, exclude=mounts
        )
        root_state = next(r["merge_state"] for r in rows if r["role"] == "root")
        # `merge` publishes the root whenever it has something to publish: its
        # source, a merge request it already has (opened while it had source,
        # or a pointer bump's fallback), or a bump its policy asks for. An
        # item never completes while a merge request it opened is still open
        # (`external-wait-covers-merge-request-lifecycle`).
        publishes = handler == "merge" and (
            root_state != "pending" or root_policy is RootPointerPolicy.BUMP
        )
        if not root_has_changes and not publishes:
            # A pointer-only root has no source of its own to review -- it
            # never goes through the ordinary per-repo loop below
            # (`workspace-root-code-change-gets-a-root-merge-request`). Its
            # pointer follows the item's root-pointer policy after every
            # member merges (`_ready_root`): at `open_mr` time no member has
            # merged yet, so a root MR here would review a pointer that
            # doesn't exist.
            targets = [t for t in targets if t[2] != "root"]
        # An item selects more members than it changes, and only a changed
        # one is published (`changed-child-repositories-get-separate-merge-
        # requests`): a member with nothing beyond its own base gets no merge
        # request and is no reason to stop (Kraft-j14jn). Only one that never
        # opened one -- an open merge request is followed to its end.
        untouched = set()
        for r in rows:
            if r["role"] == "submodule" and r["merge_state"] == "pending":
                sub = Path(r["repo_path"])
                sub_base = await _builtins.base_branch(db, work_item_id, sub, member=True)
                if await git.commits_ahead(sub, branch, sub_base) == 0:
                    untouched.add(r["id"])
        targets = [t for t in targets if t[0] not in untouched]

    if handler == "merge_watch" and targets:
        # `merge_watch` reads the item's base branch in `orig_repo`, and finds
        # the merge commit it watches through `repo`'s merge request
        # (Kraft-h6wi2). One pass, not one per target: a multi-repo item would
        # otherwise poll the same branch N times and file N identical
        # follow-up beads (gate review). Always the root: a member's merge
        # request landed on the member's own base, not this one, so its
        # merged commit is never on the branch whose CI is read. With
        # `root_policy == "skip"` the root has no merge request, and the watch
        # starts from the base's tip. After the `multi` block above on
        # purpose: that block still needs the root row to resolve
        # `root_has_changes`.
        root = next((r for r in rows if r["role"] == "root"), None)
        targets = [(root["id"], Path(root["repo_path"]), "root") if root else targets[0]]

    if handler == "open_mr" and multi and not targets:
        # Every selected repository untouched: the workspace form of
        # Kraft-vz8e's empty branch, refused the same way.
        return await _builtins.finish_session(
            db,
            log_path,
            result_path,
            session_id=session_id,
            work_item_id=work_item_id,
            status="config_error",
            log=f"refusing to open a merge request for {branch}: no selected repository has "
            "commits beyond its base -- the implementation committed nothing\n",
            reused=reused,
        )
    try:
        live_forge = resolve(backend_for(backend, repo_forge))
    except ForgeError as exc:
        # No forge this repo can reach: nothing was launched, so this is a
        # configuration stop naming its cause, never a failed task a fix loop
        # would spend cycles on (Kraft-hr0xr). Finished here, not raised, so
        # the session row started above is not stranded (Kraft-41b, Kraft-7xt).
        return await _builtins.finish_session(
            db,
            log_path,
            result_path,
            session_id=session_id,
            work_item_id=work_item_id,
            status="config_error",
            log=f"{hook_point} cannot run: {exc}\n",
            reused=reused,
        )
    # Whether the merge wait's last observation asked for a merge, and whose:
    # the loop stops at the first target that has not merged, so that was the
    # first target not recorded merged. A later target's merge is not asked
    # for yet, however the last observation ended.
    merge_wait = (
        db.read(lambda c: waits.open_wait(c, work_item_id, hook_point))
        if handler == "merge"
        else None
    )
    merge_requested = (
        merge_wait is not None
        and merge_wait.last is not None
        and merge_wait.last["condition"] == "merge"
    )
    merged_rows = {r["id"] for r in rows if r["merge_state"] == "merged"}
    awaiting_landing = next((t[0] for t in targets if t[0] not in merged_rows), None)
    log, status = "", "done"
    collect = handler == "automated_review" and reads_feedback and not multi
    settle_bots = handler == "automated_review"
    note: WaitNote | None = None
    #: A multi-repo review wait's memory, one entry per repository. Carried
    #: over from the last check, so a repository this check does not reach
    #: keeps what it had.
    memories: dict = {}
    if multi and settle_bots:
        last = db.read(lambda c: waits.open_wait(c, work_item_id, hook_point))
        memories = dict(((last.last if last is not None else None) or {}).get("memory", {}))
    #: The members a conflict rebase moved (Kraft-puqxq).
    moved: list[Path] = []
    #: Why the observation could not be made at all, when it could not.
    unobserved: str | None = None
    #: The merge found a head the final review did not approve.
    unreviewed = False
    #: The forge merged one.
    landed_unreviewed = False
    checkout_stopped = False
    # Findings only ever reach `finish_session` for a single-target run: a
    # submodule's own CI is not this item's `mr_checks` node, and findings
    # from it would double-count against the wrong job (Kraft-cbr §3).
    findings: list[dict] | None = None
    try:
        # Resolved once against the item's own worktree (`repo`, not whichever
        # target the loop below is on) -- a multi-repo item's submodule paths
        # are not where the agent's `on.mr.describe` artifact lives.
        meta = mr_ops.read_mr_meta(repo, work_item_id, run_dirs)
        if handler == "open_mr" and workspace:
            # Every workspace item, before anything opens: a submodule the agent changed
            # that the target never selected has no branch and no merge
            # request anywhere, and must stop the chain rather than be dropped
            # -- whether or not root itself has anything to publish.
            covered = {Path(r["repo_path"]).resolve() for r in rows if r["role"] == "submodule"}
            await git._assert_submodules_covered(repo, covered)
        for row_id, target_repo, role in targets:
            if multi and role == "root" and handler in ("mark_ready", "external_approval"):
                # The root's own merge request waits for its members: it is
                # marked ready by `merge`, once they have landed and it names
                # their merged revisions (`root-source-merge-request-
                # readiness-waits-for-child-merges`), and its approval is
                # awaited there, once there is something to approve. A draft
                # reads as approved on both CLIs (`classify_block_reason`), so
                # reading it here would record an approval nobody gave.
                log += f"[{target_repo.name}] stays a draft until its members merge\n"
                continue
            requested = merge_requested and row_id == awaiting_landing
            settled = None
            if multi and role == "root" and handler == "merge":
                # Reached only once every member merged: the loop stops at the
                # first one that does not.
                root_log, settled = await _ready_root(
                    live_forge,
                    db,
                    rows,
                    root_repo=target_repo,
                    branch=branch,
                    base=item_base,
                    title=title,
                    work_item_id=work_item_id,
                    node_id=node_id,
                    has_source=root_has_changes,
                )
                log += root_log
            # `merge_watch` reads the item's own base whichever target it
            # was handed (above); every other handler targets this one's.
            member = role == "submodule" and handler != "merge_watch"
            if settled is None:
                one_log, one_status, one_findings, one_note = await _run_one(
                    live_forge,
                    db,
                    repo=target_repo,
                    # A member is its own source repository: its conflict
                    # rebase reads its own origin, not the root's (Kraft-puqxq).
                    orig_repo=target_repo if member else (orig_repo or target_repo),
                    branch=branch,
                    base=(
                        await _builtins.base_branch(db, work_item_id, target_repo, member=True)
                        if member
                        else item_base
                    ),
                    title=title,
                    work_item_id=work_item_id,
                    node_id=node_id,
                    handler=handler,
                    hook_point=hook_point,
                    meta=meta,
                    has_rebase_bounce=has_rebase_bounce,
                    reviewers=reviewers,
                    ci_checks=ci_checks,
                    merge_requested=requested,
                    check_clean=check_clean,
                    repo_row_id=row_id,
                    moved=moved if member else None,
                    run_dirs=run_dirs,
                    collect=collect,
                    settle_bots=settle_bots,
                    scope=str(target_repo) if multi else None,
                    grace=grace,
                    bounds=wait or DEFAULT_WAIT,
                    session_id=session_id,
                    round=round,
                )
                if multi and settle_bots and one_note is not None:
                    memories[str(target_repo)] = one_note.extra["memory"]
                    wakes = [n.next_at for n in (note, one_note) if n and n.next_at]
                    one_note = replace(
                        one_note, extra={"memory": memories}, next_at=min(wakes, default=None)
                    )
                note = one_note or note
            else:
                one_log, one_status, one_findings = "", settled, None
            if not multi:
                findings = one_findings
            log += (f"[{target_repo.name}] " if multi else "") + one_log
            if row_id is not None:
                new_state = {
                    "open_mr": "open",
                    # "rebased" (conflict rebased away, not actually merged --
                    # see `_run_one`) maps to `None`: this row stays whatever
                    # it already was (`open`), not "merged" -- the bounce
                    # this triggers below re-enters `merge` from scratch once
                    # verify has re-run, and `_run_one`'s own "already
                    # merged" check is what makes that re-entry idempotent.
                    # "merge_rejected" maps to `None` too: the forge dropped
                    # it from its train or queue, and the merge request stays
                    # open for the node's merge-rejection recovery.
                    "merge": (
                        "merged"
                        if one_status == "done"
                        else (
                            "failed"
                            if one_status
                            not in (
                                "rebased",
                                "merge_rejected",
                                "unreviewed",
                                "landed_unreviewed",
                                *_PENDING,
                            )
                            else None
                        )
                    ),
                }.get(handler)
                if new_state:
                    await db.write(
                        lambda c, i=row_id, s=new_state: store.update_repo_state(
                            c, i, merge_state=s
                        )
                    )
            if one_status != "done":
                # Stop here rather than merging later targets (root last) or
                # running the root pointer-bump below: a "rebased" result
                # means this target never actually landed, and root must not
                # bump a submodule pointer past a branch nothing merged yet
                # (code-review). Normalized back to "done" below once the
                # loop is known to have stopped for this reason alone.
                status = one_status
                break
        if status == "unreviewed":
            # Internal-only too: the session failed to merge, and the walk is
            # told why (`UNREVIEWED_HEAD`), below.
            unreviewed, status = True, "failed"
        if status == "landed_unreviewed":
            # And the same for a merge that already happened (`LANDED_UNREVIEWED`).
            landed_unreviewed, status = True, "failed"
        if status == "rebased":
            # Internal-only marker (worker_sessions.status has no "rebased"
            # value, and the walk's own bounce -- keyed off base_ref moving,
            # not this string -- already treats a plain "done" as the signal
            # to re-verify). Everything above that must not treat this target
            # as landed has already run off the un-normalized value.
            status = "done"
    except ForgeError as exc:
        log, status, findings = f"{hook_point} failed: {exc}\n", "failed", None
        if isinstance(exc, git.UnsafeWorktree):
            # Nothing a fix loop may touch: a person looks first (Kraft-xngty).
            status = "config_error"
        unobserved = str(exc)
    except (inplace.AutostashClash, inplace.CheckoutRefused) as exc:
        # The person's checkout: stopped for them, as a refused take is.
        log, status, findings = f"{hook_point} failed: {exc}\n", "config_error", None
        unobserved = str(exc)
        await inplace.stop_refused(db, work_item_id, node_id, exc)
        checkout_stopped = True

    if handler in _WAITS:
        status, log = await _observed(
            db,
            work_item_id=work_item_id,
            node_id=node_id,
            task=hook_point,
            handler=handler,
            bounds=wait or DEFAULT_WAIT,
            status=status,
            log=log,
            unobserved=unobserved,
            session_id=session_id,
            note=note,
        )
    recorded = await _builtins.finish_session(
        db,
        log_path,
        result_path,
        session_id=session_id,
        work_item_id=work_item_id,
        status=status,
        log=log,
        findings=findings,
        reused=reused,
    )
    # A member's move is a base change `base_ref` cannot show: report it, as
    # `builtins.mr_rebase` does, so the node's declared span re-verifies it.
    from kraft.executor.context import (  # the executor imports this module
        BASE_MOVED,
        CHECKOUT_REFUSED,
        LANDED_UNREVIEWED,
        UNREVIEWED_HEAD,
    )

    if checkout_stopped:
        return CHECKOUT_REFUSED
    if landed_unreviewed:
        return LANDED_UNREVIEWED
    if unreviewed:
        return UNREVIEWED_HEAD
    return BASE_MOVED if (moved and has_rebase_bounce and recorded == "done") else recorded


async def _observed(
    db,
    *,
    work_item_id: str,
    node_id: str,
    task: str,
    handler: str,
    bounds: WaitBounds,
    status: str,
    log: str,
    unobserved: str | None,
    session_id: str,
    note: WaitNote | None = None,
) -> tuple[str, str]:
    """Record one wait observation; return the session's status and log.

    A pending status becomes plain `waiting`, and one past the deadline
    `capped_out`: the wait ran out, which is neither done nor a failure
    (`external-wait-timeout-needs-human`). An observation that could not be
    made at all (`unobserved`, the forge's error) ends the wait as an `error`
    and keeps its status -- a failure stays a failure.

    A review wait that settles on its bots hands a `note`: its next check, its
    memory, and the instant it decided at. Settled by the deadline, it is recorded as
    the pending observation `observe` turns into `timed_out` at that same
    instant, and the session still ends `done`: for this wait the deadline is a
    settle (`automated-review-timeout-settles-the-collect-path`), not a stop."""
    kind, own = _WAITS[handler]
    by_deadline = note is not None and note.settled_by_timeout and status == "done"
    if unobserved is not None:
        state, condition, result = "error", own, unobserved
    elif status in _PENDING or by_deadline:
        state, condition, result = "pending", _PENDING.get(status) or own, "pending"
    else:
        # Settled on whatever it was waiting for -- the last pending
        # observation's condition, or the handler's own on a first look.
        wait = db.read(lambda c: waits.open_wait(c, work_item_id, task))
        condition = wait.last["condition"] if wait is not None and wait.last else own
        state, result = "settled", status
    outcome = await db.write(
        lambda c: waits.observe(
            c,
            work_item_id,
            node_id=node_id,
            task=task,
            kind=kind,
            bounds=bounds,
            condition=condition,
            state=state,
            result=result,
            session_id=session_id,
            now=note.now if note else None,
            next_at=note.next_at if note else None,
            extra=note.extra if note else None,
        )
    )
    if outcome == "timed_out" and by_deadline:
        return "done", log + (
            "settled at the wait's deadline; review bots that never posted had nothing to say\n"
        )
    if outcome == "timed_out":
        return "capped_out", log + (
            f"timed out: still waiting for {condition} at the wait's deadline; "
            "nothing about the code failed\n"
        )
    return ("waiting" if state == "pending" else status), log


async def _point_at_merged_members(
    forge: Forge, db, root_repo: Path, branch: str, work_item_id: str
) -> list[str]:
    """Move the pointer in `root_repo` of each member whose merge request
    merged in this item to the revision that merge landed, and commit them
    (Kraft-n60oh). Returns the mount paths that moved. A member the item never
    merged keeps its pointer, and a merged one never moves past its merge to
    whatever its origin's tip has become since: the item built neither.

    Read afresh, not off `run_task`'s rows: a member merged earlier in the
    same pass is recorded merged only in the table.

    The commit, and the direct push of `HEAD` that may follow it, both go
    wherever the root's HEAD names, so it must name the item's branch."""
    git.assert_on_branch(root_repo, branch)
    merged = db.read(
        lambda c: c.execute(
            "SELECT repo_path FROM work_item_repos WHERE work_item_id = ? "
            "AND role = 'submodule' AND merge_state = 'merged' ORDER BY merge_rank",
            (work_item_id,),
        ).fetchall()
    )
    bumped = []
    for r in merged:
        sub_path = Path(r["repo_path"])
        landed = await forge.find_mr(repo=sub_path, branch=branch)
        if landed is None or landed.state != "merged" or not landed.merged_sha:
            raise ForgeError(
                f"cannot tell which revision {sub_path.name}'s merge landed; "
                "its pointer is left where it was"
            )
        default = await _builtins.base_branch(db, work_item_id, sub_path, member=True)
        await git.run_git(sub_path, ["git", "fetch", "origin", default])
        git.assert_no_operation(sub_path)  # a checkout stores a planted autostash
        git.assert_lfsconfig_unmoved(sub_path, f"check {sub_path.name}'s merge out")
        await git.run_git(sub_path, ["git", "checkout", landed.merged_sha])
        rel = str(sub_path.relative_to(root_repo))
        await git.run_git(root_repo, ["git", "add", "--", rel])
        bumped.append(rel)
    # The pointers alone: whatever else is staged in the root is no part of
    # Kraft's own commit, which is repinned and may go straight onto the base.
    if not bumped:
        return []
    staged = await git.run_git(root_repo, ["git", "diff", "--cached", "--name-only", "--", *bumped])
    if not staged.strip():
        return []
    before = reviewed_head.branch_tip(root_repo, branch)
    await git.run_git(
        root_repo,
        [
            "git",
            "commit",
            "-m",
            f"chore: bump submodule pointers for {work_item_id}",
            "--",
            *bumped,
        ],
    )
    # Kraft's own commit, naming only what its members' merges landed: the
    # root the final review approved stays the approved one (`repin`).
    after = reviewed_head.branch_tip(root_repo, branch)
    await reviewed_head.repin(
        db, work_item_id, {str(root_repo): before}, {str(root_repo): after}, cause="pointer_bump"
    )
    return bumped


async def _ready_root_at_merged_members(
    forge: Forge, db, *, root_repo: Path, branch: str, work_item_id: str
) -> str:
    """A root with source changes of its own, once its members merged: point
    it at their merged revisions, push, and only now mark its merge request
    ready (`root-source-merge-request-readiness-waits-for-child-merges`)."""
    bumped = await _point_at_merged_members(forge, db, root_repo, branch, work_item_id)
    await forge.push(repo=root_repo, branch=branch)
    existing = await forge.find_mr(repo=root_repo, branch=branch)
    await forge.mark_ready(
        repo=root_repo, branch=branch, mr=MR(number=existing.number if existing else 0, url="")
    )
    moved = f"pointed {', '.join(bumped)} at the merged revisions, " if bumped else ""
    return f"[{root_repo.name}] {moved}marked ready now that its members merged\n"


async def _ready_root(
    forge: Forge,
    db,
    rows,
    *,
    root_repo: Path,
    branch: str,
    base: str,
    title: str,
    work_item_id: str,
    node_id: str,
    has_source: bool,
) -> tuple[str, str | None]:
    """The root's turn in `merge`, once every member merged: bump a
    pointer-only root, ready its merge request at the merged revisions, and
    await its own approval. Returns the log and the root's status when that
    settles it -- `done` for a bump that needed no merge request,
    `approval_pending` -- or None when its merge request goes on to merge.

    A merge the forge already holds queued, or has landed, is only read for
    its landing: the source branch may be gone with it, and nothing may be
    pushed to it. The forge's record, not the wait's, so a restart of the
    wait does not forget it (Kraft-l98h6)."""
    root = next(r for r in rows if r["role"] == "root")
    if root["merge_state"] == "merged":
        return "", None
    if root["merge_state"] == "open":
        existing = await forge.find_mr(repo=root_repo, branch=branch)
        if existing is not None and (existing.state == "merged" or existing.merge_queued):
            return "", None
    # Before the pointer bump, which a pointer-only root pushes straight onto
    # its base: a root the final review did not approve goes nowhere.
    refused = await _refuse_unreviewed(db, work_item_id, node_id, root_repo, branch)
    if refused is not None:
        return refused, "unreviewed"
    log = ""
    try:
        if root["merge_state"] == "pending" and not has_source:
            log, opened = await _bump_pointer_only_root(
                forge, db, rows, branch=branch, base=base, title=title, work_item_id=work_item_id
            )
            if not opened:
                return log, "done"
        log += await _ready_root_at_merged_members(
            forge, db, root_repo=root_repo, branch=branch, work_item_id=work_item_id
        )
    except ForeignCommits as exc:
        # The root's own push after its members merged: commits the forge
        # holds that the final review did not see.
        if not _pinned(db, work_item_id):
            raise
        held = await _refuse_forge_head(db, work_item_id, node_id, root_repo, branch, exc.remote)
        return log + held, "unreviewed"
    if await forge.approval_state(repo=root_repo, branch=branch) == "pending":
        return log + f"[{root_repo.name}] waiting for its required approval\n", "approval_pending"
    return log, None


async def _bump_pointer_only_root(
    forge: Forge, db, rows, *, branch: str, base: str, title: str, work_item_id: str
) -> tuple[str, bool]:
    """A requested bump of a root with no source changes: straight onto the
    item's base branch in the root (`base`) when it takes the push
    (`workspace-pointer-bump-prefers-direct-push`), otherwise as a merge
    request from the item's branch in the root
    (`workspace-pointer-bump-falls-back-to-merge-request`). Returns the log
    and whether it opened that merge request, which `_ready_root` then
    follows to its merge like any root merge request (Kraft-srt9v)."""
    root = next(r for r in rows if r["role"] == "root")
    root_repo = Path(root["repo_path"])
    bumped = await _point_at_merged_members(forge, db, root_repo, branch, work_item_id)
    if not bumped:
        return "every member pointer already names its merged revision\n", False
    try:
        await git.run_git(root_repo, ["git", "push", "origin", f"HEAD:{base}"])
        return (
            f"bumped {', '.join(bumped)} directly on {base}, no root merge request\n",
            False,
        )
    except ForgeError as exc:
        refused = str(exc).strip().splitlines()[-1] if str(exc).strip() else "refused"
    await forge.push(repo=root_repo, branch=branch)
    mr = await forge.open_mr(
        repo=root_repo,
        branch=branch,
        base=base,
        title=f"chore: bump submodule pointers for {title}",
        body=f"Moves {', '.join(bumped)} to the revisions merged for work item {work_item_id}.",
    )
    await db.write(
        lambda c: store.update_repo_state(
            c, root["id"], merge_state="open", mr_ref={"number": mr.number, "url": mr.url}
        )
    )
    return (
        f"{base} refused the direct push ({refused}); opened !{mr.number} "
        f"to bump {', '.join(bumped)}\n"
    ), True
