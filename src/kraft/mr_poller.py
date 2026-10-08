"""Notice a merge request closed on the forge without merging (B8).

Nothing inside Kraft watches for this: a human (or someone else's automation)
closing the MR on GitHub/GitLab leaves the item sitting `waiting` on a wait
that will never settle, or already `needs_human` for an unrelated reason,
with no door back except a human noticing the forge disagrees. This poller is
that notice -- same fixed-interval shape as `rate_limit_retry.poller`/
`waits.poller`, except its own cadence (`policy.forge_poll_s`, default 300s):
it calls `gh`/`glab` once per item per tick, where those read only the
database, so it cannot share their 10-30s interval without hammering the
forge on every tick for every item parked at an MR node.
"""

from __future__ import annotations

import asyncio
import logging

from kraft import store
from kraft.adapters import forge as forge_mod
from kraft.templates.models import ForgeTask
from kraft.vocab import MR_WATCHED
from kraft.vocab.sql import in_list

logger = logging.getLogger(__name__)

#: Used only when no policy loaded at all (an unset or invalid policy.yaml);
#: mirrors `Policy.forge_poll_s`'s own default.
_DEFAULT_INTERVAL_S = 300

REASON = "the merge request was closed on the forge without merging"


def _is_mr_node(node) -> bool:
    """Whether `node` (a `ResolvedNode`) takes the item's merge request: it
    runs at least one `kind: forge` task. Every `ForgeAction` target is a
    point on the merge-request lifecycle (`mr.open_draft` .. `mr.post_merge_
    ci`, `adapters.forge.run.V1_HANDLERS`), so this does not need to name
    `open_mr`/`mr_checks`/`merge` by their chain-authored node ids -- a
    custom chain's own names would not match those anyway."""
    return any(isinstance(t.task, ForgeTask) for t in node.tasks())


async def tick(app) -> list[str]:
    """One poll. Returns the work item ids stopped for a closed MR, usually
    none."""
    st = app.state
    rows = st.db.read(
        lambda c: c.execute(
            f"SELECT * FROM work_items WHERE status IN ({in_list(MR_WATCHED)}) "
            "AND archived_at IS NULL AND stop_kind IS NOT 'mr_closed'"
        ).fetchall()
    )
    stopped: list[str] = []
    errors: dict[str, str] = getattr(st, "mr_poll_errors", None)
    if errors is None:
        errors = st.mr_poll_errors = {}
    for row in rows:
        wid = row["id"]
        try:
            if await _check_one(st, row):
                stopped.append(wid)
            errors.pop(wid, None)
        except forge_mod.ForgeError as exc:
            # Logged once per item until the outcome changes (a closed MR, a
            # merge, the item moving on) -- not once per tick, which would be
            # one log line per item every `forge_poll_s` for as long as the
            # forge stays unreachable.
            if errors.get(wid) != str(exc):
                logger.warning("mr poller: %s: %s", wid, exc)
                errors[wid] = str(exc)
        except Exception:  # noqa: BLE001 -- one bad item must not stop the rest
            logger.exception("mr poller: %s: unexpected error", wid)
    return stopped


async def _check_one(st, row) -> bool:
    from kraft.api.routes import board  # deferred: kraft.api <-> kraft.mr_poller cycle
    from kraft.executor import walk  # deferred, same reason

    wid = row["id"]
    node_id = row["current_node_id"]
    try:
        chain = walk.chain_of(row)
    except LookupError:
        return False
    index = store.node_index(row, node_id)
    if index is None or not _is_mr_node(chain.chain.nodes[index]):
        return False
    ref = board._mr_ref(st, wid)
    if ref is None:
        return False
    state = await board._mr_state(st, row)
    if state != "closed":
        return False

    def write(c):
        # Re-checked in the writer's own transaction (`caps.stop_if_still_
        # parked`'s pattern): the item measured above may have moved --
        # resumed, retried, cancelled -- between that read and this write,
        # and a stale stop would clobber whatever it moved to.
        now = c.execute(
            "SELECT status, current_node_id FROM work_items WHERE id = ?", (wid,)
        ).fetchone()
        if now is None or now["status"] != row["status"] or now["current_node_id"] != node_id:
            return False
        store.mark_needs_human(
            c,
            wid,
            node_id,
            REASON,
            kind="mr_closed",
            facts={"ref": ref["number"], "url": ref["url"]},
        )
        return True

    return bool(await st.db.write(write))


async def poller(app) -> None:
    """`tick` on `policy.forge_poll_s` until cancelled."""
    while True:
        policy = app.state.policy
        await asyncio.sleep(policy.forge_poll_s if policy is not None else _DEFAULT_INTERVAL_S)
        try:
            await tick(app)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- a bad tick must not end the poller
            logger.exception("mr poller tick failed")
