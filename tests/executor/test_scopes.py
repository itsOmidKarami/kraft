"""The changed-test-scope builtin (`kraft.verify_changed_test_scopes`): which of
a repo's test scopes a round runs, how they run, and how their per-scope
sessions read back as findings."""

import ast
import sys

import pytest
from support.harness import _git, v1_chain, v1_walk

from kraft import executor, store
from kraft.config import git_read
from kraft.executor import dispatch
from kraft.findings import JobRef

_BUILTIN = {"id": "t", "kind": "builtin", "ref": "kraft.verify_changed_test_scopes"}
_FRONTEND = {"paths": ["frontend/**"], "command": "frontend-cmd"}
_BACKEND = {"paths": ["backend/**"], "command": "backend-cmd"}


def _verify(task=_BUILTIN, node_id="verify"):
    return [{"id": node_id, "kind": "exec", "tasks": [task]}]


def _commit(repo, *paths, content="a"):
    """Write `paths` in `repo` and commit them; returns the new HEAD."""
    for path in paths:
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"touch {' '.join(paths)}")
    return git_read(repo, "rev-parse", "HEAD")


async def _on_a_branch(item_on, repo):
    """An item on the builtin, its branch based at `repo`'s current HEAD."""
    it = await item_on(_verify())
    base = git_read(repo, "rev-parse", "HEAD")
    await it.database.write(lambda c: store.set_base_ref(c, it.id, base))
    return it


def _selected(it, round, scopes=(_FRONTEND, _BACKEND)):
    to_run, _sandbox = dispatch._select_scopes(
        it.database, it.id, it.repo, "verify", "verify.main.t", round, {"test_scopes": list(scopes)}
    )
    return [tuple(s["cmd"]) for s in to_run]


async def _dispatch(item_on, *, test_scopes, task=_BUILTIN, node_id="verify", wid="w1"):
    """Dispatch the builtin once, on a repo whose own test scopes are
    `test_scopes`. Returns the status and the item."""
    it = await item_on(_verify(task, node_id), wid=wid)
    node = it.chain.chain.nodes[0]
    status = await dispatch.dispatch_node(
        it.database,
        it.run_dirs,
        node.steps[0].tasks[0],
        node,
        it.row(),
        it.repo,
        launch=executor.LaunchContext(
            repo_entry={"setup_command": "", "test_scopes": test_scopes}, steering_dir=None
        ),
    )
    return status, it


# -- selection (Kraft-9wzy, C7 Kraft-s7c04.14) --------------------------------


_ROOT = {"paths": ["*", "src/**", "docs/**", "!frontend/**"], "command": "root"}
_SRC = {"paths": ["src/**"], "command": "backend"}
_UI = {"paths": ["frontend/**"], "command": "frontend"}
_ALL = {"paths": ["**"], "command": "all"}


@pytest.mark.parametrize(
    ("scopes", "changed", "expected"),
    [
        ([_ALL], ["a.py", "b.py"], [_ALL]),
        ([_SRC, _UI], ["frontend/x.ts"], [_UI]),
        ([_SRC, _UI], ["frontend/a.ts", "src/x.py", "frontend/b.ts"], [_SRC, _UI]),
        ([_ALL, _UI], ["frontend/x.ts"], [_ALL, _UI]),
        ([_ROOT, _UI], ["frontend/x.ts"], [_UI]),
        ([_ROOT, _UI], ["src/x.py"], [_ROOT]),
        # One path no scope claims fails the whole diff open, even beside a
        # path that does match: under-testing is the bug (Kraft-9wzy).
        ([_SRC, _UI], ["frontend/x.ts", "README.md"], [_SRC, _UI]),
    ],
    ids=[
        "one-scope-covers-everything",
        "only-the-scope-a-path-falls-under",
        "deduped-in-declaration-order",
        "a-path-in-two-scopes-runs-both",
        "exclusion-form-excludes",
        "exclusion-form-includes",
        "an-unclaimed-path-fails-open",
    ],
)
def test_matched_scopes_runs_each_scope_a_changed_path_falls_under(scopes, changed, expected):
    assert dispatch._matched_scopes(scopes, changed) == expected


