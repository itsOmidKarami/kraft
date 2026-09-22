from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from pydantic import TypeAdapter

from kraft import builtins as _builtins
from kraft import caps as _caps
from kraft import events, store, waits
from kraft import policy as _policy
from kraft.adapters import subprocess as _subprocess
from kraft.caps import Breach, DailyBreach, TokenBreach, UsdBreach, WorkItemBreach
from kraft.executor.context import RATE_LIMITED, WAITING, LaunchContext
from kraft.store import _now as _now
from kraft.templates.models import DEFAULT_WAIT, ResolvedNode, ResolvedTask
from kraft.worker import sandbox as _sandbox

#: Parses a raw event payload (the DB's dict, read back from JSON) into the
#: right `Breach` shape. Used only where a breach might come from history
#: instead of a fresh `budget_breach` call (`stop_for_budget`'s fallback) --
#: everywhere else a `Breach` is already a model, built by `budget_breach`.
_breach_adapter: TypeAdapter[Breach] = TypeAdapter(Breach)


@asynccontextmanager
async def claimed_or_stopped(
    db,
    work_item_id: str,
    node_id: str | None,
    *,
    reason: str,
    handed_off: Callable[[], bool] | None = None,
) -> AsyncIterator[None]:
    """Bracket a claim-then-hand-off region so no exit can leave a work item
    claimed and unowned.

    **The invariant.** A path that writes a *runnable* status
    (`store.claim_for_run`, `mark_reentered`) has taken ownership of the item: it
    now reads `active`, which means "a walk is behind this". Every such path must
    end by either handing the item to a walk, or leaving it in a status some
    selector will pick up again. A path that does neither leaves the item
    claimed with nothing behind it -- it *looks* running and is not, and nothing
    will ever select it: `waits.tick` filters `status = 'waiting'`,
    `rate_limit_retry.tick` filters `status = 'rate_limited'`, the board shows it
    as live, and the only trace is whatever was logged on the way out.

    **Enforced here rather than at each `return`**, for three reasons that a
    per-return `mark_needs_human` does not cover:

    * The exits are not all lexical. `walk.chain_of` raises `LookupError` on a
      legacy row and `next(...)` raises `StopIteration`; a static sweep of
      `return`/`raise` statements cannot see either, and both leave through this
      `finally` anyway.
    * This class has now recurred three times on one branch -- a crash in
      `resume`/`retry`/`skip`, then the same crash in the two pollers, then the
      pollers again with the crash swapped for a `logger.warning` and a bare
      `return`. Each round fixed the reported site. A bracket is the only form
      of the fix that the *next* early return inherits for free.
    * The stop it performs is the same stop at every site, so writing it once
      removes the chance of two doors disagreeing about what a stranded item
      should become.

    `handed_off` says "something else owns this item now" -- for a route or a
    poller that is `deps.task_is_live(app, wid)`, which is true both when this
    call spawned the walk and when `spawn` refused because another walk already
    holds it. A site that awaits its walk inline needs no callback: by the time
    this `finally` runs the walk has set its own terminal status, so the
    `active` test is already false.

    Deliberately not a `return` inside `finally` -- that would swallow an
    in-flight exception, and the 409 a refused hand-off raises has to reach the
    caller.
    """
    cause = ""
    try:
        yield
    except BaseException as exc:
        # The card names what escaped, not only that something did: the
        # exception otherwise reaches the server log alone.
        cause = f": {exc!r}"
        raise
    finally:
        if not (handed_off is not None and handed_off()):
            row = db.read(
                lambda c: c.execute(
                    "SELECT status FROM work_items WHERE id = ?", (work_item_id,)
                ).fetchone()
            )
            if row is not None and row["status"] == "active":
                stop = reason + cause
                await db.write(lambda c: store.mark_needs_human(c, work_item_id, node_id, stop))


