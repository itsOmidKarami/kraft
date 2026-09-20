"""Fire work items from policy.yaml's cron triggers (Kraft-859).

Off by default the same way intake.py's poller is: no `triggers` in
policy.yaml means no task, no timer, no tick. Every trigger files its item
`paused` -- an agent cannot start work here any more than it can from manual
intake; a triggered item still needs a human to resume it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from kraft import executor
from kraft.api import deps as api_deps
from kraft.policy import cron_due

logger = logging.getLogger(__name__)

#: Matches the tick's own resolution (cron is minute-grained); cheap enough
#: to run unconditionally like rate_limit_retry's poller.
_INTERVAL_S = 60


async def tick(app, *, now: datetime | None = None) -> list[str]:
    """One pass over `policy.triggers`. Returns the work item ids filed.

    `app.state.trigger_last_fired` maps trigger index to the last
    "YYYY-MM-DDTHH:MM" it fired for, so a tick landing twice in the same
    minute does not double-file. Reset on restart -- a missed minute is not
    an incident (spec, Kraft-7izl).
    """
    st = app.state
    pol = getattr(st, "policy", None)
    if pol is None:
        return []
    now = now or datetime.now(UTC)
    stamp = now.strftime("%Y-%m-%dT%H:%M")
    filed: list[str] = []
    for index, trig in enumerate(pol.triggers):
        if not cron_due(trig.cron, now):
            continue
        if st.trigger_last_fired.get(index) == stamp:
            continue
        st.trigger_last_fired[index] = stamp
        if getattr(st, "library", None) is None:
            # Not "unknown chain template": see `intake._start`. One bad file
            # makes every chain unresolvable, and naming the chain id sends the
            # operator to the wrong file.
            logger.warning(
                "trigger %d: the template library is invalid (%s), skipped",
                index,
                "; ".join(getattr(st, "invalid_library", None) or ["templates/library.yaml"]),
            )
            continue
        chain = api_deps.resolve_chain(st, trig.chain)
        if chain is None:
            logger.warning("trigger %d: unknown chain template %r, skipped", index, trig.chain)
            continue
        wid = await executor.intake(
            st.db,
            st.run_dirs,
            title=trig.title,
            description=trig.description,
            repo=trig.repo,
            chain=chain,
            effective_policy=getattr(st, "instance_policy", None),
            chain_template=trig.chain,
            status="paused",
        )
        filed.append(wid)
    return filed


async def poller(app) -> None:
    """`tick` on a fixed interval until cancelled."""
    while True:
        await asyncio.sleep(_INTERVAL_S)
        try:
            await tick(app)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- a bad tick must not end the poller
            logger.exception("trigger tick failed")