async def test_changed_test_scopes_run_all_scopes_when_nothing_matches(item_on, repo):
    """`changed-test-scope-verification-selects-safely`: a changed path matching
    no configured scope, and an empty diff, both run every scope. Under-testing
    is the bug this exists to close."""
    it = await _on_a_branch(item_on, repo)
    scopes = [
        {"paths": ["src/**"], "command": "echo src"},
        {"paths": ["tests/**"], "command": "echo tests"},
    ]
    every = [("echo", "src"), ("echo", "tests")]

    assert _selected(it, 0, scopes) == every  # an empty diff
    _commit(repo, "docs/note.md")  # a changed path no scope claims
    assert _selected(it, 0, scopes) == every


async def test_select_scopes_on_the_first_round_uses_the_whole_branch_diff(item_on, repo):
    """No prior measurement for this hook -- the first round always selects
    from the full branch diff, same as before C7."""
    it = await _on_a_branch(item_on, repo)
    _commit(repo, "frontend/x.txt")

    assert _selected(it, 0) == [("frontend-cmd",)]


async def test_select_scopes_stays_incremental_after_a_clean_round(item_on, repo):
    """Round 0 ran both scopes and both passed; round 1's fix touches only
    frontend. The efficiency half of C7: nothing red from last round, so
    only the scope the new diff actually touches runs (6c712ea8 ran a
    13-minute `just ci-test` for a frontend-only fix)."""
    it = await _on_a_branch(item_on, repo)
    round0_head = _commit(repo, "frontend/x.txt", "backend/y.txt")
    for sid in ("r0-frontend", "r0-backend"):
        await it.session(sid, "verify.main.t", "done", head_sha=round0_head)
    _commit(repo, "frontend/x.txt", content="b")

    assert _selected(it, 1) == [("frontend-cmd",)]


async def test_select_scopes_reruns_everything_after_any_scope_failed_last_round(item_on, repo):
    """The C2/C7 interaction the spec calls out by name: round 0 fails
    backend and passes frontend, with frontend's row created *last* -- the
    same per-scope identity problem Task 1 fixed in `collect_findings`/
    `reusable_session`, now showing up in `prompts.last_review_session`'s own
    "most recent row" query once C7 reuses it for a multi-session hook. Round
    1's fix touches only frontend. Naive incremental selection would let a
    passing last-created row mark round 0 as fully reviewed and pick only
    frontend for round 1, leaving the backend failure unverified and
    un-reported forever. The union rule: a round following any failure at
    this hook does not trust the incremental diff and runs the full scope
    set again."""
    it = await _on_a_branch(item_on, repo)
    round0_head = _commit(repo, "frontend/x.txt", "backend/y.txt")
    # backend's row is created first and fails; frontend's is created last and
    # passes -- the mixed shape a bare "last row wins" read gets wrong.
    await it.session("r0-backend", "verify.main.t", "failed", head_sha=round0_head)
    await it.session("r0-frontend", "verify.main.t", "done", head_sha=round0_head)
    _commit(repo, "frontend/x.txt", content="b")

    assert set(_selected(it, 1)) == {("frontend-cmd",), ("backend-cmd",)}


async def test_select_scopes_verify_round_0_ignores_a_same_head_c1_gate_dispatch(item_on, repo):
    """C1 (`implementation`) and verify both dispatch `on.test.run` under the
    same hook_point. Before the review fix, verify's round 0 read the latest
    `done` row for that hook_point via `prompts.last_review_session` --
    node-blind -- and found C1's own clean gate dispatch at the same HEAD,
    so the diff since it was empty and `_matched_scopes` failed open to
    *every* scope. That is not the spec's first acceptance criterion ("the
    first round still selects from the full branch diff"): round 0 must
    ignore any other node's dispatch and always measure since `base_ref`,
    which here touches only frontend."""
    it = await _on_a_branch(item_on, repo)
    head = _commit(repo, "frontend/x.txt")
    # C1's own gate dispatch at `implementation`, round 0, clean, at the same
    # head verify is about to measure from.
    await it.session("c1-gate", "verify.main.t", "done", node="implementation", head_sha=head)

    assert _selected(it, 0) == [("frontend-cmd",)]