def budget_breach(
    db, work_item_id: str, budget: _policy.Budget, *, row=None, path: str | None = None
) -> Breach | None:
    """The breached cap, or None. Evaluated fresh: it is a query, not a counter.

    A breach refuses the *next* launch. It cannot stop a running agent — cost is
    only known once that agent's session has exited (`usage.read_envelope`) — so
    the overshoot is bounded by the cost of one task, not by the cap.

    `row` and `path` are the item and the scope launching (a task's path, or a
    node's for its escalation turn): every scope enclosing it that sets a
    `token_budget` or `budget_usd` caps its own launches' spend (Ruling 195,
    `caps.budget_breach`), under `budget`'s instance-wide ceilings.
    """
    if row is not None and path is not None:
        scoped = db.read(lambda c: _caps.budget_breach(c, row, path))
        if scoped is not None:
            return scoped
    if budget.work_item_usd is None and budget.daily_usd is None:
        return None
    since = store.local_midnight_utc() if budget.daily_usd is not None else None
    item_usd, daily_usd = db.read(lambda c: store.budget_spend(c, work_item_id, since=since))
    if budget.work_item_usd is not None and item_usd >= budget.work_item_usd:
        return WorkItemBreach(scope="work_item", spent_usd=item_usd, cap_usd=budget.work_item_usd)
    if budget.daily_usd is not None and daily_usd >= budget.daily_usd:
        return DailyBreach(scope="daily", spent_usd=daily_usd, cap_usd=budget.daily_usd)
    return None


def budget_reason(breach: Breach) -> str:
    tail = " Nothing new was started; a running agent was not interrupted."
    if isinstance(breach, TokenBreach):
        where = f"`{breach.path}`" if breach.path else "the work item"
        return (
            f"token budget reached: {breach.spent_tokens} tokens spent in {where}, "
            f"cap {breach.cap_tokens} tokens." + tail
        )
    if isinstance(breach, UsdBreach):
        where = f"`{breach.path}`" if breach.path else "the work item"
        if breach.unknown_launches:
            return (
                f"budget_usd cannot be checked: {breach.unknown_launches} launch(es) in "
                f"{where} reported no cost, and unknown spend is never counted as free "
                f"(${breach.spent_usd:.2f} known, cap ${breach.cap_usd:.2f})." + tail
            )
        return (
            f"budget_usd reached: ${breach.spent_usd:.2f} spent in {where}, "
            f"cap ${breach.cap_usd:.2f}." + tail
        )
    where = (
        "this work item" if isinstance(breach, WorkItemBreach) else "today, across every work item"
    )
    return (
        f"budget cap reached: ${breach.spent_usd:.2f} spent on {where}, "
        f"cap ${breach.cap_usd:.2f}." + tail
    )


async def stop_for_budget(db, work_item_id: str, node: ResolvedNode, budget: _policy.Budget) -> str:
    # The fallback cannot fire in practice — sums only grow between the dispatch
    # that returned BUDGET and here — but a None would crash the escalation path
    # rather than stop the item, which is the wrong failure.
    # A scope's own cap (Ruling 195) is one only dispatch knew: it recorded
    # it as `scope_budget_reached` on the way out.
    tokens = next(
        (
            e["payload"]
            for e in reversed(db.read(lambda c: events.read_after(c, 0, work_item_id)))
            if e["type"] in ("token_budget_reached", "scope_budget_reached")
            # `token_budget_reached` was its name before Ruling 195.
        ),
        None,
    )
    breach = (
        budget_breach(db, work_item_id, budget)
        # `extra="ignore"` here, not the model's own `"forbid"`: this event's
        # payload is a breach's fields plus the launching task's own path
        # (`executor.dispatch.over_budget` writes `{**breach.model_dump(),
        # "task": ...}`), which is a legitimate extra key on this one
        # round-trip -- not a reader reaching for another scope's number.
        or (_breach_adapter.validate_python(tokens, extra="ignore") if tokens is not None else None)
        or WorkItemBreach(scope="work_item", spent_usd=0.0, cap_usd=0.0)
    )
    reason = budget_reason(breach)
    dump = breach.model_dump()
    await db.write(lambda c: store.mark_needs_human(c, work_item_id, node.id, reason, None, dump))
    return "needs_human"


