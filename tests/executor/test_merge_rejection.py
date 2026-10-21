"""`on_merge_rejected`: a merge train or merge queue that drops the merge
request sends the `merge` node to its declared repair -- or, for a conflict
Kraft's own forced rebase already cleared, to no agent at all -- and the chain
rewinds to `restart_from` under its own cap, carrying a note of why."""

import pytest
from support.harness import IN_PLACE, commit_all, git, make_repo, v1_node, v1_task, write
from support.item_walk import approve, walk_once

from kraft import events, inplace, store
from kraft import policy as _policy
from kraft.vocab import ForgeEvent

_MERGE = v1_task("merge", kind="forge", target="mr.merge")
_POLICY = {"restart_from": "feedback", "repair": {"tasks": [v1_task("repair")]}}


def _chain(merge=_MERGE, **merge_fields):
    """feedback -> review (a gate) -> merge, which declares `merge_fields`."""
    return [
        v1_node("feedback", tasks=[v1_task("ci")]),
        v1_node("review", "gate"),
        v1_node("merge", tasks=[merge], **merge_fields),
    ]


def _starts(it):
    return [
        e["payload"]["node_id"]
        for e in it.events("node_started")
        if e["payload"]["node_id"] != "review"
    ]


def _drops(
    it, script, cause="code", *, rebased=True, times=1, folder=None, moves_base=False, repo=None
):
    """`merge` answers `merge_rejected` `times` times, then `done`, recording
    each drop as the forge's merge handler does, at the head it left in
    `folder` -- and, `moves_base`, the `base_ref` its forced rebase persists.
    `repo` is the dropped merge request's repository, the item's own unless
    given. No `cause` records nothing."""
    script.plan["merge"] = ["merge_rejected"] * times + ["done"]
    dropped = [0]

    async def effect(_row):
        if dropped[0] == times or cause is None:
            return
        dropped[0] += 1
        if moves_base:
            await it.database.write(lambda c: store.set_base_ref(c, it.id, f"{dropped[0]:040d}"))
        head = git(folder or it.worktree, "rev-parse", "HEAD")
        await it.database.write(
            lambda c: events.append(
                c,
                it.id,
                ForgeEvent.MERGE_QUEUE_DROPPED,
                {
                    "node_id": "merge",
                    "repo": str(repo or it.repo),
                    "kind": "train",
                    "cause": cause,
                    "reason": "pipeline failed on the train",
                    "url": "https://forge.example/p/1",
                    "failed_jobs": ["unit", "lint"],
                    "head_sha": head,
                    "rebased": rebased,
                },
            )
        )

    script.effects["merge"] = effect


async def _walk_through_review(it, policy=None):
    """Walk, approving `review` each time a rewind reopened it."""
    while (status := await walk_once(it, policy)) == "awaiting_gate":
        await approve(it, "review")
    return status


def _commits_a_fix(it, folder=None):
    async def effect(_row):
        write(folder or it.worktree, "fix.txt", "fixed\n")
        commit_all(folder or it.worktree, "fix the train failure")

    return effect


async def test_a_code_drop_runs_the_repair_and_rewinds_with_gates_reopened(item_on, script):
    """The repair committed code no gate saw: the span's approved `review`
    reopens, and the restarted walk is told why it went back."""
    it = await item_on(_chain(on_merge_rejected=_POLICY))
    await approve(it, "review")
    _drops(it, script)
    script.effects["repair"] = _commits_a_fix(it)

    assert await walk_once(it) == "awaiting_gate"
    [restart] = it.events("merge_rejection_restart")
    assert restart["payload"] == {
        "node_id": "merge",
        "restart_from": "feedback",
        "nodes": ["feedback", "merge"],
        "restart": 1,
    }
    [reopened] = it.events("gate_reopened")
    assert reopened["payload"] == {"gate": "review", "reason": "merge_rejected"}
    assert script.steers["ci"][1].source == "seeded"
    [undelivered] = it.events("steer_undelivered")
    assert undelivered["payload"]["steer"].startswith(
        "Rewound to feedback because the merge train or queue rejected the merge request:"
    )
    assert "pipeline failed on the train" in undelivered["payload"]["steer"]

    await approve(it, "review")
    assert await walk_once(it) == "completed"
    assert _starts(it) == ["feedback", "merge", "feedback", "merge"]
    assert script.calls == ["ci", "merge", "repair", "ci", "merge"]


