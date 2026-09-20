"""Re-enter a node parked on a pipeline that has not settled.

`ci_poll` (`adapters/forge/run.py`) no longer blocks in-process for up to
`poll_timeout`: it makes one CI check, and a pending pipeline becomes a row
the scheduler owns -- `status = 'waiting'` plus `retry_at`, set by
`store.mark_waiting` (Kraft-ru98). This is the poller that owns that row:
structurally the same job `rate_limit_retry.py` does for a rate limit, ticking
on a fixed interval and relaunching through `executor.run(start_index=...)`,
the same path a manual retry uses.

Always on, for the same reason the rate-limit poller is: a work item parked on
a pipeline has to be woken by something, and that something cannot be the
coroutine that used to sit in the wait.
"""

from __future__ import annotations

import asyncio
import logging

from kraft import policy as policy_mod
from kraft import store
from kraft.adapters import forge as _forge
from kraft.store import _now as _now  # test seam for wall-clock checks

logger = logging.getLogger(__name__)

#: How often the poller checks for a due item. Same fixed cadence
#: `rate_limit_retry` uses -- no per-operator tuning knob for this.
_INTERVAL_S = 30

#: Fallback when `app.state.policy` is `None` outright (an unset or invalid
#: policy.yaml) -- the same shape `poll_timeout`'s old 1800s default carried,
#: with attempts high enough not to bind before the wall clock does.
_DEFAULT_CAP = policy_mod.Cap(attempts=1000, wall_clock_s=int(_forge.DEFAULT_POLL_TIMEOUT))


def _key(node_id: str) -> str:
    """Per-node, not per-item: mirrors `rate_limit_retry._key` for the same
    reason -- a node that waits, resolves, and later waits again on a
    *different* node must not spend the same budget the first node used.

    Duplicated in `store.counters.retry_after_cap`, which clears this same
    key on `/retry` (Kraft-cs4s) -- edit both together.
    """
    return f"ci_wait:{node_id}"


async def tick(app) -> list[str]:
    """One poll. Returns the work item ids re-entered, which is usually none."""
    st = app.state
    due = st.db.read(
        lambda c: c.execute(
            # `materialized_chain` as well as `chain_definition`: the row is
            # handed to `store.node_index`, which reads the V1 snapshot first
            # and cannot find it in a column the SELECT never fetched.
            "SELECT id, repo, current_node_id, current_step, chain_definition, "
            "materialized_chain FROM work_items "
            "WHERE status = 'waiting' AND retry_at <= ?",
            (_now(),),
        ).fetchall()
    )
    reentered: list[str] = []
    for row in due:
        if await _re_enter_one(app, row):
            reentered.append(row["id"])
    return reentered


async def _re_enter_one(app, row) -> bool:
    from kraft.api import deps
    from kraft.executor import stops  # deferred, same cycle as `executor` below

    st = app.state
    wid = row["id"]
    node_id = row["current_node_id"]
    cap = policy_mod.resolve_cap(st.policy, "ci_wait") if st.policy is not None else _DEFAULT_CAP

    # Bracketed from *before* the claim -- which happens inside the transaction
    # body below -- to the hand-off. A claim makes this item read `active`, and
    # this poller's own `tick` SELECT filters `status = 'waiting'`, so an exit
    # from here that neither spawns a walk nor moves the status again leaves the
    # item claimed and unowned, and *no later tick will ever select it again*.
    # The nested `def` is inside the bracket deliberately: its `return result`
    # is a value handed back to `db.write`, so `dev/check_claim_handoff.py`
    # would otherwise report it as an unprotected exit a human must adjudicate
    # every time it runs.
    async with stops.claimed_or_stopped(
        st.db,
        wid,
        node_id,
        reason="the CI wait poller re-entered this item but could not start a walk",
        handed_off=lambda: deps.task_is_live(app, wid),
    ):

        def _bump_and_reenter(c):
            # One transaction: the counter bump and the status flip back to
            # 'active' must land together, or a tick between them could re-select
            # this same row (Kraft-ppk9).
            result = store.bump_counter(c, wid, _key(node_id), cap)
            store.mark_reentered(c, wid)
            return result

        count, started_at, cap = await st.db.write(_bump_and_reenter)
        if policy_mod.check(count=count, started_at=started_at, cap=cap, now=_now()) == "breached":
            await st.db.write(
                lambda c: store.mark_needs_human(
                    c, wid, node_id, f"CI wait exceeded its cap after {count - 1} re-entry(ies)"
                )
            )
            return False
        # `store.node_index`, not `chain["nodes"]`: a V1 row's `chain_definition` is
        # `"{}"`, and this poller re-enters a node it already claimed -- a raise here
        # would leave the item waiting forever with nothing behind it. `None` means
        # this node is not in this item's chain at all, which is a stop, not a
        # restart at zero.
        start = store.node_index(row, node_id)
        if start is None:
            # `return False`, not a bare `return`: this function's contract is
            # "did I re-enter it". The bracket above turns the claim into a stop.
            logger.warning("ci_wait: %s has no node %r in its chain, not re-entering", wid, node_id)
            return False
        from kraft import executor  # deferred: avoids a kraft.api <-> kraft.executor import cycle

        # No steer: there is no agent to address here, and a note handed to a
        # forge node would sit unconsumed until the next agent launch -- a
        # different node's business.
        try:
            deps.spawn(
                app,
                wid,
                deps.guard(
                    st.db,
                    wid,
                    executor.run(
                        st.db,
                        st.run_dirs,
                        work_item_id=wid,
                        registry=st.registry,
                        bd_cwd=deps.bd_cwd(),
                        start_index=start,
                        start_step=row["current_step"],
                        policy=st.policy,
                        launch=deps.launch(st, row["repo"]),
                    ),
                ),
            )
        except deps.AlreadyRunning:
            logger.warning("ci-wait: %s already has a live walk, skipping this tick", wid)
            return False
        return True


async def poller(app) -> None:
    """`tick` on a fixed interval until cancelled."""
    while True:
        await asyncio.sleep(_INTERVAL_S)
        try:
            await tick(app)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- a bad tick must not end the poller
            logger.exception("ci-wait retry tick failed")