def latest_rate_limit(db, work_item_id: str) -> dict | None:
    """The most recently appended `rate_limit_hit` event's payload, or None.

    Same reverse-scan idiom as `kraft.executor.dispatch.last_measurement`/
    `kraft.executor.dispatch.needs_context_question`: the event was just
    written by `_subprocess.run_task` in the same session this verdict came
    from, so the latest one is always the right one -- unless a fallback
    list ran out after it (`launch_fallback_exhausted`), which names the
    earliest reset among every candidate it found limited.
    """
    evts = db.read(lambda c: events.read_after(c, 0, work_item_id))
    for e in reversed(evts):
        if e["type"] in ("rate_limit_hit", "launch_fallback_exhausted"):
            return e["payload"]
    return None


async def stop_for_rate_limit(db, work_item_id: str, node: ResolvedNode) -> str:
    info = latest_rate_limit(db, work_item_id) or {}
    retry_at = info.get("resets_at_iso") or _now()
    await db.write(lambda c: store.mark_rate_limited(c, work_item_id, node.id, retry_at))
    return RATE_LIMITED


async def stop_for_waiting(
    db, work_item_id: str, node: ResolvedNode, waiting: list[ResolvedTask]
) -> str:
    """Park the item until the earliest next observation among `waiting`'s
    external waits (`kraft.waits`), and release the walk. A waiting task that
    somehow recorded no next observation is looked at again after the default
    initial interval rather than never."""
    due = db.read(lambda c: waits.next_observation(c, work_item_id, [t.path for t in waiting]))
    if due is None:
        due = (datetime.fromisoformat(_now()) + DEFAULT_WAIT.initial_interval).isoformat()
    await db.write(lambda c: store.mark_waiting(c, work_item_id, node.id, due))
    return WAITING


#: The suggestion a stop makes when what stopped it is outside the work:
#: nothing about the code is wrong, so the answer is the same node again once
#: the outside recovers (Kraft-s7c04.27).
RETRY_LATER = {
    "action": "retry",
    "reason": "the cause is outside the code; retry once it has recovered",
}


def suggestion(db, work_item_id: str, node_id: str) -> dict | None:
    """What the node's latest session suggested a person do next, from its
    result file's `suggested_action`, or None (Kraft-s7c04.27).

    The latest session, the one `store.mark_needs_human` names on the stop: a
    repair that concluded no repair can help reports `failed`, so it is the
    last thing the node ran before stopping. Read before any escalation turn,
    whose own session would otherwise stand in front of it."""
    row = db.read(
        lambda c: c.execute(
            "SELECT result_path FROM worker_sessions WHERE work_item_id = ? AND node_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (work_item_id, node_id),
        ).fetchone()
    )
    if row is None or not row["result_path"]:
        return None
    return _subprocess.read_suggested_action(Path(row["result_path"]))


async def stop_for_infra(db, work_item_id: str, node: ResolvedNode) -> str:
    """Something outside the code broke and a repair cannot fix it: the
    persisted `ci_infra:<node_id>` retry cap is spent on a pipeline red for
    the forge's own reasons (Kraft-h81i, Kraft-s8ul), the automated
    reviewer errored (Ruling 170), or a cancelled run has no successor
    coming (Kraft-kbqmk). Straight to needs_human, no fix cycle, no
    on_failure repair, and the stop names whichever it was.
    """
    info = db.read(lambda c: events.read_after(c, 0, work_item_id))
    reason = next(
        (
            e["payload"]["reason"]
            for e in reversed(info)
            if e["type"] in ("ci_infra_exhausted", "automated_review_errored", "ci_run_abandoned")
        ),
        "CI infrastructure failed and retrying it did not recover",
    )
    await db.write(
        lambda c: store.mark_needs_human(c, work_item_id, node.id, reason, suggested=RETRY_LATER)
    )
    return "needs_human"


async def stop_for_time_cap(db, work_item_id: str, node: ResolvedNode) -> str:
    """A scope's time cap ran out (Rulings 194, 195): the item stops for a
    human under the reason `caps.REACHED` recorded -- "`<scope>` hit its time
    cap of N minutes". Not a failure: no recovery, no fix attempt, and not in
    the stuck set, so no escalation turn answers it."""
    reason = db.read(lambda c: _caps.reason_of(c, work_item_id))
    await db.write(lambda c: store.mark_needs_human(c, work_item_id, node.id, reason))
    return "needs_human"