@pytest.mark.parametrize(
    ("merge", "fields"),
    [
        (_MERGE, {}),
        ({**_MERGE, "on_failure": {"tasks": [v1_task("task_repair")]}}, {}),
        (_MERGE, {"on_base_changed": {"restart_from": "feedback"}}),
    ],
    ids=["plain", "the-task-declares-on-failure", "the-node-declares-on-base-changed"],
)
async def test_a_clean_conflict_rebase_rewinds_without_an_agent_and_reopens_gates(
    item_on, script, merge, fields
):
    """Kraft's forced rebase cleared the conflict, so no repair runs; the span's
    approved `review` still reopens, because the rewound span's own fix loops
    can commit before any gate sees the head again. The drop is
    `on_merge_rejected`'s alone: no task recovery spends itself on it, and the
    base that forced rebase moved is not a second, base-change restart."""
    it = await item_on(_chain(merge, on_merge_rejected=_POLICY, **fields))
    await approve(it, "review")
    _drops(it, script, "conflict", moves_base=True)

    assert await walk_once(it) == "awaiting_gate"
    assert [e["payload"] for e in it.events("gate_reopened")] == [
        {"gate": "review", "reason": "merge_rejected"}
    ]
    await approve(it, "review")
    assert await walk_once(it) == "completed"
    assert _starts(it) == ["feedback", "merge", "feedback", "merge"]
    assert script.calls == ["ci", "merge", "ci", "merge"]
    assert not it.events("node_recovery_started")
    assert len(it.events("merge_rejection_restart")) == 1
    assert not it.events("base_change_restart")


_NOTHING = (
    "the merge-rejection repair in node merge committed nothing: pipeline failed on the train"
)


@pytest.mark.parametrize(
    ("cause", "rebased", "member", "calls", "reason", "kind"),
    [
        ("code", True, False, ["ci", "merge", "repair"], _NOTHING, "failed"),
        ("conflict", False, False, ["ci", "merge", "repair"], _NOTHING, "conflict"),
        (
            None,
            True,
            False,
            ["ci", "merge"],
            "node merge reported merge_rejected but recorded no merge_queue_dropped",
            "failed",
        ),
        ("code", True, True, ["ci", "merge", "repair"], _NOTHING, "failed"),
    ],
    ids=["code", "unrebased-conflict", "no-drop-recorded", "a-members-drop"],
)
async def test_a_repair_that_commits_nothing_stops_for_a_human(
    item_on, script, cause, rebased, member, calls, reason, kind
):
    """No rewind on an unchanged head: the train would only reject it again.
    Nor on a drop with nothing recorded to tell a repair. A workspace member's
    drop is judged in that member's checkout: a commit in the root is not its
    fix."""
    it = await item_on(_chain(on_merge_rejected=_POLICY))
    await approve(it, "review")
    lib = make_repo(it.run_dirs.base, "lib") if member else None
    if lib is not None:
        await it.database.write(
            lambda c: store.add_repo(
                c, work_item_id=it.id, repo_path=str(lib), role="submodule", merge_rank=0
            )
        )
        script.effects["repair"] = _commits_a_fix(it)
    _drops(it, script, cause, rebased=rebased, folder=lib, repo=lib)

    assert await walk_once(it) == "needs_human"
    assert script.calls == calls
    assert not it.events("merge_rejection_restart")
    assert it.events("work_item_needs_human")[-1]["payload"]["reason"] == reason
    assert it.row()["stop_kind"] == kind
    if lib is not None:
        assert f"Its merge request is the one in {lib}; fix it there." in (
            script.steers["repair"][0].take()
        )


