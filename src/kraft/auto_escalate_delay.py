"""Fire a delayed `auto_escalate`/`auto_escalate_stuck` once its configured
wait has elapsed (docs/superpowers/specs/2026-09-12-auto-escalate-delay-and-preemption-design.md).

Both mechanisms already carry their own "has the delay elapsed" check
(`executor.gates.review_gates`/`auto_escalate_stuck`, Kraft-vyk8) and already
run inline, synchronously, right after every step in
`walk.run`/`resuming.resume` -- so at `auto_escalate_delay_s == 0` (the
default) that inline call always fires immediately, exactly as before this
module existed. This poller is the backstop for a delay greater than 0: the
item that inline call left alone because the delay hadn't elapsed yet has
nothing else watching it once that call returns, so something has to
re-check it once the delay passes. Same fixed-interval shape as
`rate_limit_retry.poller`/`waits.poller`.

Always on, like those two: a delay is a per-node config an operator opts
into, but once they have, nothing except this poller ever revisits an item
sitting past it -- there is no coroutine left waiting on it the way the
pre-poller inline call briefly was.
"""

from __future__ import annotations

import asyncio
import logging

from kraft import events, store
from kraft.executor import gates

logger = logging.getLogger(__name__)

#: Same fixed cadence `rate_limit_retry`/`waits` use -- no per-operator
#: tuning knob for this either.
_INTERVAL_S = 30