def _item_sandbox(row, launch: LaunchContext | None) -> dict | None:
    """`dispatch.item_sandbox`, the one resolution of an item's sandbox
    (Ruling 189). Imported here, not at the top: `dispatch` imports this
    module. Raises `SandboxUnresolved`, a `RuntimeError`, when it cannot tell
    -- unreadable is never "no sandbox"."""
    from kraft.executor.dispatch import item_sandbox

    return item_sandbox(row, launch)


def refuse_sandboxed_submodules(
    row, launch: LaunchContext | None, worktree: Path | None = None
) -> None:
    """Raise `RuntimeError` naming why, when `row`'s item runs sandboxed and
    host git in its `worktree` could reach a submodule's config, which a
    sandboxed worker writes. Called before any host-side git touches the
    worktree -- the walk and every door that refreshes the worktree first --
    and each caller already turns a `RuntimeError` into a stop for a human.

    Two ways in. The item mounts submodules (Kraft-dshto): refused at intake
    since Ruling 180, but an item filed before that, or one filed before
    workspaces (Kraft-zvqwl), can still be in flight. Or the worker made its
    own (Kraft-nx4id): `refuse_planted_repos`."""
    mounts = _builtins.item_mounts(row)
    if not mounts:
        refuse_planted_repos(row, launch, worktree)
        return
    if _item_sandbox(row, launch) is None:
        return
    snapshot = store.materialized_chain_of(row)
    refusal = (snapshot.sandbox_refusal() if snapshot is not None else None) or (
        _sandbox.submodule_refusal(f"work item {row['id']}")
    )
    raise RuntimeError(f"{refusal}. Its submodules: {', '.join(mounts)}")


def refuse_planted_repos(row, launch: LaunchContext | None, worktree: Path | None) -> None:
    """Raise `RuntimeError` naming the paths when `row`'s item runs sandboxed
    and its `worktree` holds a git repository Kraft did not create
    (`sandbox.planted_repos`): an untracked one, a populated gitlink, or a
    gitlink the branch added or moved. Host git never enters one
    (`sandbox.SUBMODULES_UNENTERED`), but a commit or a rebase still reads
    its config file, and a gitlink in the merge request is not work anyone
    asked for. Also each task's own dispatch check. An item that mounts
    submodules is `refuse_sandboxed_submodules`'s.

    An unsandboxed item costs no git at all; one whose sandbox cannot be
    resolved stops only if its worktree holds something it could matter to."""
    if worktree is None or not worktree.is_dir() or _builtins.item_mounts(row):
        return
    unresolved = None
    try:
        sandbox = _item_sandbox(row, launch)
    except RuntimeError as exc:
        sandbox, unresolved = None, exc
    if sandbox is None and unresolved is None:
        return
    planted = _sandbox.planted_repos(worktree, row["base_ref"])
    if planted == []:
        return
    if unresolved is not None:
        raise unresolved
    if planted is None:
        raise RuntimeError(f"work item {row['id']} runs sandboxed, and git cannot read its index")
    raise RuntimeError(_sandbox.planted_refusal(f"work item {row['id']}", planted))


def refuse_live_sandboxed_session(db, row, launch: LaunchContext | None, *, what: str) -> None:
    """Raise `RuntimeError` when `row`'s item runs sandboxed and one of its
    sessions is still live: `what` -- the diff, the review package, the
    straggler sweep -- waits until it ends (Kraft-69rwp).

    The one guard for host git a sandboxed worker could race. Every other
    host git on the worktree runs between sessions, behind
    `refuse_planted_repos`; these can run while a co-task's container still
    writes the worktree, and even a git that never enters a gitlink parses
    the nested repository's config file to read its format. So while a
    session is live, no host git runs there at all. An unsandboxed item pays
    one database read and is never refused."""
    if not db.read(lambda c: store.live_session_ids(c, row["id"])):
        return
    if _item_sandbox(row, launch) is None:
        return
    raise RuntimeError(
        f"{what} is available once work item {row['id']}'s sandboxed session ends: "
        "host git does not read a sandboxed worktree while its worker can still write it"
    )
