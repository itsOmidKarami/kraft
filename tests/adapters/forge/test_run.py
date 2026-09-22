"""`run_task`/`_run_one`: one forge node -- open_mr, ci_poll, sync_mr,
mark_ready, merge -- against `FakeForge` (or a stubbed CLI), plus
`resolve`/`backend_for`. merge_watch is in test_merge_watch.py, whole chains
through the executor in test_run_chain.py."""

from __future__ import annotations

import json
import subprocess

import pytest

from kraft.adapters import forge

from . import outputs
from .nodes import back_half

RED = [(forge.FailedJob("test", "failed", "script_failure"),)]


async def _opened(fake: forge.FakeForge, repo, *, merged: bool = False) -> forge.FakeForge:
    """`fake` with an MR open on `kraft/w1` in `repo` (and merged, if asked)."""
    mr = await fake.open_mr(repo=repo, branch="kraft/w1", base="main", title="t", body="b")
    if merged:
        await fake.merge(repo=repo, branch="kraft/w1", mr=mr)
        fake.pushed.clear()
    return fake


# --- resolve / backend_for ---------------------------------------------------


@pytest.mark.parametrize(
    "name, cls",
    [("glab", forge.GlabCli), ("gh", forge.GhCli), ("fake", forge.FakeForge)],
    ids=["glab", "gh", "fake"],
)
def test_resolve_returns_each_named_backend(name, cls):
    assert isinstance(forge.resolve(name), cls)


def test_resolve_rejects_an_unknown_backend():
    """Named, never probed: an unusable backend must fail loudly rather than
    silently taking a different path than the one configured."""
    with pytest.raises(forge.ForgeError, match="bitbucket"):
        forge.resolve("bitbucket")


@pytest.mark.parametrize(
    "backend, repo_forge, expected",
    [
        # `auto` is still a *name*: it resolves against the forge recorded for
        # the repo in repos.yaml, never against which CLI happens to be installed.
        ("auto", "gitlab", "glab"),
        ("auto", "github", "gh"),
        # Ruling 147: `forge: fake` is the dev-only repo value `just dev` uses to
        # reach the merge-request half of a chain without a real forge.
        ("auto", "fake", "fake"),
        # A registry that pins a backend wins over the repo entry -- the escape
        # hatch for a self-hosted host `config._FORGES` cannot recognise.
        ("glab", "github", "glab"),
        ("fake", None, "fake"),
    ],
    ids=["auto-gitlab", "auto-github", "auto-dev-fake", "explicit-beats-repo", "explicit-no-repo"],
)
def test_backend_for_names_the_backend(backend, repo_forge, expected):
    assert forge.backend_for(backend, repo_forge) == expected


def test_a_repo_with_no_forge_is_told_the_remedy_v1_actually_reads():
    """V1 dispatch always passes `backend: auto`, so a registry `backend:` pin
    is never read -- naming it sends an operator to a file that changes
    nothing. The remedy is the repo's own `forge`, and `fake` is for dev."""
    with pytest.raises(forge.ForgeError) as err:
        forge.backend_for("auto", None)
    message = str(err.value)
    assert "registry.yaml" not in message
    assert "`forge: fake`" in message and "dev" in message


async def test_the_dev_fake_forge_remembers_an_mr_across_nodes(item_on, database, run_dirs, repo):
    """Ruling 147, round 3: a repo on `forge: fake` resolves afresh at every
    forge node, so the fake must be one instance per process -- otherwise the
    draft MR `open_mr` created is gone by the time `sync_mr` (and later
    `mark_ready`, `merge`) look for it. Nothing is monkeypatched: each
    `run_task` below goes through the real `backend_for`/`resolve`."""
    branch = f"kraft/{repo.parent.name}"
    subprocess.run(["git", "checkout", "-qb", branch], cwd=repo, check=True)
    await item_on(back_half(), repo=repo)

    statuses = [
        await forge.run_task(
            database,
            run_dirs,
            session_id=f"s-{handler}",
            work_item_id="w1",
            node_id=handler,
            hook_point=handler,
            handler=handler,
            backend="auto",
            repo_forge="fake",
            repo=repo,
            orig_repo=repo,
            branch=branch,
            title="t",
        )
        for handler in ("open_mr", "sync_mr")
    ]

    assert statuses == ["done", "done"]
    fake = forge.resolve(forge.backend_for("auto", "fake"))
    found = await fake.find_mr(repo=repo, branch=branch)
    assert found is not None and found.state == "open"
    assert branch in fake.bodies, "sync_mr's description never reached the MR open_mr made"
    assert fake.opened_draft[found.number] is False


