"""`on_merge_rejected`: a merge train or merge queue that drops the merge
request sends the `merge` node to its declared repair -- or, for a conflict
Kraft's own forced rebase already cleared, to no agent at all -- and the chain
rewinds to `restart_from` under its own cap, carrying a note of why."""

import pytest
from support.harness import IN_PLACE, commit_all, git, make_repo, v1_node, v1_task, write
from support.item_walk import NO_SETUP, approve, commits_a_fix, merge_drops, walk_once

from kraft import executor, inplace, node_runs, store
from kraft import policy as _policy
from kraft.executor import gates

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


async def _walk_through_review(it, policy=None):
    """Walk, approving `review` each time a rewind reopened it."""
    while (status := await walk_once(it, policy)) == "awaiting_gate":
        await approve(it, "review")
    return status


async def test_a_code_drop_runs_the_repair_and_rewinds_with_gates_reopened(item_on, script):
    """The repair committed code no gate saw: the span's approved `review`
    reopens, and the restarted walk is told why it went back."""
    it = await item_on(_chain(on_merge_rejected=_POLICY))
    await approve(it, "review")
    merge_drops(it, script)
    script.effects["repair"] = commits_a_fix(it)

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
    merge_drops(it, script, "conflict", moves_base=True)

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
    fix, though it still holds the item for the gates."""
    it = await item_on(_chain(on_merge_rejected=_POLICY))
    await approve(it, "review")
    lib = make_repo(it.run_dirs.base, "lib") if member else None
    if lib is not None:
        await it.database.write(
            lambda c: store.add_repo(
                c, work_item_id=it.id, repo_path=str(lib), role="submodule", merge_rank=0
            )
        )
        script.effects["repair"] = commits_a_fix(it)
    merge_drops(it, script, cause, rebased=rebased, folder=lib, repo=lib)

    assert await walk_once(it) == "needs_human"
    assert script.calls == calls
    assert not it.events("merge_rejection_restart")
    assert it.events("work_item_needs_human")[-1]["payload"]["reason"] == reason
    assert it.row()["stop_kind"] == kind
    # The member's repair committed in the root, which no gate saw either:
    # held at `feedback` with `review` reopened. Nothing changed: left as is.
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
    it = await item_on(_chain(on_merge_rejected=_POLICY))
    await approve(it, "review")
    merge_drops(it, script, cause, times=5)
    script.effects["repair"] = commits_a_fix(it)
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
    merge_drops(it, script, "conflict", times=2)
    policy = _policy.Policy(loops={}, default=_policy.Cap(1, 3600))

    assert await _walk_through_review(it, policy) == "completed"
    assert len(it.events("merge_rejection_restart")) == 2


_NEVER = "; Kraft never re-queues a merge request on its own"


@pytest.mark.parametrize(
    ("merge", "fields", "cause", "why"),
    [
        (_MERGE, {}, "code", "the node declares no on_merge_rejected"),
        (
            {**_MERGE, "on_failure": {"tasks": [v1_task("task_repair")]}},
            {"on_failure": {"tasks": [v1_task("node_repair")]}},
            "code",
            "the node declares no on_merge_rejected",
        ),
        (
            _MERGE,
            {"on_merge_rejected": _POLICY},
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
    it = await item_on(_chain(merge, **fields))
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
    it = await item_on(_chain(on_merge_rejected=_POLICY))
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
    it = await item_on(_chain(on_merge_rejected=_POLICY))
    await approve(it, "review")
    merge_drops(it, script)
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


async def test_a_rewind_whose_clock_ran_out_during_the_repair_holds_the_item(
    item_on, script, monkeypatch
):
    """The cap is read before the repair, but its wall clock can run out while
    the repair works: the refused rewind still reopens `review` and stands the
    item at `feedback`, the repair's commit unseen by any gate."""
    from kraft.executor import walk

    it = await item_on(_chain(on_merge_rejected=_POLICY))
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
    merge_drops(it, script, rebased=False, folder=it.repo)

    assert await walk_once(it) == "needs_human"
    assert it.row()["stop_kind"] == "checkout"
    reason = it.events("work_item_needs_human")[-1]["payload"]["reason"]
    assert "would overwrite local.cfg, ignored here but tracked on main" in reason
    assert "repair" not in script.calls
    assert (it.repo / "local.cfg").read_text() == "the person's\n"