async def test_select_scopes_round_0_reentry_after_a_partial_failure_still_runs_the_failed_scope(
    item_on, repo
):
    """The `retry_after_cap` shape the review flagged: a prior dispatch at an
    older head failed backend and passed frontend, and the retried dispatch
    re-enters at round 0 with HEAD having since moved (a fix commit landed,
    or a human commit). Before the fix, round 0 read the latest `done` row
    for this hook_point -- frontend's, since the read never looks at its
    failed backend sibling -- and diffed only from there, so a fix touching
    only frontend's paths selected only frontend and would have gone green
    with backend still broken. Round 0 must measure the whole branch diff
    instead, which still covers backend's files."""
    it = await _on_a_branch(item_on, repo)
    h1 = _commit(repo, "frontend/x.txt", "backend/y.txt")
    await it.session("backend-fail", "verify.main.t", "failed", head_sha=h1)
    await it.session("frontend-pass", "verify.main.t", "done", head_sha=h1)
    # A fix commit lands touching only frontend before the cap's counter reset
    # re-enters at round 0.
    _commit(repo, "frontend/x.txt", content="b")

    assert set(_selected(it, 0)) == {("frontend-cmd",), ("backend-cmd",)}


# -- running the selected scopes ------------------------------------------------


async def test_changed_test_scopes_run_under_a_node_not_named_verify(item_on, tmp_path):
    """`changed-test-scope-verification-is-a-typed-built-in-task`: the task's
    type is what runs it, not the node's name. Every other test puts the builtin
    under a node called `verify`, so a name check would pass them all."""
    log = tmp_path / "ran.txt"

    status, _it = await _dispatch(
        item_on,
        test_scopes=[{"paths": ["**"], "command": f"sh -c 'echo ran >> {log}'"}],
        node_id="checks",
    )

    assert status == "done"
    assert log.read_text().split() == ["ran"]


def _scope_marker(log, name):
    return {
        "paths": ["**"],
        "command": f"sh -c 'echo {name}-start >> {log}; sleep 0.4; echo {name}-end >> {log}'",
    }


async def test_changed_test_scopes_run_sequentially_unless_configured_parallel(item_on, tmp_path):
    """`changed-test-scope-verification-is-sequential-by-default`: the scopes
    share one worktree, so one finishes before the next starts unless the task
    asks for parallel."""
    ran = {}
    for execution in ("sequential", "parallel"):
        log = tmp_path / f"{execution}.txt"
        status, _it = await _dispatch(
            item_on,
            test_scopes=[_scope_marker(log, "one"), _scope_marker(log, "two")],
            task={**_BUILTIN, "execution": execution},
            wid=execution,
        )
        assert status == "done"
        ran[execution] = log.read_text().split()

    assert ran["sequential"] == ["one-start", "one-end", "two-start", "two-end"]
    assert set(ran["parallel"][:2]) == {"one-start", "two-start"}


async def test_changed_test_scopes_report_one_aggregate_result(item_on):
    """`changed-test-scope-verification-aggregates-results`: every selected
    scope runs and the task reports one status -- a later scope's pass never
    hides an earlier scope's failure (C2, Kraft-s7c04.9)."""
    status, it = await _dispatch(
        item_on,
        test_scopes=[{"paths": ["**"], "command": "false"}, {"paths": ["**"], "command": "true"}],
    )

    assert status == "failed"
    # Both scopes ran, both under the task's own canonical path, and the task
    # reported once.
    assert [(s["hook_point"], s["status"]) for s in it.sessions()] == [
        ("verify.main.t", "failed"),
        ("verify.main.t", "done"),
    ]


async def test_the_repos_declared_env_reaches_a_test_scopes_run_task(tmp_path, repo):
    """dispatch.py calls `_subprocess.run_task` directly for each matched test
    scope, bypassing `resolve_invocation`. Left unwired, that call hands
    `run_task` a `repo_entry` it never reads, and the repo's declared `env`
    never reaches the one path whose job is to decide whether the MR is safe
    to merge (Kraft-69atv Step 4b). The call-site override
    (`PYTHONDONTWRITEBYTECODE=1`) must still win alongside it."""
    dumped = tmp_path / "child-env.txt"
    dump = f"open({str(dumped)!r}, 'w').write(repr(dict(os.environ)))"

    await v1_walk(
        tmp_path,
        v1_chain(_verify(), repo=repo),
        repo=repo,
        repo_entry={
            "test_command": f'{sys.executable} -c "import os; {dump}"',
            "setup_command": "",
            "env": {"MY_REPO": "1"},
        },
    )

    child = ast.literal_eval(dumped.read_text())
    assert child["MY_REPO"] == "1"  # the repo's declared env reached the scope
    assert child["PYTHONDONTWRITEBYTECODE"] == "1"  # the call-site override still wins