async def tick(app) -> list[str]:
    """One poll over every `needs_human` row -- the one DB status value both
    a pending gate and a genuine stuck stop share (`db.py`'s work_items CHECK
    constraint has no separate `awaiting_gate` value; `"awaiting_gate"` only
    ever exists as the in-memory status string `walk`/`resuming` hand
    `review_gates`, never as a column value). `gates.pending_gate` is what
    tells the two apart, the same door `review_gates`/`auto_escalate_stuck`
    already use for it: a row with a pending gate goes through
    `gates.review_gates` (seeded with the `"awaiting_gate"` status string,
    same as the inline caller), everything else through
    `gates.auto_escalate_stuck`. Both are safe to call unconditionally --
    each re-checks its own preconditions (pending gate, reason, cap, budget,
    and now elapsed delay) and returns its `status` unchanged if nothing is
    due, the same "a delayed re-check that finds the status already changed
    simply doesn't match its own scan" guarantee a human's own action
    already relies on.

    Skips a row with a live task already in `app.state.tasks`: that is
    either the original inline call from `run()`/`resume()` still in flight,
    or a previous tick's own dispatch that hasn't finished yet, and a second
    concurrent `review_gates`/`auto_escalate_stuck` call for the same item is
    exactly the failure both functions' own docstrings warn against. The row
    settles once that task's `deps.spawn` done-callback pops it, and the next
    tick (or the item's own resolution) picks it up from there.

    Returns the work item ids this tick spawned a check against, which is
    usually none -- most calls land on an item whose delay has not elapsed,
    or that a human already cleared, and that check returns its status
    unchanged.
    """
    from kraft.api import deps

    st = app.state
    attempted: list[str] = []
    # Same bound `intake.tick`/manual resume already enforce: a dispatch here
    # launches a real agent session, same as a resume does, so it must count
    # against the same `max_concurrent` slots rather than opening one per
    # `needs_human` row regardless of how many are already running (code
    # review finding) -- otherwise a backlog parked before this feature
    # existed launches all at once the first tick after a server start.
    #
    # `active_count` alone only bounds one tick: `escalate.dispatch` and
    # `gate_review.review` both leave the row `needs_human`/`awaiting_gate`
    # for the whole agent turn, so a session this poller spawned is invisible
    # to it -- the next tick's `active_count` read would not see it and would
    # hand out a fresh batch of slots on top of the ones still running (code
    # review finding). `app.state.tasks` is where every dispatch, from this
    # poller or any other, actually lives for as long as it runs (`deps.spawn`
    # populates it, its done-callback empties it), so union it with the
    # `active`-status rows rather than trust either alone: a row can be
    # `active` before its task lands in `st.tasks` (claim, then spawn) or
    # have a live task while its row is still `needs_human` (this poller's
    # own case), and `OR` over both catches whichever is true without
    # double-counting a row that happens to satisfy both.
    live = tuple(st.tasks)
    placeholders = ",".join("?" * len(live))
    busy = st.db.read(
        lambda c: c.execute(
            "SELECT COUNT(*) FROM work_items WHERE status = 'active'"
            + (f" OR id IN ({placeholders})" if live else ""),
            live,
        ).fetchone()[0]
    )
    slots = int(st.policy.max_concurrent if st.policy else 1) - busy
    if slots <= 0:
        return attempted
    # ORDER BY random() -- a row this poller can never resolve on its own (a
    # human-only gate, a needs_context stop, an already-capped run) stays
    # `needs_human` forever. Without a rotating scan order it would sit at a
    # stable rowid position at the head of every tick's results and eat every
    # slot below, starving rows behind it for good (code review finding).
    # Shuffling means a blocked row wins some ticks and loses others, so the
    # rest of the backlog still gets its turn.
    due = st.db.read(
        lambda c: c.execute(
            "SELECT * FROM work_items WHERE status = 'needs_human' ORDER BY RANDOM()"
        ).fetchall()
    )
    default_delay = int(st.policy.auto_escalate_delay_s if st.policy else 0)
    for row in due:
        if slots <= 0:
            break
        wid = row["id"]
        if wid in st.tasks:
            continue
        # Only rows that actually opted into a delay (code review finding). At
        # the default 0 the inline call from `run()`/`resume()` already fired
        # immediately and there is nothing left for a backstop to back-stop --
        # re-checking those here would hand a stuck item a *fresh*
        # `auto_escalate_stuck` turn every `_INTERVAL_S` (up to the cap) where
        # before this module it got one per `run()`/`resume()`. That is the
        # opposite of this feature's "zero regression at the default".
        if store.effective_auto_escalate_delay_s(row, default_delay) <= 0:
            continue
        evts = st.db.read(lambda c, wid=wid: events.read_after(c, 0, wid))
        gate = gates.pending_gate(st.db, wid, evts=evts)
        # Armed and actually due, checked here rather than left to the
        # spawned call (code review finding): an unarmed or already-handled
        # row can only return its status unchanged, and spawning it anyway
        # spends a `max_concurrent` slot the rows that *are* due needed, and
        # briefly installs a task under this wid that 409s a human's retry.
        try:
            if not gates.auto_check_due(row, gate, evts, st.policy):
                continue
        except LookupError:
            # A legacy row with a pending gate and a delay > 0: `auto_check_due`
            # walks the V1 chain and `walk.chain_of` raises for a row the legacy
            # intake path wrote. Per row, not per tick -- uncaught it aborted the
            # whole scan every `_INTERVAL_S` and starved every other due row
            # behind it. Not a compatibility path: nothing is retried against the
            # legacy shape, the row is simply skipped, which is what it already
            # gets from every other V1-converted door.
            continue
        status, fn = (
            ("awaiting_gate", gates.review_gates)
            if gate is not None
            else ("needs_human", gates.auto_escalate_stuck)
        )
        deps.spawn(
            app,
            wid,
            deps.guard(
                st.db,
                wid,
                fn(
                    status,
                    st.db,
                    st.run_dirs,
                    work_item_id=wid,
                    policy=st.policy,
                    launch=deps.launch(st, row["repo"]),
                    bd_cwd=deps.bd_cwd(),
                    on_approve=deps._on_approve(st),
                ),
            ),
        )
        attempted.append(wid)
        slots -= 1
    return attempted


async def poller(app) -> None:
    """`tick` on a fixed interval until cancelled."""
    while True:
        await asyncio.sleep(_INTERVAL_S)
        try:
            await tick(app)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- a bad tick must not end the poller
            logger.exception("auto-escalate delay tick failed")
