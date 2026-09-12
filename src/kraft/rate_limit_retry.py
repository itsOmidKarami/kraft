"""Auto-relaunch a work item once its rate-limit wait is over.

Always on, unlike `intake.py`'s poller: waiting out a rate limit is not
optional behaviour a human opts into, it is what the rest of this feature
promises. Ticks on a fixed interval and relaunches through the exact code
path `POST /work-items/{id}/retry` uses -- `store.retry_after_cap` plus
`executor.run(start_index=...)` -- so a rate-limited item and a manually
retried one are put back to work the same way.
"""

from __future__ import annotations

import asyncio
import json
import logging

from kraft import policy as policy_mod
from kraft import store
from kraft.store import _now as _now  # test seam for wall-clock checks

logger = logging.getLogger(__name__)

RESUME_PROMPT = (
    "You were working on this task when the agent hit an API rate limit and "
    "stopped. That limit has now reset — continue the work from where you "
    "left off."
)

#: How often the poller checks for a due item. Fixed, unlike auto-intake's
#: `interval_s`: there is no per-operator tuning knob for this, only a fixed
#: cadence cheap enough to run unconditionally (spec "Non-goals").
_INTERVAL_S = 30

#: `bump_counter`'s `cap_wall_s` column is NOT NULL, but a rate-limit wait can
#: legitimately run for hours -- ponytail: a wall-clock cap this large is
#: effectively "no wall-clock limit"; only `count` is ever checked below.
_NO_WALL_CAP = 10**9

#: `Policy.rate_limit_retries` defaults to 5; mirrored here for the one caller
#: whose `app.state.policy` is `None` outright (an unset or invalid policy.yaml).
_DEFAULT_RETRIES = 5


def _key(node_id: str) -> str:
    """Per-node, not per-item: a node that gets rate-limited, recovers, and
    later gets rate-limited again on a *different* node must not spend the
    same budget the first node already used up."""
    return f"rate_limit:{node_id}"


async def tick(app) -> list[str]:
    """One poll. Returns the work item ids relaunched, which is usually none."""
    st = app.state
    due = st.db.read(
        lambda c: c.execute(
            "SELECT id, repo, current_node_id, chain_definition FROM work_items "
            "WHERE status = 'rate_limited' AND retry_at <= ?",
            (_now(),),
        ).fetchall()
    )
    relaunched: list[str] = []
    for row in due:
        if await _retry_one(app, row):
            relaunched.append(row["id"])
    return relaunched


async def _retry_one(app, row) -> bool:
    from kraft.api import deps

    st = app.state
    wid = row["id"]
    node_id = row["current_node_id"]
    retries = st.policy.rate_limit_retries if st.policy is not None else _DEFAULT_RETRIES
    cap = policy_mod.Cap(attempts=retries, wall_clock_s=_NO_WALL_CAP)
    count, _started_at, cap = await st.db.write(
        lambda c: store.bump_counter(c, wid, _key(node_id), cap)
    )
    if count > cap.attempts:
        await st.db.write(
            lambda c: store.mark_needs_human(
                c, wid, node_id, f"rate_limit retries exhausted after {count - 1} attempt(s)"
            )
        )
        return False

    claimed = await st.db.write(
        lambda c: store.claim_for_run(c, wid, from_statuses=["rate_limited"])
    )
    if not claimed:
        # Something else (a human abandoning it, most plausibly) already moved
        # this item off `rate_limited` between the `due` SELECT and here.
        # `retry_after_cap` no longer flips status itself -- without this
        # check the poller would blindly claw an abandoned item back to
        # 'active' and spawn a walk into a worktree that may already be gone.
        logger.info("rate-limit retry: %s is no longer rate_limited, skipping", wid)
        return False

    await st.db.write(lambda c: store.retry_after_cap(c, wid, node_id, None, RESUME_PROMPT))
    chain = json.loads(row["chain_definition"])
    start = next(i for i, n in enumerate(chain["nodes"]) if n["id"] == node_id)
    from kraft import executor  # deferred: avoids a kraft.api <-> kraft.executor import cycle

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
                    policy=st.policy,
                    steer=RESUME_PROMPT,
                    launch=deps.launch(st, row["repo"]),
                ),
            ),
        )
    except deps.AlreadyRunning:
        logger.warning("rate-limit retry: %s already has a live walk, skipping", wid)
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
            logger.exception("rate-limit retry tick failed")
