"""`on_merge_rejected`: a merge train or merge queue that drops the merge
request sends the `merge` node to its declared repair -- or, for a conflict
Kraft's own forced rebase already cleared, to no agent at all -- and the chain
rewinds to `restart_from` under its own cap, carrying a note of why."""

import pytest
from support.harness import IN_PLACE, commit_all, git, make_repo, v1_task, write
from support.item_walk import (
    MERGE_POLICY,
    MERGE_TASK,
    approve,
    commits_a_fix,
    merge_chain,
    merge_drops,
    records_merge_sessions,
    walk_once,
    walk_through_review,
)

from kraft import inplace, store
from kraft import policy as _policy
from kraft.executor import gates


def _starts(it):
    return [
        e["payload"]["node_id"]
        for e in it.events("node_started")
        if e["payload"]["node_id"] not in ("review", "signoff")
    ]


def _then_leaves_the_branch(it):
    """A repair that commits its fix, then checks out the head the train
    rejected: `HEAD` says nothing changed, the branch the merge pushes did."""
    commit = commits_a_fix(it)

    async def effect(row):
        git(it.worktree, "checkout", "-q", "--detach", "HEAD")
        rejected = git(it.worktree, "rev-parse", "HEAD")
        git(it.worktree, "checkout", "-q", store.branch_for(row))
        await commit(row)
        git(it.worktree, "checkout", "-q", "--detach", rejected)

    return effect


@pytest.mark.parametrize(
    "repair", [commits_a_fix, _then_leaves_the_branch], ids=["commits", "leaves-head-behind"]
)
async def test_a_code_drop_runs_the_repair_and_rewinds_with_gates_reopened(item_on, script, repair):
    """The repair committed code no gate saw: the span's approved `review`
    reopens, and the restarted walk is told why it went back. The branch the
    merge pushes is what is read, not `HEAD`: a repair that left `HEAD` on the
    rejected head still committed on the branch."""
    it = await item_on(merge_chain(on_merge_rejected=MERGE_POLICY))
    await approve(it, "review")
    merge_drops(it, script)
    script.effects["repair"] = repair(it)

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
    ("fields", "approved"),
    [
        ({}, ("review",)),
        ({"on_base_changed": {"restart_from": "feedback"}}, ("review",)),
        ({}, ("review", "signoff")),
    ],
    ids=["plain", "the-node-declares-on-base-changed", "two-gates"],
)
async def test_a_clean_conflict_rebase_rewinds_without_an_agent_and_reopens_gates(
    item_on, script, fields, approved
):
    """Kraft's forced rebase cleared the conflict, so no repair runs; the span's
    approved `review` still reopens, because the rewound span's own fix loops
    can commit before any gate sees the head again -- every approved gate in
    it, `two-gates` the older one too, though the newer approval came after it.
    The base that forced rebase moved is not a second, base-change restart."""
    it = await item_on(merge_chain(gates=approved, on_merge_rejected=MERGE_POLICY, **fields))
    for gate in approved:
        await approve(it, gate)
    merge_drops(it, script, "conflict", moves_base=True)

    assert await walk_once(it) == "awaiting_gate"
    assert [e["payload"] for e in it.events("gate_reopened")] == [
        {"gate": gate, "reason": "merge_rejected"} for gate in approved
    ]
    for gate in approved:
        assert gates.pending_gate(it.database, it.id) == gate
        await approve(it, gate)
        status = await walk_once(it)
    assert status == "completed"
    assert _starts(it) == ["feedback", "merge", "feedback", "merge"]
    assert script.calls == ["ci", "merge", "ci", "merge"]
    assert not it.events("node_recovery_started")
    assert len(it.events("merge_rejection_restart")) == 1
    assert not it.events("base_change_restart")


_NOTHING = (
    "the merge-rejection repair in node merge committed nothing: pipeline failed on the train"
)


async def _member(it, name: str, rank: int = 0):
    """A workspace member checkout on the item's branch, still to merge."""
    member = make_repo(it.run_dirs.base, name)
    git(member, "checkout", "-q", "-b", store.branch_for(it.row()))
    await it.database.write(
        lambda c: store.add_repo(
            c, work_item_id=it.id, repo_path=str(member), role="submodule", merge_rank=rank
        )
    )
    return member


_IN_LIB = _NOTHING.replace("nothing:", "nothing in {lib}:")


