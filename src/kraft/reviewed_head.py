"""The head a person's final review approved, and a merge that pushes nothing
else (`merge-refuses-a-head-final-review-did-not-see`).

Approving the chain's first `chain_finalized` gate pins the item's branch tips
(`tips`): every checkout a later merge could push, on the `gate_approved`
event. So does skipping that gate, which a person does explicitly, on its
`node_skipped`. Only Kraft's own rewrite of that exact head, with no agent and
no conflict resolved -- a clean rebase, the root's pointer bump to its merged
members -- moves the pin (`repin`, `reviewed_head_repinned`). Right before it
asks the forge to merge, the merge handler reads the tip it would push against
the pin (`unreviewed`): anything else is code no person approved, and it does
not merge.

No pin, no check: a chain with no final gate, or an item whose final gate was
approved before the pin existed.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from kraft import config as _config
from kraft import events, store
from kraft.vocab import ChainEvent, ForgeEvent, GateEvent, SessionEvent

#: A checkout's path -> its branch tip, None where git could not read it.
Tips = dict[str, str | None]

#: The events that carry a pin as `tips`; the newest one is the pin.
_PINNING = (GateEvent.APPROVED, ChainEvent.NODE_SKIPPED, ForgeEvent.REVIEWED_HEAD_REPINNED)


def branch_tip(checkout, branch: str) -> str | None:
    """`branch`'s tip in `checkout`, or None when git cannot read it:
    `refs/heads/<branch>`, what the merge pushes, never `HEAD`, which an agent
    may have moved off the branch (or, in place, a person's)."""
    return _config.git_read(
        Path(checkout), "rev-parse", f"refs/heads/{branch}", expected_failure=True
    )


def tips(db, work_item_id: str, row, worktree) -> Tips:
    """The item's branch tip in its own checkout and in every workspace member
    whose merge request has not merged: everything a later merge could push."""
    checkouts = [str(worktree)] + [
        r["path"]
        for r in db.read(lambda c: store.repos_for(c, work_item_id))
        if r["role"] == "submodule" and r["state"] != "merged"
    ]
    branch = store.branch_for(row)
    return {path: branch_tip(path, branch) for path in dict.fromkeys(checkouts)}


def unchanged(before: Mapping[str, str | None], after: Mapping[str, str | None]) -> bool:
    """Whether nothing a merge would push moved between two `tips` reads. A
    tip git could not read counts as moved: holding costs a review at most,
    going on could merge code nobody saw."""
    return before == after and None not in after.values()


def final_gate(row) -> str | None:
    """The id of `row`'s chain's first `chain_finalized` gate, or None."""
    from kraft.templates.models import GateNode  # store's reason: the schema is heavy

    chain = store.materialized_chain_of(row)
    if chain is None:
        return None
    return next(
        (
            n.id
            for n in chain.chain.nodes
            if isinstance(n.node, GateNode) and n.node.chain_finalized
        ),
        None,
    )


def pin_for(db, row, node_id: str | None, worktree) -> Tips | None:
    """The tips approving or skipping `node_id` pins: the item's own when it
    is its chain's final gate, None for any other node."""
    if node_id is None or node_id != final_gate(row):
        return None
    return tips(db, row["id"], row, worktree)


def started_over(c, work_item_id: str, session_id: str) -> Tips | None:
    """The tips an agent at the final gate recorded as it started (a reviewer,
    a reply, an escalation turn: `store.create_session(tips=...)`, on its
    `worker_session_created`). None when its session recorded none."""
    row = c.execute(
        "SELECT payload FROM events WHERE work_item_id = ? AND type = ? "
        "AND json_extract(payload, '$.session_id') = ? ORDER BY seq DESC LIMIT 1",
        (work_item_id, SessionEvent.WORKER_SESSION_CREATED, session_id),
    ).fetchone()
    return json.loads(row["payload"]).get("tips") if row is not None else None


def moved_under(db, work_item_id: str, session_ids, now: Mapping[str, str | None]) -> bool:
    """Whether an agent a person's approval or skip of the final gate just
    stopped (`session_ids`) may have moved what that decision would pin
    (`now`): its recorded start tips are not `now`, or it recorded none --
    an agent started before they were recorded is not known to have left the
    branch alone, so it counts as having moved it."""
    starts = db.read(lambda c: [started_over(c, work_item_id, sid) for sid in session_ids])
    return any(start is None or not unchanged(start, now) for start in starts)


def pinned(c, work_item_id: str) -> Tips | None:
    """The pin: the newest `tips` an approval or a skip of the final gate, or a
    repin, recorded. None when there is none."""
    row = c.execute(
        "SELECT payload FROM events WHERE work_item_id = ? "
        f"AND type IN ({', '.join('?' * len(_PINNING))}) "
        "AND json_extract(payload, '$.tips') IS NOT NULL ORDER BY seq DESC LIMIT 1",
        (work_item_id, *_PINNING),
    ).fetchone()
    return json.loads(row["payload"])["tips"] if row is not None else None


def same_checkout(a, b) -> bool:
    """Whether two checkout paths name one checkout: the pin is written by
    whoever approved, the merge reads by the folder the walk handed it."""
    return os.path.realpath(str(a)) == os.path.realpath(str(b))


def _entry(pin: Mapping[str, str | None], checkout) -> str | None:
    """The key `pin` holds `checkout` under, or None."""
    return next((k for k in pin if same_checkout(k, checkout)), None)


def unreviewed(db, work_item_id: str, checkout, branch: str) -> dict | None:
    """What a merge of `branch` in `checkout` would push that the final review
    did not approve: `{repo, reviewed, current}`, `reviewed` None for a
    checkout the pin does not hold. None when the tip is the pinned one, or
    nothing is pinned. A tip git cannot read is never the pinned one."""
    pin = db.read(lambda c: pinned(c, work_item_id))
    if pin is None:
        return None
    key = _entry(pin, checkout)
    reviewed = pin[key] if key is not None else None
    current = branch_tip(checkout, branch)
    if reviewed is not None and current == reviewed:
        return None
    return {"repo": str(checkout), "reviewed": reviewed, "current": current}


async def repin(
    db,
    work_item_id: str,
    before: Mapping[str, str | None],
    after: Mapping[str, str | None],
    *,
    cause: str,
) -> None:
    """Kraft rewrote the item's branch itself, with no agent and no conflict
    resolved, between two reads of its tips (`cause`: `rebase`, or
    `pointer_bump`): each checkout whose tip `before` was the pinned one is
    pinned at its tip `after`. A checkout whose tip was not the pinned one
    keeps its pin, so a change no person saw still fails the merge."""
    pin = db.read(lambda c: pinned(c, work_item_id))
    if pin is None:
        return
    moved: dict[str, dict] = {}
    for path, was in before.items():
        now = after.get(path)
        key = _entry(pin, path)
        if key is None or was is None or now is None or now == was or pin[key] != was:
            continue
        moved[key] = {"from": was, "to": now}
    if not moved:
        return
    new = {**pin, **{k: m["to"] for k, m in moved.items()}}
    await db.write(
        lambda c: events.append(
            c,
            work_item_id,
            ForgeEvent.REVIEWED_HEAD_REPINNED,
            {"tips": new, "moved": moved, "cause": cause},
        )
    )
