"""The changed-test-scope builtin (`kraft.verify_changed_test_scopes`): which of
a repo's test scopes a round runs, how they run, and how their per-scope
sessions read back as findings."""

import ast
import sys
from pathlib import Path

import pytest
from support.harness import commit_all, entry_of, git, v1_chain, v1_walk, write

from kraft import executor, store
from kraft.config import probe_repo
from kraft.executor import dispatch
from kraft.findings import JobRef

from .test_walk import _walk

_BUILTIN = {"id": "t", "kind": "builtin", "ref": "kraft.verify_changed_test_scopes"}
_FRONTEND = {"paths": ["frontend/**"], "command": "frontend-cmd"}
_BACKEND = {"paths": ["backend/**"], "command": "backend-cmd"}


def _verify(task=_BUILTIN, node_id="verify"):
    return [{"id": node_id, "kind": "exec", "tasks": [task]}]


async def _on_a_branch(item_on, repo):
    """An item on the builtin, its branch based at `repo`'s current HEAD."""
    it = await item_on(_verify())
    base = git(repo, "rev-parse", "HEAD")
    await it.database.write(lambda c: store.set_base_ref(c, it.id, base))
    return it


def _selected(it, round, scopes=(_FRONTEND, _BACKEND)):
    to_run = dispatch._select_scopes(
        it.database,
        it.id,
        it.repo,
        "verify",
        "verify.main.t",
        round,
        entry_of({"test_scopes": list(scopes)}),
    )
    return [tuple(s["cmd"]) for s in to_run]


async def _dispatch(
    item_on, *, test_scopes, task=_BUILTIN, node_id="verify", wid="w1", test_command=None
):
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
            repo_entry=entry_of(
                {"setup_command": "", "test_scopes": test_scopes, "test_command": test_command}
            ),
        ),
    )
    return status, it


async def test_a_repo_that_declares_no_test_command_stops_naming_what_to_configure(item_on):
    """Kraft-r19n0: quick-task and `default` both verify with this builtin. A
    repo declaring neither `test_scopes` nor `test_command` stops for a human
    at verify, naming both keys, rather than having a command guessed for it."""
    status, it = await _dispatch(item_on, test_scopes=None)

    [session] = it.sessions()
    assert (status, session["status"]) == ("config_error", "config_error")
    log = Path(session["log_path"]).read_text()
    assert "neither test_scopes nor test_command in repos.yaml" in log
    assert "will not guess a command" in log


async def test_a_repo_that_declares_it_has_no_tests_passes_verify_saying_so(item_on):
    """`test_command: ""` is a decision, as `setup_command: ""` is: a repo with
    no tests (docs, infrastructure) passes verify, and its session says why."""
    status, it = await _dispatch(item_on, test_scopes=None, test_command="")

    [session] = it.sessions()
    assert (status, session["status"]) == ("done", "done")
    assert 'test_command: "" (no tests)' in Path(session["log_path"]).read_text()


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
    write(repo, "docs/note.md", "a")  # a changed path no scope claims
    commit_all(repo)
    assert _selected(it, 0, scopes) == every


async def test_select_scopes_on_the_first_round_uses_the_whole_branch_diff(item_on, repo):
    """No prior measurement for this hook -- the first round always selects
    from the full branch diff, same as before C7."""
    it = await _on_a_branch(item_on, repo)
    write(repo, "frontend/x.txt", "a")
    commit_all(repo)

    assert _selected(it, 0) == [("frontend-cmd",)]


async def test_select_scopes_stays_incremental_after_a_clean_round(item_on, repo):
    """Round 0 ran both scopes and both passed; round 1's fix touches only
    frontend. The efficiency half of C7: nothing red from last round, so
    only the scope the new diff actually touches runs (6c712ea8 ran a
    13-minute `just ci-test` for a frontend-only fix)."""
    it = await _on_a_branch(item_on, repo)
    write(repo, "frontend/x.txt", "a")
    write(repo, "backend/y.txt", "a")
    round0_head = commit_all(repo)
    for sid in ("r0-frontend", "r0-backend"):
        await it.session(sid, "verify.main.t", "done", head_sha=round0_head)
    write(repo, "frontend/x.txt", "b")
    commit_all(repo)

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
    write(repo, "frontend/x.txt", "a")
    write(repo, "backend/y.txt", "a")
    round0_head = commit_all(repo)
    # backend's row is created first and fails; frontend's is created last and
    # passes -- the mixed shape a bare "last row wins" read gets wrong.
    await it.session("r0-backend", "verify.main.t", "failed", head_sha=round0_head)
    await it.session("r0-frontend", "verify.main.t", "done", head_sha=round0_head)
    write(repo, "frontend/x.txt", "b")
    commit_all(repo)

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
    write(repo, "frontend/x.txt", "a")
    head = commit_all(repo)
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
    write(repo, "frontend/x.txt", "a")
    write(repo, "backend/y.txt", "a")
    h1 = commit_all(repo)
    await it.session("backend-fail", "verify.main.t", "failed", head_sha=h1)
    await it.session("frontend-pass", "verify.main.t", "done", head_sha=h1)
    # A fix commit lands touching only frontend before the cap's counter reset
    # re-enters at round 0.
    write(repo, "frontend/x.txt", "b")
    commit_all(repo)

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