@pytest.mark.parametrize(
    ("cause", "rebased", "commits_in", "calls", "reason", "kind"),
    [
        ("code", True, None, ["ci", "merge", "repair"], _NOTHING, "failed"),
        ("conflict", False, None, ["ci", "merge", "repair"], _NOTHING, "conflict"),
        (
            None,
            True,
            None,
            ["ci", "merge"],
            "node merge reported merge_rejected but recorded no merge_queue_dropped",
            "failed",
        ),
        ("code", True, "root", ["ci", "merge", "repair"], _IN_LIB, "failed"),
        ("code", True, "other", ["ci", "merge", "repair"], _IN_LIB, "failed"),
    ],
    ids=["code", "unrebased-conflict", "no-drop-recorded", "a-members-drop", "another-member"],
)
async def test_a_repair_that_commits_nothing_stops_for_a_human(
    item_on, script, cause, rebased, commits_in, calls, reason, kind
):
    """No rewind on an unchanged head: the train would only reject it again.
    Nor on a drop with nothing recorded to tell a repair. A workspace member's
    drop is judged in that member's checkout: a commit in the root, or in
    another member still to merge, is not its fix, though it still holds the
    item for the gates -- left as it was, a retry would push and merge it."""
    it = await item_on(merge_chain(on_merge_rejected=MERGE_POLICY))
    await approve(it, "review")
    lib = await _member(it, "lib") if commits_in else None
    if commits_in == "root":
        script.effects["repair"] = commits_a_fix(it)
    elif commits_in == "other":
        script.effects["repair"] = commits_a_fix(it, await _member(it, "other", rank=1))
    merge_drops(it, script, cause, rebased=rebased, folder=lib, repo=lib)

    assert await walk_once(it) == "needs_human"
    assert script.calls == calls
    assert not it.events("merge_rejection_restart")
    assert it.events("work_item_needs_human")[-1]["payload"]["reason"] == reason.format(lib=lib)
    assert it.row()["stop_kind"] == kind
    # The repair committed where no gate saw it: held at `feedback` with
    # `review` reopened. Nothing changed: left as is.
    held = lib is not None
    assert [e["payload"] for e in it.events("gate_reopened")] == (
        [{"gate": "review", "reason": "merge_rejected"}] if held else []
    )
    assert it.row()["current_node_id"] == ("feedback" if held else "merge")
    if lib is not None:
        assert f"Its merge request is the one in {lib}; fix it there." in (
            script.steers["repair"][0].take()
        )


async def test_a_members_repair_rewinds_and_leaves_the_roots_base_ref(item_on, script):
    """A member's fix is committed in that member's checkout, and that is what
    rewinds; `base_ref` is the root's, so the member's base never becomes it."""
    it = await item_on(merge_chain(on_merge_rejected=MERGE_POLICY))
    await approve(it, "review")
    lib = await _member(it, "lib")
    write(lib, "lib.txt", "the member's own history\n")
    commit_all(lib, "a member is not a copy of the root")
    merge_drops(it, script, folder=lib, repo=lib)
    script.effects["repair"] = commits_a_fix(it, lib)
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
    it = await item_on(merge_chain(on_merge_rejected=MERGE_POLICY))
    await approve(it, "review")
    merge_drops(it, script, cause, times=5)
    script.effects["repair"] = commits_a_fix(it)
    policy = _policy.Policy(
        loops={"merge.on_merge_rejected": _policy.Cap(1, 604800)}, default=_policy.Cap(3, 3600)
    )

    assert await walk_through_review(it, policy) == "needs_human"
    assert script.calls.count("repair") == repairs
    assert len(it.events("merge_rejection_restart")) == 1
    reason = it.events("work_item_needs_human")[-1]["payload"]["reason"]
    assert reason == "merge.on_merge_rejected exhausted after 1 restart(s) from 'feedback'"
    assert it.row()["stop_kind"] == "cap"


async def test_merge_rejection_rewinds_never_take_the_policy_default(item_on, script):
    """The default's one-hour wall clock would run out while `final_review`
    waits for a person: an unset key gets three attempts over a week."""
    it = await item_on(merge_chain(on_merge_rejected=MERGE_POLICY))
    await approve(it, "review")
    merge_drops(it, script, "conflict", times=2)
    policy = _policy.Policy(loops={}, default=_policy.Cap(1, 3600))

    assert await walk_through_review(it, policy) == "completed"
    assert len(it.events("merge_rejection_restart")) == 2


_NEVER = "; Kraft never re-queues a merge request on its own"


