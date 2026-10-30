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

import hashlib
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


def token(tips: Mapping[str, str | None]) -> str | None:
    """What a person's final-review decision names as the head they were shown:
    the sha itself for one checkout, so it can be recognised, and a
    `sha256:` digest over every checkout's realpath and tip for several (a
    workspace with unmerged members). None when a tip could not be read: there
    is nothing a decision could name. Clients treat it as opaque."""
    if not tips or None in tips.values():
        return None
    if len(tips) == 1:
        return next(iter(tips.values()))
    lines = sorted(f"{os.path.realpath(path)}\0{sha}" for path, sha in tips.items())
    return "sha256:" + hashlib.sha256("\n".join(lines).encode()).hexdigest()


def review_head(db, row, gate: str | None, worktree) -> str | None:
    """The token a decision on `gate` must carry: that of the tips it would pin
    (`pin_for`), so None unless `gate` is the chain's final one."""
    pin = pin_for(db, row, gate, worktree)
    return token(pin) if pin is not None else None


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


def reviewed_tip(db, work_item_id: str, checkout) -> str | None:
    """The tip the final review approved for `checkout`, None when nothing is
    pinned or the pin holds no tip for it."""
    pin = db.read(lambda c: pinned(c, work_item_id))
    key = _entry(pin, checkout) if pin is not None else None
    return pin[key] if key is not None else None


async def bring_home(checkout, branch: str, *, in_place: bool) -> bool:
    """Bring the commits the forge holds for `branch` into `checkout`'s own
    branch, so the final review that comes round again shows them. True when it
    moved the branch.

    The forge's tip is fetched into `FETCH_HEAD` only (`--refmap=` keeps the
    shared `refs/remotes/origin/<branch>` alone, which `git.push` does not trust
    -- Kraft-m7ppj), and the branch is fast-forwarded only when all hold:
    `FETCH_HEAD` is a fast-forward of it, the checkout's HEAD is that branch (an
    agent may have left it elsewhere), and no tracked file is modified. Then
    `merge --ff-only` moves the working tree with the ref. Otherwise -- a
    force-push, local commits the forge lacks, a checkout in use -- nothing is
    touched and False says so. An in-place checkout is the person's own, and is
    never touched, nor even fetched into."""
    if in_place:
        return False
    from kraft.adapters.forge import git  # imports this module through `run`
    from kraft.adapters.forge.models import ForgeError
    from kraft.worker import sandbox

    checkout, ref = Path(checkout), f"refs/heads/{branch}"
    try:
        await git.run_git(
            checkout,
            ["git", "fetch", "--refmap=", "origin", branch],
            env=sandbox.hardened_git_env(),
        )
    except ForgeError:
        return False
    fetched = _config.git_read(
        checkout, "rev-parse", "--verify", "FETCH_HEAD", expected_failure=True
    )
    before = branch_tip(checkout, branch)
    if (
        not fetched
        or fetched == before
        or _config.git_read(
            checkout, "merge-base", "--is-ancestor", ref, fetched, expected_failure=True
        )
        != ""
        or _config.git_read(checkout, "symbolic-ref", "-q", "HEAD", expected_failure=True) != ref
        or _config.git_read(checkout, "status", "--porcelain", "--untracked-files=no") != ""
    ):
        return False
    # Raised, not False: a planted .lfsconfig is for a person, not a quiet skip.
    git.assert_lfsconfig_unmoved(checkout, "bring the forge's commits in")
    try:
        await git.run_git(checkout, ["git", "merge", "--ff-only", fetched])
    except ForgeError:
        return False
    return branch_tip(checkout, branch) == fetched


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
