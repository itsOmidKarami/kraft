from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from kraft import events, store
from kraft import policy as _policy
from kraft.adapters import forge as _forge
from kraft.executor.context import RATE_LIMITED, WAITING
from kraft.store import _now as _now
from kraft.templates.models import ResolvedNode


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
    will ever select it: `ci_wait.tick` filters `status = 'waiting'`,
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


def budget_breach(db, work_item_id: str, budget: _policy.Budget) -> dict | None:
    """The breached cap, or None. Evaluated fresh: it is a query, not a counter.

    A breach refuses the *next* launch. It cannot stop a running agent — cost is
    only known once that agent's session has exited (`usage.read_envelope`) — so
    the overshoot is bounded by the cost of one task, not by the cap.
    """
    if budget.work_item_usd is None and budget.daily_usd is None:
        return None
    since = store.local_midnight_utc() if budget.daily_usd is not None else None
    item_usd, daily_usd = db.read(lambda c: store.budget_spend(c, work_item_id, since=since))
    if budget.work_item_usd is not None and item_usd >= budget.work_item_usd:
        return {"scope": "work_item", "spent_usd": item_usd, "cap_usd": budget.work_item_usd}
    if budget.daily_usd is not None and daily_usd >= budget.daily_usd:
        return {"scope": "daily", "spent_usd": daily_usd, "cap_usd": budget.daily_usd}
    return None


def budget_reason(breach: dict) -> str:
    where = "this work item" if breach["scope"] == "work_item" else "today, across every work item"
    return (
        f"budget cap reached: ${breach['spent_usd']:.2f} spent on {where}, "
        f"cap ${breach['cap_usd']:.2f}. Nothing new was started; a running agent "
        "was not interrupted."
    )


async def stop_for_budget(db, work_item_id: str, node: ResolvedNode, budget: _policy.Budget) -> str:
    # The fallback cannot fire in practice — sums only grow between the dispatch
    # that returned BUDGET and here — but a None would crash the escalation path
    # rather than stop the item, which is the wrong failure.
    breach = budget_breach(db, work_item_id, budget) or {
        "scope": "work_item",
        "spent_usd": 0.0,
        "cap_usd": 0.0,
    }
    reason = budget_reason(breach)
    await db.write(lambda c: store.mark_needs_human(c, work_item_id, node.id, reason, None, breach))
    return "needs_human"


def latest_rate_limit(db, work_item_id: str) -> dict | None:
    """The most recently appended `rate_limit_hit` event's payload, or None.

    Same reverse-scan idiom as `kraft.executor.dispatch.last_measurement`/
    `kraft.executor.dispatch.needs_context_question`: the event was just
    written by `_subprocess.run_task` in the same session this verdict came
    from, so the latest one is always the right one.
    """
    evts = db.read(lambda c: events.read_after(c, 0, work_item_id))
    for e in reversed(evts):
        if e["type"] == "rate_limit_hit":
            return e["payload"]
    return None


async def stop_for_rate_limit(db, work_item_id: str, node: ResolvedNode) -> str:
    info = latest_rate_limit(db, work_item_id) or {}
    retry_at = info.get("resets_at_iso") or _now()
    await db.write(lambda c: store.mark_rate_limited(c, work_item_id, node.id, retry_at))
    return RATE_LIMITED


async def stop_for_waiting(db, work_item_id: str, node: ResolvedNode) -> str:
    # Counter-driven backoff, growing the same way `poll_ci`'s in-loop sleep
    # grows today (`adapters/forge/ci.py`'s `DEFAULT_POLL_INTERVAL`/
    # `_MAX_POLL_INTERVAL`): doubling from the first wait, capped. The counter
    # is `ci_wait.py`'s (`ci_wait:<node_id>`) -- read, not bumped, here; the
    # poller bumps it on each re-entry, so a node waiting for the first time
    # (no row yet) starts at the base interval.
    row = db.read(lambda c: store.read_counter(c, work_item_id, f"ci_wait:{node.id}"))
    count = (row["count"] if row else 0) + 1
    interval = min(_forge.DEFAULT_POLL_INTERVAL * (2 ** (count - 1)), _forge._MAX_POLL_INTERVAL)
    retry_at = (datetime.fromisoformat(_now()) + timedelta(seconds=interval)).isoformat()
    await db.write(lambda c: store.mark_waiting(c, work_item_id, node.id, retry_at))
    return WAITING


async def stop_for_infra(db, work_item_id: str, node: ResolvedNode) -> str:
    """The persisted `ci_infra:<node_id>` retry cap is spent and the pipeline
    is still red for a reason that is the forge's fault, not the branch's
    code (Kraft-h81i, Kraft-s8ul) -- straight to needs_human, no fix cycle, no
    on_failure repair: neither can fix a runner.
    """
    info = db.read(lambda c: events.read_after(c, 0, work_item_id))
    reason = next(
        (e["payload"]["reason"] for e in reversed(info) if e["type"] == "ci_infra_exhausted"),
        "CI infrastructure failed and retrying it did not recover",
    )
    await db.write(lambda c: store.mark_needs_human(c, work_item_id, node.id, reason))
    return "needs_human"