@pytest.mark.parametrize(
    ("merge", "fields", "cause", "why"),
    [
        (MERGE_TASK, {}, "code", "the node declares no on_merge_rejected"),
        (
            {**MERGE_TASK, "on_failure": {"tasks": [v1_task("task_repair")]}},
            {"on_failure": {"tasks": [v1_task("node_repair")]}},
            "code",
            "the node declares no on_merge_rejected",
        ),
        (
            MERGE_TASK,
            {"on_merge_rejected": MERGE_POLICY},
            "other",
            "it was taken off by hand or for a reason Kraft cannot read",
        ),
    ],
    ids=["no-policy", "on-failure-is-no-drop-s-repair", "taken-off-by-hand"],
)
async def test_a_drop_nothing_may_repair_stops_promptly_for_a_person(
    item_on, script, merge, fields, cause, why
):
    """No `on_merge_rejected`, or a drop whose cause Kraft cannot read: the
    item stops at once -- no wait, no repair, no task or node recovery, no
    rewind -- and the stop is not one automatic escalation answers. An agent
    retrying `merge` could re-queue what a person took off, or merge its own
    commit unseen."""
    it = await item_on(merge_chain(merge, **fields))
    await approve(it, "review")
    merge_drops(it, script, cause)

    assert await walk_once(it) == "needs_human"
    assert script.calls == ["ci", "merge"]
    stops = it.events("work_item_needs_human")
    assert stops[-1]["payload"]["reason"] == (
        f"dropped from the merge train in node merge: pipeline failed on the train; {why}{_NEVER}"
    )
    assert it.row()["stop_kind"] == "failed"
    assert not gates.stuck_stop(stops)
    assert not it.events("external_wait_started")
    assert not it.events("node_recovery_started")
    assert not it.events("merge_rejection_restart")


_NODE_REPAIR = {"on_failure": {"tasks": [v1_task("node_repair")]}}


@pytest.mark.parametrize(
    "fields",
    [_NODE_REPAIR, {**_NODE_REPAIR, "fix_loop": {"tasks": [v1_task("fix")]}}],
    ids=["after-on-failure", "after-on-failure-in-a-fix-loop"],
)
async def test_a_drop_on_a_re_measure_stops_promptly_for_a_person(item_on, script, fields):
    """The merge failed, the node's `on_failure` ran, and its re-measure asked
    the forge again, which dropped it: the same prompt stop as a first drop --
    never a stuck stop an escalation's retry answers by re-queueing it, nor a
    paid fix cycle whose commit the next measure would merge unseen."""
    it = await item_on(merge_chain(**fields))
    await approve(it, "review")
    merge_drops(it, script)
    drop, merges = script.effects["merge"], [0]

    async def drops_the_second_time(row):
        merges[0] += 1
        if merges[0] == 2:
            await drop(row)

    script.effects["merge"] = drops_the_second_time
    script.plan["merge"] = ["failed", "merge_rejected", "done"]

    assert await walk_once(it) == "needs_human"
    assert script.calls == ["ci", "merge", "node_repair", "merge"]
    stops = it.events("work_item_needs_human")
    assert stops[-1]["payload"]["reason"] == (
        "dropped from the merge train in node merge: pipeline failed on the train; "
        f"the node declares no on_merge_rejected{_NEVER}"
    )
    assert not gates.stuck_stop(stops)


@pytest.mark.parametrize(
    ("effect", "status", "calls"),
    [
        (None, "completed", ["ci", "merge", "wait", "merge"]),
        (commits_a_fix, "needs_human", ["ci", "merge", "wait"]),
    ],
    ids=["merges-in-place", "a-commit-is-caught-and-held"],
)
async def test_a_read_only_on_failure_on_a_merge_node_retries_the_merge_in_place(
    item_on, script, effect, status, calls
):
    """A merge that failed for another reason than a drop goes to the node's
    read_only `on_failure`, and the merge is asked again in place: the head is
    the one `review` approved. A handler that commits all the same is stopped
    by the read_only check, and the item held at `restart_from` with `review`
    reopened, so no retry merges its commit unseen."""
    read_only = {"tasks": [v1_task("wait")], "read_only": True}
    it = await item_on(merge_chain(on_merge_rejected=MERGE_POLICY, on_failure=read_only))
    await approve(it, "review")
    script.plan["merge"] = ["failed", "done"]
    if effect is not None:
        script.effects["wait"] = effect(it)

    assert await walk_once(it) == status
    assert script.calls == calls
    reopened = [e["payload"] for e in it.events("gate_reopened")]
    if effect is None:
        assert reopened == []
        return
    assert it.events("work_item_needs_human")[-1]["payload"]["reason"] == (
        "merge.on_failure.main is read_only, but it changed the worktree: fix.txt"
    )
    assert reopened == [{"gate": "review", "reason": "read_only_violated"}]
    assert it.row()["current_node_id"] == "feedback"