async def test_a_members_repair_rewinds_and_leaves_the_roots_base_ref(item_on, script):
    """A member's fix is committed in that member's checkout, and that is what
    rewinds; `base_ref` is the root's, so the member's base never becomes it."""
    it = await item_on(_chain(on_merge_rejected=_POLICY))
    await approve(it, "review")
    lib = make_repo(it.run_dirs.base, "lib")
    write(lib, "lib.txt", "the member's own history\n")
    commit_all(lib, "a member is not a copy of the root")
    await it.database.write(
        lambda c: store.add_repo(
            c, work_item_id=it.id, repo_path=str(lib), role="submodule", merge_rank=0
        )
    )
    _drops(it, script, folder=lib, repo=lib)
    script.effects["repair"] = _commits_a_fix(it, lib)
    before = {}

    async def record(_row):
        before.setdefault("base_ref", it.row()["base_ref"])

    script.effects["ci"] = record

    assert await walk_once(it) == "awaiting_gate"
    assert len(it.events("merge_rejection_restart")) == 1
    assert it.row()["base_ref"] == before["base_ref"]


@pytest.mark.parametrize(
    ("cause", "repairs"), [("conflict", 0), ("code", 1)], ids=["no-agent", "cap-after-repair"]
)
async def test_merge_rejection_rewinds_are_capped(item_on, script, cause, repairs):
    """Its own `<node>.on_merge_rejected` counter, which the span never
    clears: a train that rejects every pass stops for a person -- read before
    the repair, so no agent commits code a rewind the cap refuses would leave
    unseen."""
    it = await item_on(_chain(on_merge_rejected=_POLICY))
    await approve(it, "review")
    _drops(it, script, cause, times=5)
    script.effects["repair"] = _commits_a_fix(it)
    policy = _policy.Policy(
        loops={"merge.on_merge_rejected": _policy.Cap(1, 604800)}, default=_policy.Cap(3, 3600)
    )

    assert await _walk_through_review(it, policy) == "needs_human"
    assert script.calls.count("repair") == repairs
    assert len(it.events("merge_rejection_restart")) == 1
    reason = it.events("work_item_needs_human")[-1]["payload"]["reason"]
    assert reason == "merge.on_merge_rejected exhausted after 1 restart(s) from 'feedback'"
    assert it.row()["stop_kind"] == "cap"


async def test_merge_rejection_rewinds_never_take_the_policy_default(item_on, script):
    """The default's one-hour wall clock would run out while `final_review`
    waits for a person: an unset key gets three attempts over a week."""
    it = await item_on(_chain(on_merge_rejected=_POLICY))
    await approve(it, "review")
    _drops(it, script, "conflict", times=2)
    policy = _policy.Policy(loops={}, default=_policy.Cap(1, 3600))

    assert await _walk_through_review(it, policy) == "completed"
    assert len(it.events("merge_rejection_restart")) == 2


async def test_a_drop_without_the_policy_stops_promptly(item_on, script):
    """No `on_merge_rejected`: the drop is a task failure like any other, and
    the item stops at once with it -- no wait, no repair, no rewind."""
    it = await item_on(_chain())
    await approve(it, "review")
    _drops(it, script)

    assert await walk_once(it) == "needs_human"
    assert script.calls == ["ci", "merge"]
    reason = it.events("work_item_needs_human")[-1]["payload"]["reason"]
    assert reason.startswith("task failed in node merge: merge [forge]")
    assert it.row()["stop_kind"] == "failed"
    assert not it.events("external_wait_started")
    assert not it.events("node_recovery_started")


async def _upstream_moves(it) -> str:
    """A commit lands on the repo the worktree was cut from; its sha."""
    write(it.repo, "upstream.txt", "landed upstream\n")
    return commit_all(it.repo, "landed upstream")