#: A CLI -> a project one level down that passes only when its test command
#: runs inside that directory.
_NESTED_PROJECTS = {
    "npm": {"web/package.json": '{"scripts": {"test": "test -f package.json"}}'},
    "just": {"tools/justfile": "test:\n    test -f justfile\n"},
    "go": {
        "svc/go.mod": "module svc\n\ngo 1.20\n",
        "svc/svc_test.go": 'package svc\n\nimport "testing"\n\nfunc TestX(t *testing.T) {}\n',
    },
}


@pytest.mark.parametrize(
    "files",
    [pytest.param(f, id=cli, marks=pytest.mark.e2e(cli)) for cli, f in _NESTED_PROJECTS.items()],
)
async def test_a_probed_nested_scope_finds_its_project_from_the_worktree_root(item_on, repo, files):
    """A monorepo's probed scope, run the way verify runs every scope: from the
    worktree root, without a shell. A bare `npm test` there read
    `<worktree>/package.json` and failed, so a nested scope could never pass."""
    for path, text in files.items():
        write(repo, path, text)
    commit_all(repo)  # the probe reads the committed tree

    status, it = await _dispatch(item_on, test_scopes=probe_repo(repo)["test_scopes"])
    assert status == "done", [Path(s["log_path"]).read_text() for s in it.sessions()]
    assert it.sessions(), "no scope ran"


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
        repo_entry=entry_of(
            {
                "test_command": f'{sys.executable} -c "import os; {dump}"',
                "setup_command": "",
                "env": {"MY_REPO": "1"},
            }
        ),
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


# -- what a red scope's stop says (R12a-03) -------------------------------------


_EXIT_3 = "sh -c 'exit 3'"


@pytest.mark.parametrize(
    ("fields", "named", "facts"),
    [
        pytest.param(
            {"test_command": _EXIT_3},
            f"`{_EXIT_3}` (scope `**`, exit 3)",
            {"command": _EXIT_3, "scope": "**", "exit_code": 3},
            id="test-command",
        ),
        pytest.param(
            {
                "test_scopes": [
                    {"paths": ["src/**", "*"], "command": _EXIT_3},
                    _ALL | {"command": "true"},
                ]
            },
            f"`{_EXIT_3}` (scope `src/**, *`, exit 3)",
            {"command": _EXIT_3, "scope": "src/**, *", "exit_code": 3},
            id="a-later-scope-passes",
        ),
        pytest.param(
            {
                "test_command": "true",
                "areas": {
                    "ui": {
                        "paths": ["**"],
                        "setup": _EXIT_3,
                        "verification": {"test_scopes": [{"paths": ["**"], "command": "true"}]},
                    }
                },
            },
            f"`{_EXIT_3}` (setup of area `ui`, exit 3)",
            {"command": _EXIT_3, "exit_code": 3, "area": "ui", "setup": True},
            id="area-setup",
        ),
    ],
)
async def test_a_red_scope_stops_naming_its_command_scope_and_session(
    item_on, fields, named, facts
):
    """quick-task's verify went red as `task failed in node verify:
    test_changed_scopes [builtin]` with empty facts: neither the command nor
    its scope showed anywhere, and the session holding the output was only in
    `view events --json` (R12a-03). The reason names the command and its
    scope; `facts` carries them, the exit code and that session -- the failed
    one, even when a later scope's passing session is newer."""
    it = await item_on(_verify())

    await _walk(it, repo_entry=entry_of({"setup_command": "", **fields}))

    [failed] = [s for s in it.sessions() if s["status"] == "failed"]
    payload = it.events("work_item_needs_human")[-1]["payload"]
    assert payload["reason"] == f"task failed in node verify: t [builtin] — tests failed: {named}"
    assert payload["facts"] == {**facts, "session_id": failed["id"]}


async def test_every_red_scope_is_named_and_listed(item_on):
    """Two scopes go red in one run: the reason names both, and `facts` keeps
    the first one's keys flat (what the board's card shows) with both under
    `failed_scopes`."""
    it = await item_on(_verify())
    scopes = [{"paths": ["a/**"], "command": "false"}, {"paths": ["b/**"], "command": _EXIT_3}]

    await _walk(it, repo_entry=entry_of({"setup_command": "", "test_scopes": scopes}))

    first, second = (s["id"] for s in it.sessions())
    payload = it.events("work_item_needs_human")[-1]["payload"]
    assert payload["reason"].endswith(
        f"tests failed: `false` (scope `a/**`, exit 1); `{_EXIT_3}` (scope `b/**`, exit 3)"
    )
    one = {"command": "false", "scope": "a/**", "exit_code": 1, "session_id": first}
    two = {"command": _EXIT_3, "scope": "b/**", "exit_code": 3, "session_id": second}
    assert payload["facts"] == {**one, "failed_scopes": [one, two]}


async def test_a_red_scope_is_named_from_the_latest_run_only(item_on):
    """The newest run (its round and head) by each command's later row: a
    scope red in an earlier round, or red and then green again at the same
    head, is not what stopped the item now."""
    it = await item_on(_verify(), repo="/r")
    await _exited(it, [("s-old", "failed", "old", None)], round=0)
    rerun = [("s-flaky", "failed", "flaky", None), ("s-red", "failed", "red", None)]
    await _exited(it, [*rerun, ("s-flaky-2", "done", "flaky", None)], round=1)

    found = dispatch.failed_test_scopes(it.database, it.id, "verify", "verify.main.t", None)

    assert found == [{"command": "red", "session_id": "s-red"}]