# --- a node that cannot run ----------------------------------------------------


async def test_auto_with_no_recorded_forge_stops_as_a_config_error_rather_than_escaping(run_forge):
    """Resolution can fail at runtime. Escaping `run_task` past
    `finish_session` would leave a started session row with no result and no
    log -- pause, abandon and reattach all key off that row. So it finishes the
    row, and as `config_error`, not `failed`: nothing was launched, and a failed
    task would send a fix loop round cycles no agent can win (Kraft-hr0xr).
    """
    fake = forge.FakeForge()

    result = await run_forge(fake, "open_mr", "s-auto", backend="auto")

    assert result == ("config_error", "config_error"), "the session row must be finished"
    assert "repos.yaml" in run_forge.log("s-auto"), "the log must name the fix"
    assert not fake.opened, "no merge request may be opened with no forge resolved"


def test_every_v1_forge_target_maps_to_a_handler():
    """Ruling 48 left `mr.automated_review` and `mr.external_approval`
    unmapped until the wait scheduler; a target with no handler stops every
    chain that names it. And every wait target is one of `run`'s waits, so it
    is observed and recorded, never slept on."""
    from kraft.templates.models import ForgeAction

    assert {a.value for a in ForgeAction} <= set(forge.run.V1_HANDLERS)
    waits = {forge.run.V1_HANDLERS[a.value] for a in ForgeAction if a.waits}
    assert waits == set(forge.run._WAITS)


class _Exploding(forge.FakeForge):
    async def ci_status(self, *, repo, mr, branch="", pipeline_id=""):
        raise forge.ForgeError("glab fell over")


async def test_a_forge_error_mid_poll_fails_the_node_rather_than_escaping(run_forge):
    """A flaky CLI still lands as a failed node -- and, since a wait left no
    record of how it ended (Kraft-vzq2q), as the wait's `error` outcome."""
    assert await run_forge(_Exploding(), "ci_poll", "s7") == ("failed", "failed")
    assert "glab fell over" in run_forge.log("s7")
    (ended,) = run_forge.events("external_wait_ended")
    assert ended["outcome"] == "error" and "glab fell over" in ended["result"]


# --- open_mr / sync_mr / mark_ready --------------------------------------------


async def test_open_mr_creates_one_and_records_an_mr_opened_event(run_forge):
    """Kraft-d2sq. The URL went into the session log text and nowhere else:
    not on the work item row, not in an event, not in the session result. Past
    open_mr there was no way to reach the merge request but to open the forge
    and search for the branch."""
    fake = forge.FakeForge()

    assert await run_forge(fake, "open_mr", "x6") == ("done", "done")

    assert fake.opened == {1: "kraft/w1"}
    assert "opened http://fake.forge/1" in run_forge.log("x6")
    assert run_forge.events("mr_opened") == [{"number": 1, "url": "http://fake.forge/1"}]


