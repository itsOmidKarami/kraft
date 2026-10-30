"""`github_queries`: the GraphQL documents the GitHub backend reads a pull
request through, the rows its rollup comes back as, and how the pull request is
picked. Against a stubbed `gh` on PATH, as test_gh.py."""

from __future__ import annotations

import pytest

from kraft.adapters import forge
from kraft.adapters.forge import github_queries as gq
from kraft.adapters.forge.transport import CliTransport

from .api_outputs import (
    FIND,
    READ_BRANCH,
    READ_MORE,
    READ_NUMBER,
    SAD_JOB,
    SAD_JOB_LOG,
    SAD_JOB_LOG_FAILED,
    check_run,
    checks_page,
    contexts_page,
    find_result,
    listed,
    pr_by_branch,
    pr_by_number,
    pull_request,
    status_context,
    step,
    workflow_run,
)
from .nodes import FAIL

_RUN_URL = "https://github.com/o/r/actions/runs/{}/job/9"


def _read(tmp_path, target, **kw):
    return gq.read_pr(CliTransport("gh", tmp_path), tmp_path, target, **kw)


@pytest.mark.parametrize(
    "nodes, rows",
    [
        pytest.param(
            [check_run("build", "SUCCESS", run=111)],
            [
                {
                    "__typename": "CheckRun",
                    "name": "build",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                    "startedAt": "2026-09-18T00:15:33Z",
                    "completedAt": "2026-09-18T00:16:33Z",
                    "detailsUrl": _RUN_URL.format(111),
                    "workflowRunId": 111,
                }
            ],
            id="check-run",
        ),
        pytest.param(
            [check_run("buildkite", None)],
            [
                {
                    "__typename": "CheckRun",
                    "name": "buildkite",
                    "status": "IN_PROGRESS",
                    "conclusion": None,
                    "startedAt": "2026-09-18T00:15:33Z",
                    "completedAt": None,
                    "detailsUrl": "sweet link",
                    "workflowRunId": None,
                }
            ],
            id="check-run-outside-actions",
        ),
        pytest.param(
            [status_context("ci/x", "SUCCESS")],
            [
                {
                    "__typename": "StatusContext",
                    "context": "ci/x",
                    "state": "SUCCESS",
                    "targetUrl": "http://example.com/details",
                    "startedAt": "2026-09-18T00:15:33Z",
                }
            ],
            id="status-context",
        ),
        pytest.param([], [], id="empty"),
    ],
)
def test_rollup_rows_export_what_gh_exports(nodes, rows):
    assert gq.rollup_rows(nodes) == rows


@pytest.mark.parametrize(
    "target, branch_read",
    [(["7"], False), (["kraft/abc"], True)],
    ids=["by-number", "by-branch"],
)
async def test_a_pull_request_is_asked_for_by_number_or_by_branch(
    cli, tmp_path, target, branch_read
):
    pr = pull_request(contexts_page([]))
    cli.stub("gh", routes={READ_NUMBER: pr_by_number(pr), READ_BRANCH: pr_by_branch(pr)})

    got = await _read(tmp_path, target)

    argv = cli.argv("gh")
    assert argv[argv.index("graphql") - 1] == "api"
    assert argv[argv.index("owner={owner}") - 1] == "-F"
    assert argv[argv.index("name={repo}") - 1] == "-F"
    var, flag = ("b=kraft/abc", "-f") if branch_read else ("n=7", "-F")
    assert argv[argv.index(var) - 1] == flag, "a branch is a string, a number typed"
    assert argv.count("graphql") == 1
    assert got["number"] == 7


async def test_a_pull_request_read_without_checks_does_not_ask_for_them(cli, tmp_path):
    cli.stub("gh", routes={READ_NUMBER: pr_by_number(pull_request())})

    got = await _read(tmp_path, ["7"], checks=False)

    query = "\n".join(cli.argv("gh"))
    assert "statusCheckRollup" not in query and "labels(first: 100)" in query
    assert "statusCheckRollup" not in got


async def test_the_checkout_own_branch_is_asked_of_git_when_nothing_is_named(cli, tmp_path):
    cli.stub("git", "kraft/own\n")
    cli.stub("gh", routes={READ_BRANCH: pr_by_branch(pull_request(headRefName="kraft/own"))})

    await _read(tmp_path, [])

    assert cli.calls("git")[0] == "symbolic-ref --short HEAD"
    assert "b=kraft/own" in cli.argv("gh")