#: What the repair is told, beyond the drop itself, by where it works and
#: whether Kraft's own rebase already brought the branch onto the target.
_TOLD = {
    "rebased": ([], ["First rebase onto origin/main", "This is a person's own checkout"]),
    "unrebased": (
        [
            "First rebase onto origin/main and resolve any conflict.",
            "Run the rebase (`git rebase origin/main`)",
        ],
        ["This is a person's own checkout", "--autostash", "Re-run"],
    ),
    "unrebased-in-place": (
        [
            "First rebase onto origin/main and resolve any conflict.",
            "This is a person's own checkout",
            "Run the rebase with `git -c rebase.updateRefs=false rebase --autostash origin/main`",
        ],
        ["`git rebase origin/main`", "Re-run"],
    ),
}


@pytest.mark.parametrize("where", list(_TOLD))
async def test_the_repair_is_told_what_the_train_rejected(item_on, script, where):
    """The drop's findings, the train's URL and each failed job; never to
    push or merge. A branch Kraft could not rebase is the repair's to rebase
    first, and a repair that lands it on the upstream tip moves `base_ref`."""
    in_place = where.endswith("in-place")
    rebased = where == "rebased"
    it = await item_on(_chain(on_merge_rejected=_POLICY), target=IN_PLACE if in_place else None)
    folder = it.repo if in_place else it.worktree
    await approve(it, "review")
    _drops(it, script, rebased=rebased, folder=folder)
    upstream = {}

    async def land_upstream(_row):
        if not upstream:
            upstream["sha"] = await _upstream_moves(it)

    async def repair(_row):
        if not rebased:
            git(folder, "rebase", upstream["sha"])
        await _commits_a_fix(it, folder)(_row)

    script.effects.update({"ci": land_upstream, "repair": repair})

    assert await walk_once(it) == "awaiting_gate"
    note = script.steers["repair"][0].take()
    for words in (
        "A merge train or merge queue rejected this merge request.",
        "pipeline failed on the train",
        "https://forge.example/p/1",
        "- unit\n- lint",
        "Commit your fix on this branch. Do not push and do not merge: Kraft does both.",
    ):
        assert words in note
    present, absent = _TOLD[where]
    assert [w for w in present if w not in note] == []
    assert [w for w in absent if w in note] == []
    assert "already landed" not in note
    if not rebased:
        assert it.row()["base_ref"] == upstream["sha"]


async def test_a_fix_needed_in_a_landed_member_stops_with_its_question(item_on, script):
    """A multi-repo item whose other target already merged: the repair is told
    it cannot change there, and its question is the stop."""
    it = await item_on(_chain(on_merge_rejected=_POLICY))
    await approve(it, "review")
    await it.database.write(lambda c: _landed(c, it))
    _drops(it, script)
    script.plan["repair"] = ["needs_context"]

    async def asks(_row):
        await it.session("s-repair", "merge.on_merge_rejected.repair.main.repair", "needs_context")

    script.effects["repair"] = asks

    assert await walk_once(it) == "needs_human"
    note = script.steers["repair"][0].take()
    assert "Already merged: /repos/lib. " in note
    assert (
        "These have already landed and cannot change here; if the fix belongs in one of "
        "them, report needs_context." in note
    )
    reason = it.events("work_item_needs_human")[-1]["payload"]["reason"]
    assert reason.startswith("needs_context: ")
    assert it.row()["stop_kind"] == "question"
    assert not it.events("merge_rejection_restart")


@pytest.mark.parametrize(
    ("ending", "result", "kind"),
    [
        ("needs_context", "needs_human", "question"),
        ("failed", "needs_human", "failed"),
        ("paused", "paused", None),
        ("budget", "needs_human", "budget"),
    ],
    ids=["question-after-commit", "failed-after-commit", "paused-after-commit", "budget"],
)
async def test_a_stop_after_the_repair_committed_holds_the_item_at_restart_from(
    item_on, script, ending, result, kind
):
    """The repair committed, then did not finish: whatever walks the item next
    must go back through the span, never merge its commit unseen. The approved
    `review` reopens and the item stands at `feedback`, not at `merge`."""
    it = await item_on(_chain(on_merge_rejected=_POLICY))
    await approve(it, "review")
    _drops(it, script)
    script.plan["repair"] = [ending]
    commit = _commits_a_fix(it)

    async def commits_then_ends(_row):
        await commit(_row)
        if ending in ("needs_context", "failed"):  # a session's own ending, not a stop
            await it.session("s-repair", "merge.on_merge_rejected.repair.main.repair", ending)

    script.effects["repair"] = commits_then_ends

    assert await walk_once(it) == result
    assert kind is None or it.row()["stop_kind"] == kind
    assert [e["payload"] for e in it.events("gate_reopened")] == [
        {"gate": "review", "reason": "merge_rejected"}
    ]
    assert (it.row()["current_node_id"], it.row()["current_step"]) == ("feedback", 0)
    assert not it.events("merge_rejection_restart")
    if result == "paused":  # still active here: what a resume does next
        assert await walk_once(it) == "awaiting_gate"
        assert script.calls[-2:] == ["repair", "ci"]