class _Crash(BaseException):
    """The daemon dying mid-walk: nothing after it runs, nothing catches it."""


def _crash_after(monkeypatch, module, name: str, *, before: bool = False, when=None) -> None:
    """The daemon dies once, right after (or `before`) `module.name` runs --
    on the first call `when(*args)` accepts, if given."""
    real = getattr(module, name)

    async def crashing(*args, **kwargs):
        if when is not None and not when(*args):
            return await real(*args, **kwargs)
        monkeypatch.setattr(module, name, real)
        if not before:
            await real(*args, **kwargs)
        raise _Crash

    monkeypatch.setattr(module, name, crashing)


def _records_merge_sessions(it, script) -> None:
    """`merge`'s own session, as `run_task` records it: what a resume reads."""
    drops, n = script.effects["merge"], [0]

    async def effect(row):
        await drops(row)
        n[0] += 1
        status = script.plan["merge"][0] if len(script.plan["merge"]) > 1 else "done"
        await it.session(f"s-merge-{n[0]}", "merge.main.merge", status)

    script.effects["merge"] = effect


async def _retry_where_it_stands(it):
    """A person's `kraft item retry ID`: its default target, the node the
    item stands on (`lifecycle._retry_target`), once the item is claimed."""
    from kraft.executor import walk
    from kraft.templates.forks import ChainPath

    row = it.row()
    target = ChainPath.parse(walk.chain_of(row), row["current_node_id"])
    await it.database.write(
        lambda c: c.execute("UPDATE work_items SET status = 'active' WHERE id = ?", (it.id,))
    )
    return await executor.retry(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        target=target,
        by_person=True,
        policy=_policy.Policy(loops={}, default=_policy.Cap(3, 3600)),
        launch=executor.LaunchContext(repo_entry=NO_SETUP),
    )


@pytest.mark.parametrize(
    ("crash", "commits", "held"),
    [
        (("dispatch", "run_recovery", False), True, True),
        (("dispatch", "run_recovery", False), False, False),
        (("walk", "_reopen_gates", True), True, True),
        (("walk", "_clear_span_counters", True), True, True),
    ],
    ids=[
        "during-the-repair-after-it-committed",
        "during-the-repair-before-any-commit",
        "restarting-before-the-gates-reopened",
        "restarting-after-the-gates-reopened",
    ],
)
async def test_a_daemon_restart_mid_rejection_never_merges_the_repair_unseen(
    item_on, script, monkeypatch, crash, commits, held
):
    """The daemon dies while the repair works, or partway through the rewind's
    writes, the item still standing on `merge`. A resume must not stop it
    there: a retry's default target would push the repair's commit and merge
    it with no gate seeing it. It holds the item at `feedback` with `review`
    reopened instead, so the retry walks back through the gate. A repair that
    had committed nothing leaves nothing unseen: the item stops at `merge`.
    Neither stop is one automatic escalation answers."""
    from kraft.executor import dispatch, walk

    it = await item_on(_chain(on_merge_rejected=_POLICY))
    await approve(it, "review")
    merge_drops(it, script)
    _records_merge_sessions(it, script)
    if commits:
        script.effects["repair"] = commits_a_fix(it)
    module, name, before = crash
    _crash_after(monkeypatch, {"dispatch": dispatch, "walk": walk}[module], name, before=before)

    with pytest.raises(_Crash):
        await walk_once(it)
    assert it.row()["current_node_id"] == "merge"
    status = await executor.resume_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        adopted={},
        policy=_policy.Policy(loops={}, default=_policy.Cap(3, 3600)),
        launch=executor.LaunchContext(repo_entry=NO_SETUP),
    )

    assert status == "needs_human"
    assert it.row()["current_node_id"] == ("feedback" if held else "merge")
    assert [e["payload"] for e in it.events("gate_reopened")] == (
        [{"gate": "review", "reason": "merge_rejected"}] if held else []
    )
    stops = it.events("work_item_needs_human")
    assert stops[-1]["payload"]["reason"].startswith(
        "resume: the merge-rejection repair in node merge did not finish"
    )
    assert not gates.stuck_stop(stops)
    if held:
        assert await _retry_where_it_stands(it) == "awaiting_gate"
        assert script.calls.count("merge") == 1, "merged code no gate saw"