_OPEN = {"number": 1, "state": "OPEN"}
_OLD_OPEN = {"number": 2, "state": "OPEN"}
_NEWER_CLOSED = {"number": 3, "state": "CLOSED"}


@pytest.mark.parametrize(
    "nodes, branch, number",
    [
        # Newest first as GitHub lists them: an open one beats a newer closed one.
        pytest.param([_NEWER_CLOSED, _OPEN, _OLD_OPEN], "b", 1, id="open-before-newer-closed"),
        pytest.param([_NEWER_CLOSED, {"number": 4, "state": "MERGED"}], "b", 3, id="newest-closed"),
        # A fork's pull request from a branch of the same name is not this one.
        pytest.param(
            [{"number": 5, "state": "OPEN", "isCrossRepository": True}, _NEWER_CLOSED],
            "b",
            3,
            id="cross-repository-skipped",
        ),
        # The default branch as a head: only an open pull request is its own.
        pytest.param(
            [{"number": 6, "state": "MERGED", "headRefName": "main"}, _OPEN],
            "main",
            1,
            id="closed-from-the-default-branch-skipped",
        ),
    ],
)
async def test_the_pull_request_of_a_branch_is_picked_as_gh_picks_it(
    cli, tmp_path, nodes, branch, number
):
    nodes = [{**pull_request(), "headRefName": branch, **n} for n in nodes]
    cli.stub("gh", routes={READ_BRANCH: pr_by_branch(*nodes)})

    assert (await _read(tmp_path, [branch], checks=False))["number"] == number


@pytest.mark.parametrize(
    "nodes",
    [
        pytest.param([], id="none"),
        pytest.param([pull_request(isCrossRepository=True)], id="only-from-a-fork"),
    ],
)
async def test_no_pull_request_for_the_branch_raises(cli, tmp_path, nodes):
    cli.stub("gh", routes={READ_BRANCH: pr_by_branch(*nodes)})

    with pytest.raises(forge.ForgeError, match="no pull requests found for branch 'b'"):
        await _read(tmp_path, ["b"])


async def test_a_red_context_on_the_second_page_makes_the_rollup_red(cli, tmp_path):
    """The 101st check: the first page is a hundred green ones."""
    first = contexts_page([check_run(f"c{n}", "SUCCESS") for n in range(100)], next_cursor="CUR1")
    pr = pull_request(first)
    cli.stub(
        "gh",
        routes={
            READ_NUMBER: pr_by_number(pr),
            READ_MORE: checks_page(contexts_page([check_run("c100", "FAILURE")])),
        },
        default=FAIL,
    )

    status = await forge.GhCli().ci_status(repo=tmp_path, mr=forge.MR(7, "u"))

    assert status.state == "failed"
    assert [j.name for j in status.failed_jobs] == ["c100"]
    assert len(status.jobs) == 101
    argv = cli.argv("gh")
    assert argv.count("graphql") == 2
    assert argv[argv.index("id=PR_kwDO7") - 1] == "-f"
    assert argv[argv.index("after=CUR1") - 1] == "-f"


async def test_every_page_of_contexts_is_read(cli, tmp_path):
    pr = pull_request(contexts_page([check_run("a", "SUCCESS")], next_cursor="C1"))
    pages = iter(
        [
            checks_page(contexts_page([check_run("b", "SUCCESS")], next_cursor="C2")),
            checks_page(contexts_page([check_run("c", "SUCCESS")])),
        ]
    )
    # One stub answer per route, so the second page is told apart by its cursor.
    cli.stub(
        "gh",
        routes={
            READ_NUMBER: pr_by_number(pr),
            "api*graphql*after=C1*": next(pages),
            "api*graphql*after=C2*": next(pages),
        },
        default=FAIL,
    )

    got = await _read(tmp_path, ["7"])

    assert [r["name"] for r in got["statusCheckRollup"]] == ["a", "b", "c"]
    assert got["headCommitOid"] == "abc123"


async def test_a_read_that_answers_without_the_pull_request_raises(cli, tmp_path):
    cli.stub("gh", routes={READ_NUMBER: '{"data":{"repository":{"pullRequest":null}}}'})

    with pytest.raises(forge.ForgeError, match="no pull request #7"):
        await _read(tmp_path, ["7"])


async def test_find_mr_asks_for_every_state_of_the_branch(cli, tmp_path):
    cli.stub("gh", routes={FIND: find_result(listed())})

    found = await forge.GhCli().find_mr(repo=tmp_path, branch="kraft/abc")

    assert found is not None and found.number == 7
    argv = cli.argv("gh")
    assert argv[argv.index("b=kraft/abc") - 1] == "-f"
    assert (argv[argv.index("owner={owner}") - 1], argv[argv.index("name={repo}") - 1]) == (
        "-F",
        "-F",
    )
    assert "first: 5" in "\n".join(argv)