async def test_open_mr_reuses_an_open_mr_for_the_branch(run_forge):
    """Kraft-ko7j's re-entry walks back through this node, and a retry of an
    open_mr that crashed after the create hits the same wall: `mr create` for a
    branch that already has one is an error on both CLIs. The reuse still
    records `mr_opened`: a rejected review walks the item back through open_mr,
    and the item must still carry a current record of its MR."""
    fake = forge.FakeForge(opened={7: "kraft/w1"})

    assert await run_forge(fake, "open_mr", "x5") == ("done", "done")

    assert list(fake.opened) == [7], "it opened a second merge request for one branch"
    assert fake.pushed == ["kraft/w1"]
    assert fake.bodies["kraft/w1"], "the description was not rewritten from the branch head"
    assert "reusing !7" in run_forge.log("x5")
    assert run_forge.events("mr_opened") == [{"number": 7, "url": "http://fake.forge/7"}]


def _write_meta(worktree, front: str) -> None:
    meta_dir = worktree / ".engineering" / "mr_metas"
    meta_dir.mkdir(parents=True)
    (meta_dir / "w1.md").write_text(
        f"---\nwork_item_ids: [w1]\n{front}---\n## What this introduces\n\nA door.\n"
    )


async def test_open_mr_uses_the_authored_title_labels_and_description(run_forge, tmp_path):
    """`open_mr` reads `.engineering/mr_metas/<wid>.md` out of the item's own
    worktree and hands its title, labels and description through to the
    forge -- a reviewer sees what the agent decided, not a stitched diary."""
    _write_meta(tmp_path, "title: Authored title\nlabels: [release::minor]\n")
    fake = forge.FakeForge()

    await run_forge(fake, "open_mr", "x7")

    (number,) = fake.opened
    assert fake.opened_titles[number] == "Authored title"
    assert fake.opened_meta[number].labels == ("release::minor",)
    assert fake.opened_bodies[number].startswith("## What this introduces")


async def test_open_mr_without_the_artifact_opens_the_default_body(run_forge):
    """Spec §5: no artifact at all -- an install that never ran `on.mr.describe`,
    or one that failed to write it -- must open exactly today's MR, not fail
    the node."""
    fake = forge.FakeForge()

    assert await run_forge(fake, "open_mr", "x8", title="the work item title") == ("done", "done")

    (number,) = fake.opened
    assert fake.opened_titles[number] == "the work item title"
    assert fake.opened_meta[number] == forge.MRMeta()
    assert fake.opened_bodies[number] == forge.mr_body("w1", "kraft/w1", ())


async def test_sync_mr_republishes_the_authored_description(run_forge, tmp_path):
    _write_meta(tmp_path, "")
    fake = await _opened(forge.FakeForge(), tmp_path)

    await run_forge(fake, "sync_mr", "x9")

    assert fake.bodies["kraft/w1"].startswith("## What this introduces")


async def test_mark_ready_undrafts_the_merge_request(run_forge, tmp_path):
    """`mr.mark_ready` is V1's publication step, split out of `sync_mr` so a
    chain can put its final gate between describing the MR and publishing it.
    Without a handler the seeded chain stopped here for a human every time
    (Ruling 48's `config_error` arm), so the chain could never reach merge."""
    fake = await _opened(forge.FakeForge(), tmp_path)

    handler = forge.run.handler_for("mr.mark_ready")
    assert await run_forge(fake, handler, "s-ready") == ("done", "done")

    assert fake.opened_draft[1] is False


