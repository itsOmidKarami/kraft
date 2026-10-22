"""`kraft.worker.reattach`: an escalation turn that ended across a restart is
consumed from its own `escalation_message` on, as a live turn's consumer reads
it, so what that message carries -- the branch tips of a merge node the turn
ran on -- is compared before its self-retry is acted on."""

from __future__ import annotations

import pytest
from support import worktree as wtree
from support.item_walk import MERGE_POLICY, approve, commits_a_fix, merge_chain

from kraft import events, store
from kraft.executor import gates, walk
from kraft.worker import reattach


@pytest.mark.parametrize(
    ("commits", "start", "reopened"),
    [(True, 0, ["review"]), (False, 2, [])],
    ids=["committed", "changed-nothing"],
)
async def test_a_reattached_turn_on_a_merge_node_is_held_when_it_committed(
    item_on, database, run_dirs, repo, monkeypatch, commits, start, reopened
):
    """The server restarted while an escalation turn ran on `merge`, and the
    turn committed, then asked to retry `merge`. Read from past its own
    message, the tips it recorded were never compared, and the retry pushed
    and merged the commit with no person seeing it: it is held at `feedback`
    with `review` reopened, and the retry starts there. A turn that changed
    nothing retries `merge` as asked."""
    walk_calls = []

    async def fake_walk_run(database, run_dirs, **kw):
        walk_calls.append(kw)
        return "completed"

    async def fake_refresh(worktree, repo, branch, **_kw):
        return None

    monkeypatch.setattr("kraft.executor.walk.run", fake_walk_run)
    monkeypatch.setattr(gates._builtins, "refresh_worktree_base", fake_refresh)
    it = await item_on(merge_chain(on_merge_rejected=MERGE_POLICY))
    await approve(it, "review")
    await database.write(lambda c: store.enter_node(c, it.id, "merge"))
    await database.write(
        lambda c: store.mark_needs_human(c, it.id, "merge", "merge failed", kind="failed")
    )
    await wtree.ensure(database, run_dirs, repo)
    seen = walk.merge_node_tips(database, it.id, it.row(), it.worktree)
    message = {"session_id": "s1", "message": "help", "auto": True, "merge_node": seen}
    await database.write(lambda c: events.append(c, it.id, "escalation_message", message))
    if commits:
        await commits_a_fix(it)(it.row())
    retry = {"node_id": "merge", "key": None, "gate_key": None, "steer": "fixed it"}
    await database.write(lambda c: events.append(c, it.id, "work_item_self_retry_requested", retry))
    (run_dirs.results / "s1.json").write_text('{"status": "done"}')
    await it.session("s1", "escalation", running=(2_000_000_000, 123.0))

    _summary, adopted = await reattach.reattach(database, run_dirs, grace_retry_delay_s=0)
    for task in adopted.values():
        await task

    assert [w["start_index"] for w in walk_calls] == [start]
    assert [e["payload"]["gate"] for e in it.events("gate_reopened")] == reopened