async def test_a_rewind_whose_clock_ran_out_during_the_repair_holds_the_item(
    item_on, script, monkeypatch
):
    """The cap is read before the repair, but its wall clock can run out while
    the repair works: the refused rewind still reopens `review` and stands the
    item at `feedback`, the repair's commit unseen by any gate."""
    from kraft.executor import walk

    it = await item_on(_chain(on_merge_rejected=_POLICY))
    await approve(it, "review")
    _drops(it, script, times=2)
    commit = _commits_a_fix(it)
    late = []

    async def repair(_row):
        await commit(_row)
        if script.calls.count("repair") == 2:
            late.append(True)

    script.effects["repair"] = repair
    real_now = walk._now
    monkeypatch.setattr(walk, "_now", lambda: "2999-01-01T00:00:00+00:00" if late else real_now())
    policy = _policy.Policy(
        loops={"merge.on_merge_rejected": _policy.Cap(3, 3600)}, default=_policy.Cap(3, 3600)
    )

    assert await _walk_through_review(it, policy) == "needs_human"
    assert it.row()["stop_kind"] == "cap"
    assert script.calls.count("repair") == 2
    assert len(it.events("merge_rejection_restart")) == 1
    assert len(it.events("gate_reopened")) == 2
    assert it.row()["current_node_id"] == "feedback"


def _landed(c, it):
    """`/repos/lib` already merged; the item's own repository not yet."""
    lib = store.add_repo(
        c, work_item_id=it.id, repo_path="/repos/lib", role="submodule", merge_rank=0
    )
    store.update_repo_state(c, lib, merge_state="merged")
    store.add_repo(c, work_item_id=it.id, repo_path=str(it.repo), role="root", merge_rank=1)


async def test_an_in_place_repair_is_not_sent_to_rebase_over_an_ignored_file(item_on, script):
    """A repair told to rebase first does it in the person's checkout, where
    git would replace a file they ignore but the newer base tracks: the item
    stops as `checkout` before the repair runs, the file untouched -- the
    guard the conflict resolver has."""
    it = await item_on(_chain(on_merge_rejected=_POLICY), target=IN_PLACE)
    await inplace.prepare(it.database, it.run_dirs, it.row(), base="main")
    git(it.repo, "checkout", "-q", store.branch_for(it.row()))  # where the take leaves it
    upstream = it.run_dirs.base / "upstream"
    git(it.repo, "worktree", "add", "-q", str(upstream), "main")
    write(upstream, "local.cfg", "upstream's\n")
    git(upstream, "add", "-f", "local.cfg")
    git(upstream, "commit", "-q", "-m", "main tracks local.cfg")
    git(it.repo, "worktree", "remove", str(upstream))
    (it.repo / ".git/info/exclude").write_text("local.cfg\n")
    write(it.repo, "local.cfg", "the person's\n")
    await approve(it, "review")
    _drops(it, script, rebased=False, folder=it.repo)

    assert await walk_once(it) == "needs_human"
    assert it.row()["stop_kind"] == "checkout"
    reason = it.events("work_item_needs_human")[-1]["payload"]["reason"]
    assert "would overwrite local.cfg, ignored here but tracked on main" in reason
    assert "repair" not in script.calls
    assert (it.repo / "local.cfg").read_text() == "the person's\n"