@pytest.mark.parametrize(
    ("commits", "ends", "status", "calls"),
    [
        (True, "done", "awaiting_gate", ["ci", "merge", "esc", "ci"]),
        (True, "failed", "needs_human", ["ci", "merge", "esc"]),
        (False, "done", "completed", ["ci", "merge", "esc", "merge"]),
    ],
    ids=["commits-and-rewinds", "commits-and-fails", "changes-nothing"],
)
async def test_a_merge_node_s_escalation_never_merges_its_commit_in_place(
    item_on, script, commits, ends, status, calls
):
    """The merge failed for another reason than a drop and the node's
    `escalation` answered it. Rerunning the node would push and merge what it
    committed with no person seeing it: a clean finish that committed rewinds
    to `restart_from` instead, under the merge rejection's cap, with `review`
    reopened; one that committed and then failed is held there. Told so up
    front, not promised a rerun. With nothing committed the merge reruns."""
    escalation = v1_task("esc", kind="subprocess", command="true")
    it = await item_on(merge_chain(on_merge_rejected=MERGE_POLICY, escalation=escalation))
    await approve(it, "review")
    script.plan.update({"merge": ["failed", "done"], "esc": [ends]})
    if commits:
        script.effects["esc"] = commits_a_fix(it)

    assert await walk_once(it) == status
    assert script.calls == calls
    assert "the chain goes back to node feedback" in script.instructions["esc"][0]
    reopened = [e["payload"] for e in it.events("gate_reopened")]
    if not commits:
        assert reopened == []
        return
    assert reopened == [{"gate": "review", "reason": "escalation_committed"}]
    assert it.row()["current_node_id"] == ("review" if ends == "done" else "feedback")
    if ends == "done":
        [restart] = it.events("merge_rejection_restart")
        assert restart["payload"]["cause"] == "escalation"
        assert it.events("steer_undelivered")[-1]["payload"]["steer"].startswith(
            "Rewound to feedback because the escalation of node merge"
        )


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
    it = await item_on(
        merge_chain(on_merge_rejected=MERGE_POLICY), target=IN_PLACE if in_place else None
    )
    folder = it.repo if in_place else it.worktree
    await approve(it, "review")
    merge_drops(it, script, rebased=rebased, folder=folder)
    upstream = {}

    async def land_upstream(_row):
        if not upstream:
            upstream["sha"] = await _upstream_moves(it)

    async def repair(_row):
        if not rebased:
            git(folder, "rebase", upstream["sha"])
        await commits_a_fix(it, folder)(_row)

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
    it = await item_on(merge_chain(on_merge_rejected=MERGE_POLICY))
    await approve(it, "review")
    await it.database.write(lambda c: _landed(c, it))
    merge_drops(it, script)
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
    it = await item_on(merge_chain(on_merge_rejected=MERGE_POLICY))
    await approve(it, "review")
    merge_drops(it, script)
    records_merge_sessions(it, script)
    script.plan["repair"] = [ending]
    commit = commits_a_fix(it)

    async def commits_then_ends(_row):
        await commit(_row)
        if ending in ("needs_context", "failed"):  # a session's own ending, not a stop
            await it.session("s-repair", "merge.on_merge_rejected.repair.main.repair", ending)

    script.effects["repair"] = commits_then_ends
    spent = _policy.Cap(3, 3600)
    await it.database.write(lambda c: store.bump_counter(c, it.id, "feedback.escalation", spent))

    assert await walk_once(it) == result
    assert kind is None or it.row()["stop_kind"] == kind
    # The span runs again from scratch: a fresh budget, as after a rewind.
    assert it.database.read(lambda c: store.read_counter(c, it.id, "feedback.escalation")) is None
    assert [e["payload"] for e in it.events("gate_reopened")] == [
        {"gate": "review", "reason": "merge_rejected"}
    ]
    assert (it.row()["current_node_id"], it.row()["current_step"]) == ("feedback", 0)
    assert not it.events("merge_rejection_restart")
    if result == "paused":  # still active here: what a resume does next
        assert await walk_once(it) == "awaiting_gate"
        assert script.calls[-2:] == ["repair", "ci"]
        # Walked back through the gate, the drop is answered: the reviewed
        # head is queued, not sent to the repair again.
        await approve(it, "review")
        assert await walk_once(it) == "completed"
        assert script.calls[-3:] == ["repair", "ci", "merge"]


async def test_a_rewind_whose_clock_ran_out_during_the_repair_holds_the_item(
    item_on, script, monkeypatch
):
    """The cap is read before the repair, but its wall clock can run out while
    the repair works: the refused rewind still reopens `review` and stands the
    item at `feedback`, the repair's commit unseen by any gate."""
    from kraft.executor import walk

    it = await item_on(merge_chain(on_merge_rejected=MERGE_POLICY))
    await approve(it, "review")
    merge_drops(it, script, times=2)
    commit = commits_a_fix(it)
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

    assert await walk_through_review(it, policy) == "needs_human"
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
    it = await item_on(merge_chain(on_merge_rejected=MERGE_POLICY), target=IN_PLACE)
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
    merge_drops(it, script, rebased=False, folder=it.repo)

    assert await walk_once(it) == "needs_human"
    assert it.row()["stop_kind"] == "checkout"
    reason = it.events("work_item_needs_human")[-1]["payload"]["reason"]
    assert "would overwrite local.cfg, ignored here but tracked on main" in reason
    assert "repair" not in script.calls
    assert (it.repo / "local.cfg").read_text() == "the person's\n"