# -- per-scope result identity (Batch C·MR1 Task 1, Kraft-s7c04.9/.8/.14) ----


async def _exited(it, rows, *, round=0):
    """Seed one exited session per `(sid, status, command, log text)` row, all
    at the builtin's own task path: one session per scope under one path."""
    for sid, status, command, log in rows:
        if log:
            (it.run_dirs.logs / f"{sid}.log").write_text(log)
        await it.session(
            sid, "verify.main.t", status, round=round, head_sha=f"sha-{round}", command=command
        )


async def test_collect_findings_reports_an_early_scope_failure_even_when_a_later_scope_passes(
    item_on,
):
    """The changed-test-scope builtin mints one session per scope under one
    task path (dispatch.py's identity problem, spec 2026-09-15-batch-c1-design
    §"The identity problem"). A last-wins read of the round's sessions would
    let scope 3's pass erase scope 1's real failure -- exactly the blind
    failure gap 65f3ed90 closed, and C2 regresses it without this fix."""
    it = await item_on(_verify(), repo="/r")
    # A V1 scope session records the repo command it ran: the builtin task
    # itself carries none.
    await _exited(
        it,
        [
            ("s-scope-1", "failed", "just ci-test", "2 tests failed\n"),
            ("s-scope-2", "done", "just ci-test", None),
            ("s-scope-3", "done", "just ci-test", None),
        ],
    )

    found, reported = dispatch.collect_findings(it.database, it.id, it.chain.chain.nodes[0], 0)

    assert len(found) == 1, f"expected exactly scope 1's blind failure, got {found}"
    assert "2 tests failed" in found[0].message
    assert "just ci-test" in found[0].message
    assert reported == set()
    assert found[0].jobs == (
        JobRef(label="just ci-test", log_ref="kraft view logs w1 --session s-scope-1"),
    )


async def test_collect_findings_aggregates_multiple_failing_scopes_into_one_finding(item_on):
    """G1 brainstorm: C2 runs every scope rather than stopping at the first
    failure, so two scopes under the changed-test-scope builtin can fail in the
    same round -- the fix agent needs one coherent notice naming both, not two
    identical-looking critical findings.

    And the property `walk.py`'s stuck-detector depends on (`prints ==
    previous_prints`): the same two scopes failing identically in a later round
    -- different session ids, a different round -- produce the same single
    fingerprint, or the stuck-detector's streak can never advance past 1."""
    it = await item_on(_verify(), repo="/r")
    node = it.chain.chain.nodes[0]
    found = {}
    for round_ in (0, 1):
        await _exited(
            it,
            [
                (f"s-r{round_}-1", "failed", "just test-ui", "test A failed\n"),
                (f"s-r{round_}-2", "failed", "just e2e-ci", "test B failed\n"),
            ],
            round=round_,
        )
        found[round_], reported = dispatch.collect_findings(it.database, it.id, node, round_)
        assert reported == set()

    assert len(found[0]) == 1, f"expected one aggregated finding, got {found[0]}"
    assert "just test-ui" in found[0][0].message
    assert "just e2e-ci" in found[0][0].message
    assert len(found[0][0].jobs) == 2
    assert found[0][0].fingerprint == found[1][0].fingerprint


async def test_collect_findings_ignores_a_stale_needs_context_row_from_a_reviewer(item_on):
    """A reviewer that exits `needs_context` stops before `bump_counter`, so a
    resume re-enters at the same round and the same head_sha -- the stale
    `needs_context` row (no result file) now sits alongside the real row from
    the resumed pass. Unlike the changed-test-scope builtin's scope loop, an
    agent task only ever mints one *real* session per pass, so this must stay
    last-wins: reading every same-head row here would hit `_FAILING_STATUSES`
    on the stale row and mint a bogus `from_blind_failure` critical finding
    for a failure that never happened."""
    review = {"id": "review", "kind": "agent", "harness": "fake", "prompt": "Do it."}
    it = await item_on(_verify(review), repo="/r")
    for sid, status in (("s-stale", "needs_context"), ("s-resumed", "done")):
        await it.session(sid, "verify.main.review", status, head_sha="sha-a")

    assert dispatch.collect_findings(it.database, it.id, it.chain.chain.nodes[0], 0) == ([], set())