class _Recording(forge.FakeForge):
    """Records the order of the calls that change the MR or read its CI."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.order: list[str] = []

    async def push(self, *, repo, branch):
        self.order.append("push")
        await super().push(repo=repo, branch=branch)

    async def update_mr(self, *, repo, branch, body):
        self.order.append("update_mr")
        await super().update_mr(repo=repo, branch=branch, body=body)

    async def mark_ready(self, *, repo, branch, mr):
        self.order.append("mark_ready")
        await super().mark_ready(repo=repo, branch=branch, mr=mr)

    async def ci_status(self, *, repo, mr, branch="", pipeline_id=""):
        self.order.append("ci_status")
        return await super().ci_status(repo=repo, mr=mr, branch=branch, pipeline_id=pipeline_id)

    async def merge(self, *, repo, branch="", mr):
        self.order.append("merge")
        await super().merge(repo=repo, branch=branch, mr=mr)


@pytest.mark.parametrize(
    "handler, expected",
    [
        # Kraft-bxj8: the worker commits and is told not to push, so ci_poll
        # polled the previous head's pipeline -- the same id on every retry.
        ("ci_poll", ["push", "ci_status"]),
        # `mr_sync` is the one node every chain shape runs before `merge`, so
        # the draft comes off there -- before the push, so a project that
        # reruns checks on ready-for-review sees the head it is about to
        # check. Then push, then describe (Kraft-nh5m): the description and
        # the branch the reviewer's forge shows describe the same head.
        ("sync_mr", ["mark_ready", "push", "update_mr"]),
        # `_assert_pushed` refuses a head the remote never saw, and a retry of
        # `merge` alone never re-runs `mr_sync`, so merge pushes first itself.
        ("merge", ["push", "ci_status", "merge"]),
    ],
    ids=["ci_poll-pushes-then-polls", "sync_mr-undrafts-pushes-describes", "merge-pushes-first"],
)
async def test_a_node_pushes_before_it_acts_on_the_head(run_forge, tmp_path, handler, expected):
    fake = await _opened(_Recording(ci_states=["success"]), tmp_path)

    assert await run_forge(fake, handler, "b1") == ("done", "done")

    assert list(dict.fromkeys(fake.order)) == expected, "first call of each kind, in order"
    assert fake.pushed == ["kraft/w1"]


# --- already merged ------------------------------------------------------------


class _AlreadyMerged(forge.FakeForge):
    """An MR merged out-of-band: its source branch went with it, so any push,
    undraft, redescribe or merge would die against a ref that is gone."""

    async def push(self, *, repo, branch):
        raise AssertionError("pushed to a branch the forge has already merged and deleted")

    async def mark_ready(self, *, repo, branch, mr):
        raise AssertionError("undrafted a merge request the forge has already merged")

    async def update_mr(self, *, repo, branch, body):
        raise AssertionError("redescribed a merge request the forge has already merged")


@pytest.mark.parametrize(
    "handler, phrase",
    [
        # Kraft-7itv's twin, live on b5afe84c: the push ci_poll makes on every
        # entry died `! [rejected] ... (stale info)`.
        ("ci_poll", "nothing to check"),
        # Kraft-7itv, from 45b06993: merged by hand, human_review skipped.
        ("sync_mr", "nothing to sync"),
        ("mark_ready", "nothing to mark ready"),
        ("merge", "nothing to do"),
    ],
    ids=["ci_poll", "sync_mr", "mark_ready", "merge"],
)
async def test_a_node_treats_an_already_merged_mr_as_done(run_forge, tmp_path, handler, phrase):
    fake = await _opened(_AlreadyMerged(), tmp_path, merged=True)

    assert await run_forge(fake, handler, "s-merged") == ("done", "done")

    assert f"already merged (!1); {phrase}" in run_forge.log("s-merged")
    assert fake.merged == [1], "merged once, out-of-band, and never again"


@pytest.mark.parametrize(
    "backend, cls, mr_list, expected, phrase",
    [
        # Kraft-xron, from af0fb78e: MR !62 auto-merged when its pipeline went
        # green, and the merge node ran eight minutes later.
        (
            "glab",
            forge.GlabCli,
            outputs.GLAB_MR_LIST_MERGED,
            "done",
            "already merged (!54)",
        ),
        (
            "gh",
            forge.GhCli,
            outputs.GH_PR_LIST_MERGED,
            "done",
            "already merged (!7)",
        ),
        # A closed merge request is not a merged one, and nothing about it is
        # resolved.
        (
            "glab",
            forge.GlabCli,
            '[{"iid":54,"state":"closed","web_url":"http://x/54"}]',
            "failed",
            "closed",
        ),
    ],
    ids=["glab-merged", "gh-merged", "glab-closed-still-fails"],
)
async def test_merge_reads_the_mr_state_off_the_cli(
    run_forge, cli, backend, cls, mr_list, expected, phrase
):
    cli.stub(backend, mr_list)

    assert await run_forge(cls(), "merge", "x1") == (expected, expected)

    assert phrase in run_forge.log("x1")
    assert "merge" not in cli.argv(backend), "it tried to merge an MR that is not open"


# --- ci_poll -------------------------------------------------------------------


@pytest.mark.parametrize(
    "fake_kw, expected, phrase",
    [
        ({"ci_states": ["success"]}, "done", "success"),
        # Red CI must not read as a done node -- the next node is merge.
        ({"ci_states": ["failed"], "ci_failed_jobs": RED}, "failed", "pipeline failed"),
        # One check, then hand the wait back to the scheduler -- no 30-minute
        # coroutine (Kraft-ru98).
        ({"ci_states": ["pending"]}, "waiting", "pipeline pending"),
        # Green and unmergeable, confirmed by a re-fetch (Kraft-ejj9, Kraft-bjjm):
        # the log names the state, so the human_review brief can act on it.
        (
            {"ci_states": ["success"], "mergeable": False, "merge_detail": "conflict"},
            "conflict",
            "not mergeable: conflict",
        ),
        # No merge_detail at all: render_ci must still call the conflict, and
        # fall back to naming it "unknown" rather than staying silent.
        (
            {"ci_states": ["success"], "mergeable": False},
            "conflict",
            "not mergeable: unknown",
        ),
        # `mergeable is None` is not `False`: an unapproved MR with a green
        # pipeline is exactly what this node hands to the gate.
        (
            {"ci_states": ["success"], "mergeable": None, "merge_detail": "not_approved"},
            "done",
            "success",
        ),
    ],
    ids=[
        "green",
        "red",
        "pending",
        "unmergeable",
        "unmergeable-without-detail",
        "undecided-merge-state",
    ],
)
async def test_ci_poll_settles_the_node_from_the_pipeline(run_forge, fake_kw, expected, phrase):
    assert await run_forge(forge.FakeForge(**fake_kw), "ci_poll", "e1") == (
        expected,
        expected,
    )
    log = run_forge.log("e1")
    assert phrase in log
    # The human_review brief has to tell 'finished red' from 'never finished'.
    assert "timed out" not in log


async def test_ci_poll_writes_findings_for_a_code_red_pipeline(run_forge, run_dirs):
    """A CI wait going code-red feeds the fix-loop plumbing the same way any
    other measuring task does: one finding per failed job, result_path
    populated (Kraft-cbr §3), each finding sourced to the task that measured
    it by its canonical path."""
    await run_forge(
        forge.FakeForge(ci_states=["failed"], ci_failed_jobs=RED),
        "ci_poll",
        "s-f",
        hook_point="merge_request_feedback.ci.await_ci",
    )

    payload = json.loads((run_dirs.results / "s-f.json").read_text())
    assert payload["findings"]
    assert "test" in payload["findings"][0]["message"]
    assert {f["source_plugin"] for f in payload["findings"]} == {
        "merge_request_feedback.ci.await_ci"
    }


@pytest.mark.parametrize(
    "jobs, expected",
    [
        # Task 3's glab shape: a header line plus indented trace lines; only
        # this job's own block, not the next one's.
        (
            (
                "pipeline 88: failed",
                "job test: failed (script_failure)",
                "  AssertionError: expected 3, got 4",
                "  at test_foo.py:12",
                "job lint: failed (script_failure)",
                "  ruff: E501 line too long",
            ),
            "job test: failed (script_failure)\n"
            "  AssertionError: expected 3, got 4\n  at test_foo.py:12",
        ),
        # gh's one-liner shape has nothing to extract a block from.
        (("test: FAILURE",), "job test failed: script_failure"),
    ],
    ids=["glab-trace-tail", "gh-no-trace"],
)
def test_job_finding_message(jobs, expected):
    job = forge.FailedJob("test", "failed", "script_failure")
    assert forge.run._job_finding_message(jobs, job) == expected


# --- ci_poll and merge_watch re-entries ------------------------------------------


@pytest.mark.parametrize("handler", ["ci_poll", "merge_watch"])
async def test_a_still_pending_wait_reuses_its_session(run_forge, repo, handler):
    """Kraft-41b/Kraft-7xt: two re-entries while the pipeline is still pending
    leave exactly one worker_sessions row, with both polls' log text in it, and
    no attempt-number climb."""
    fake = forge.FakeForge(ci_states=["pending", "pending"])

    for sid in ("s1", "s2"):
        await run_forge(fake, handler, sid, repo=repo)

    rows = run_forge.sessions()
    assert [(r["id"], r["attempt"], r["status"]) for r in rows] == [("s1", 1, "waiting")]
    assert run_forge.log("s1").count("pipeline pending") == 2


@pytest.mark.parametrize("handler", ["ci_poll", "merge_watch"])
async def test_a_re_entry_pins_the_pipeline_it_last_saw(run_forge, repo, handler):
    """Kraft-ivh1: a second poll of the same head reads the pinned pipeline id
    back rather than re-resolving "latest" -- for merge_watch, against the
    target branch, which survives a second item merging before this one's own
    pipeline settles."""
    fake = forge.FakeForge(ci_states=["pending", "pending"], ci_pipeline_refs=["555"])

    for sid in ("s1", "s2"):
        await run_forge(fake, handler, sid, repo=repo)

    assert fake.pipeline_ids_requested == ["", "555"]


@pytest.mark.parametrize("handler", ["ci_poll", "merge_watch"])
async def test_infra_red_is_retried_across_entries_then_stops_with_the_reason(
    run_forge, repo, handler
):
    """Kraft-h81i: the retry budget survives across separate entries (three
    calls standing in for three ~30s-apart ci_wait re-entries), not inside one
    call's loop. Each entry's re-read after a kick sees "pending" (`waiting`),
    exactly like a real forge the instant after `retry_jobs` returns. Two kicks,
    then the third entry's count breaches the cap before a third kick -- and
    for merge_watch no follow-up bead: this is a runner problem, not the code."""
    fake = forge.FakeForge(
        ci_states=["failed", "pending"] * 2 + ["failed"],
        ci_failed_jobs=[(forge.FailedJob("build", "failed", "runner_system_failure"),)] * 5,
    )

    results = [(await run_forge(fake, handler, sid, repo=repo))[0] for sid in "abc"]

    assert results == ["waiting", "waiting", "infra_stop"]
    assert len(fake.retried) == 2
    assert run_forge.events("ci_infra_exhausted")


# --- merge ---------------------------------------------------------------------


class _MergesOnlyOnGreen(forge.FakeForge):
    """`glab mr merge --yes` against a still-running pipeline exits 0 but merges
    nothing (Kraft-79x3): this fake refuses unless the last read was green."""

    last = None

    async def ci_status(self, **kw):
        status = await super().ci_status(**kw)
        self.last = status.state
        return status

    async def merge(self, *, repo, branch="", mr):
        if self.last != "success":
            raise forge.ForgeError(f"merged over a {self.last} pipeline")
        await super().merge(repo=repo, branch=branch, mr=mr)


class _Refusing(forge.FakeForge):
    async def merge(self, *, repo, branch="", mr):
        raise forge.ForgeError("merge blocked: 1 approval required")


class _AutoMergeScheduled(forge.FakeForge):
    """Kraft-79x3, 0853ea31 / MR !76: with the head's pipeline still running,
    GitLab's `mr merge` enables "merge when all checks pass", exits 0, and
    merges nothing."""

    async def merge(self, *, repo, branch="", mr):
        return None


class _ClosedAfterwards(_AutoMergeScheduled):
    """The first read is run_task's own `find_mr`, which has to say open or the
    node never reaches the merge at all; every read after it is the
    verification poll, and says closed."""

    reads = 0

    async def find_mr(self, *, repo, branch):
        ref = await super().find_mr(repo=repo, branch=branch)
        if ref is None:
            return None
        self.reads += 1
        return forge.MRRef(ref.number, ref.url, "open" if self.reads == 1 else "closed")


@pytest.mark.parametrize(
    "make, opened, expected, phrases",
    [
        # Kraft-266b/x10m: a push after the final gate re-arms a pipeline the
        # feedback node never watched; merge waits it out -- through the
        # scheduler, one observation per entry -- before `forge.merge()`, and
        # success is decided by a read of the forge, not by an exit code.
        (
            lambda: _MergesOnlyOnGreen(ci_states=["pending", "success"]),
            True,
            ["waiting", "done"],
            ["merged !1"],
        ),
        # ... and a re-armed pipeline that comes back red stops the node first.
        (
            lambda: forge.FakeForge(ci_states=["pending", "failed"]),
            True,
            ["waiting", "failed"],
            ["pipeline failed"],
        ),
        # A conflict is an answer, not something `forge.merge()` should be asked
        # to resolve -- "conflict", not a blanket "failed", same as ci_poll.
        (
            lambda: forge.FakeForge(
                ci_states=["success"], mergeable=False, merge_detail="conflict"
            ),
            True,
            ["conflict"],
            ["not mergeable: conflict"],
        ),
        # Green and conflict-free can still lack a required approval: an
        # ordinary pending state, waited out rather than failed
        # (`missing-external-approval-is-normal-pending-state`).
        (
            lambda: forge.FakeForge(ci_states=["success"], block_reason="not_approved"),
            True,
            ["waiting"],
            ["required approval"],
        ),
        # A refusal the forge raises still fails the node.
        (_Refusing, True, ["failed"], ["1 approval required"]),
        # Kraft-79x3: exit 0 and nothing merged is a merge still to land --
        # waited for, never reported as done.
        (_AutoMergeScheduled, True, ["waiting"], ["still open"]),
        # Closed is not merged: nothing landed, and the branch is gone.
        (_ClosedAfterwards, True, ["failed"], ["closed"]),
        # No MR at all: the node records the ForgeError as a failure rather
        # than a traceback out of the executor.
        (lambda: forge.FakeForge(ci_states=["success"]), False, ["failed"], []),
    ],
    ids=[
        "re-armed-pipeline-waited-out",
        "re-armed-pipeline-red",
        "unmergeable",
        "missing-approval",
        "forge-refuses",
        "still-open-after-merge",
        "closed-after-merge",
        "no-mr",
    ],
)
async def test_merge_lands_only_a_green_mergeable_head(
    run_forge, tmp_path, make, opened, expected, phrases
):
    """One merge node, entered once per scheduler observation."""
    fake = make()
    if opened:
        await _opened(fake, tmp_path)

    results = [(await run_forge(fake, "merge", f"m{i}"))[0] for i in range(len(expected))]

    assert results == expected
    log = run_forge.log("m0")
    assert all(p in log for p in phrases), log
    assert fake.merged == ([1] if expected[-1] == "done" else []), (
        "merged, or claimed to, when it must not"
    )


async def test_merge_rebases_a_conflict_away_before_calling_merge(run_forge, repo):
    """The rebase-and-bounce `mr_checks` has for a conflict (Kraft-9h7v), on
    `merge` too: `mr_sync`'s push after `human_review` can turn up a conflict
    only `merge` ever sees. Against a real repo, a forced rebase onto an
    unmoved default branch is a no-op -- `new_head` comes back falsy, so the
    node still reports the pre-existing conflict rather than a fabricated
    'done'."""
    fake = await _opened(
        forge.FakeForge(ci_states=["success"], mergeable=False, merge_detail="conflict"), repo
    )

    assert await run_forge(fake, "merge", "r1", repo=repo) == (
        "conflict",
        "conflict",
    )

    assert fake.merged == [], "an unresolved conflict must never reach forge.merge"