@pytest.mark.parametrize(
    "crash", ["repair", "cap", "restart"], ids=["the-repair-s-stop", "the-cap-stop", "the-restart"]
)
async def test_the_cursor_leaves_merge_before_the_walk_stops_or_moves_on(
    item_on, script, monkeypatch, crash
):
    """The repair committed and then stopped, or its rewind found the cap's
    clock spent: the item is held at `feedback` with `review` reopened before
    its stop is written. A rewind stands it at `feedback` before the walk
    enters it. A crash in between leaves it active at `feedback` -- where a
    resume walks back through the gate -- never stopped on `merge`, or left
    on it, with the repair's commit unseen."""
    from kraft.executor import walk

    it = await item_on(_chain(on_merge_rejected=_POLICY))
    await approve(it, "review")
    merge_drops(it, script, times=2)
    commit = commits_a_fix(it)
    late = []

    async def repair(_row):
        await commit(_row)
        if script.calls.count("repair") == 2:
            late.append(True)

    script.effects["repair"] = repair
    policy = None
    if crash == "repair":
        script.plan["repair"] = ["failed"]
        _crash_after(
            monkeypatch, walk, "_sentinel_stop", before=True, when=lambda *a: a[3] == "failed"
        )
    elif crash == "cap":
        real_now = walk._now
        monkeypatch.setattr(
            walk, "_now", lambda: "2999-01-01T00:00:00+00:00" if late else real_now()
        )
        policy = _policy.Policy(
            loops={"merge.on_merge_rejected": _policy.Cap(3, 3600)}, default=_policy.Cap(3, 3600)
        )
        _crash_after(monkeypatch, walk, "_stop_span_capped", before=True)
    else:
        _crash_after(
            monkeypatch,
            node_runs,
            "entered",
            before=True,
            when=lambda *a: a[3] == "feedback" and "repair" in script.calls,
        )

    with pytest.raises(_Crash):
        await _walk_through_review(it, policy)
    assert it.row()["status"] == "active"
    assert it.row()["current_node_id"] == "feedback"
    assert it.events("gate_reopened")[-1]["payload"] == {
        "gate": "review",
        "reason": "merge_rejected",
    }


async def test_a_restart_back_on_merge_does_not_answer_the_old_drop_again(
    item_on, script, monkeypatch
):
    """The span already restarted, `review` was approved again, and the walk
    is back on `merge` when the daemon dies before the forge is asked: that
    drop was answered. The resume does not hold the item for it a second
    time, sending the reviewed head back through `review`."""
    it = await item_on(_chain(on_merge_rejected=_POLICY))
    await approve(it, "review")
    merge_drops(it, script)
    _records_merge_sessions(it, script)
    script.effects["repair"] = commits_a_fix(it)
    assert await walk_once(it) == "awaiting_gate"
    await approve(it, "review")
    _crash_after(monkeypatch, node_runs, "entered", when=lambda *a: a[3] == "merge")
    with pytest.raises(_Crash):
        await walk_once(it)

    await executor.resume_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        adopted={},
        policy=_policy.Policy(loops={}, default=_policy.Cap(3, 3600)),
        launch=executor.LaunchContext(repo_entry=NO_SETUP),
    )
    assert it.row()["current_node_id"] == "merge"
    assert len(it.events("gate_reopened")) == 1