def _line(second: str, text: str) -> str:
    return f"2026-10-21T10:00:{second}Z {text}\n"


def _out(job: str, step_name: str, *lines: str) -> str:
    return "".join(f"{job}\t{step_name}\t{line}" for line in lines)


_L = [_line(f"{n:02d}.5000000", f"line {n}") for n in range(0, 40, 10)]


@pytest.mark.parametrize(
    "job, log, expected",
    [
        pytest.param(SAD_JOB, SAD_JOB_LOG, SAD_JOB_LOG_FAILED, id="gh-sad-job"),
        # Both ends are whole seconds, inclusive: 10..30 holds lines 10, 20, 30.
        pytest.param(
            {"name": "j", "steps": [step(1, "s", "failure", "10:00:10", "10:00:30")]},
            "".join(_L),
            _out("j", "s", *_L[1:]),
            id="window-ends-are-inclusive-seconds",
        ),
        pytest.param(
            {"name": "j", "steps": [step(1, "s", "failure", "10:00:10.9", "10:00:20.1")]},
            "".join(_L),
            _out("j", "s", *_L[1:3]),
            id="start-is-floored-end-is-its-second",
        ),
        # Step order, not the order the API listed them in; a cancelled step is no failure.
        pytest.param(
            {
                "name": "j",
                "steps": [
                    step(3, "late", "TIMED_OUT", "10:00:30", "10:00:30"),
                    step(2, "mid", "cancelled", "10:00:20", "10:00:20"),
                    step(1, "early", "action_required", "10:00:00", "10:00:00"),
                ],
            },
            "".join(_L),
            _out("j", "early", _L[0]) + _out("j", "late", _L[3]),
            id="failed-steps-in-step-order-cancelled-skipped",
        ),
        # gh's fallback: no step log to cut, the whole job log under UNKNOWN STEP.
        pytest.param(
            {"name": "j", "steps": [step(1, "s", "success", "10:00:00", "10:00:40")]},
            "".join(_L),
            _out("j", "UNKNOWN STEP", *_L),
            id="no-failed-step",
        ),
        pytest.param(
            {"name": "j", "steps": [step(1, "s", "cancelled", "10:00:00", "10:00:40")]},
            "".join(_L),
            _out("j", "UNKNOWN STEP", *_L),
            id="a-cancelled-step-is-not-failed",
        ),
        pytest.param(
            {"name": "j", "steps": [step(1, "s", "failure", "10:05:00", "10:05:10")]},
            "".join(_L),
            _out("j", "UNKNOWN STEP", *_L),
            id="no-line-in-the-window",
        ),
        pytest.param(
            {"name": "j", "steps": [{"name": "s", "number": 1, "conclusion": "failure"}]},
            "".join(_L),
            _out("j", "UNKNOWN STEP", *_L),
            id="a-failed-step-without-times",
        ),
        pytest.param({"name": "j"}, "", "", id="no-log-no-output"),
        # A line carries one step, and a log may start with a byte order mark.
        pytest.param(
            {
                "name": "j",
                "steps": [
                    step(1, "a", "failure", "10:00:00", "10:00:10"),
                    step(2, "b", "failure", "10:00:10", "10:00:20"),
                ],
            },
            "\ufeff" + "".join(_L),
            _out("j", "a", "\ufeff" + _L[0], _L[1]) + _out("j", "b", _L[2]),
            id="a-boundary-line-goes-to-the-first-step",
        ),
    ],
)
def test_failed_step_log_cuts_the_log_as_gh_log_failed_prints_it(job, log, expected):
    assert gq.failed_step_log(job, log) == expected


def test_run_rows_rename_keys_only():
    data = {"workflow_runs": [workflow_run(5, name="t", conclusion="failure"), "junk"]}

    assert gq.run_rows(data) == [
        {
            "databaseId": 5,
            "name": "t",
            "headBranch": "main",
            "headSha": "deadbeef",
            "event": "push",
            "status": "completed",
            "conclusion": "failure",
            "url": "https://github.com/o/r/actions/runs/5",
            "createdAt": "2026-09-19T05:51:00Z",
            "updatedAt": "2026-09-19T05:51:09Z",
            "repository": {"id": 1},
            "head_repository": {"id": 1},
        }
    ]
    assert gq.run_rows({}) == gq.run_rows([]) == []
