"""`ci.pipeline`, the `pipeline_wait` handler: one look at one pipeline by id.
It stops for a person on anything it will not judge or retry, and writes to the
forge never (`ci-pipeline-never-writes-to-the-forge`). Parking and the scheduler
are tests/test_waits.py; the reads are test_glab_reads.py and test_gh_reads.py."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from support.harness import own_files

from kraft import item_paths
from kraft.adapters import forge

from .nodes import forge_node

HOOK = "wait.main.wait"
PID = "123"
RED = (forge.FailedJob("test", "failed", "script_failure"),)
RUNNER = (forge.FailedJob("test", "failed", "runner_system_failure"),)
NOT_FOUND = forge.ForgeError("404 Not Found: pipelines/123", status=404, reason="not_found")
OUTAGE = forge.ForgeError("glab: dial tcp: connection refused")
UNREACHABLE = forge.ForgeError("connect timed out", reason="unreachable")


def pipe(state: str = "success", **kw) -> forge.CIStatus:
    return forge.CIStatus(state=state, url="http://fake.forge/p", jobs=("pipeline 123",), **kw)


def fake_of(*script) -> forge.FakeForge:
    return forge.FakeForge(pipeline_statuses={PID: list(script)})


async def look(run_forge, fake, sid="s1", **kw):
    kw = {"pipeline": PID, "hook_point": HOOK, **kw}
    return await run_forge(fake, "pipeline_wait", sid, **kw)


def an_hour_ago() -> str:
    return (datetime.now(UTC) - timedelta(hours=1)).isoformat()


@pytest.mark.parametrize(
    "status, want, says",
    [
        pytest.param(pipe(), ("done", "done"), "pipeline success", id="success"),
        pytest.param(pipe("pending"), ("waiting", "waiting"), "pipeline pending", id="running"),
        pytest.param(
            pipe("failed", failed_jobs=RED),
            ("failed", "failed"),
            "pipeline failed",
            id="red",
        ),
        pytest.param(
            pipe("failed", failed_jobs=RUNNER),
            ("infra_stop", "infra_stop"),
            "forge's own infrastructure: runner_system_failure",
            id="infra-red",
        ),
        pytest.param(
            pipe("failed", nothing_ran=True),
            ("infra_stop", "infra_stop"),
            "ran nothing",
            id="nothing-ran",
        ),
        pytest.param(
            pipe("pending", cancelled_at=an_hour_ago()),
            ("infra_stop", "infra_stop"),
            "was cancelled",
            id="cancelled-after-grace",
        ),
    ],
)
async def test_pipeline_wait(run_forge, status, want, says):
    fake = fake_of(status)

    assert await look(run_forge, fake) == want

    log = run_forge.log("s1")
    assert says in log
    assert fake.retried == [], "never retries a job"
    if want[0] == "infra_stop":
        first = log.splitlines()[0]
        assert says in first, "the cause leads the log, where the stop reads it"
        [stop] = run_forge.events("ci_infra_exhausted") or run_forge.events("ci_run_abandoned")
        assert says in stop["reason"] or "cancelled" in stop["reason"]


async def test_pipeline_wait_red_writes_one_finding_per_failed_job(run_forge, run_dirs):
    await look(run_forge, fake_of(pipe("failed", failed_jobs=RED)))

    payload = json.loads(own_files(run_dirs, "s1").result.read_text())
    assert [f["source_plugin"] for f in payload["findings"]] == [HOOK]
    assert "test" in payload["findings"][0]["message"]


async def test_pipeline_wait_blocked_on_a_manual_job_waits_and_says_which(run_forge):
    fake = fake_of(pipe("pending", blocked_on="deploy-prod"))

    assert await look(run_forge, fake) == ("waiting", "waiting")

    assert "deploy-prod" in run_forge.log("s1")
    [seen] = run_forge.events("external_wait_observed")
    assert seen["blocked_on"] == "deploy-prod"


async def test_pipeline_wait_that_never_settles_times_out_for_a_person(run_forge, wait_clock):
    fake = fake_of(pipe("pending"))

    first = await look(run_forge, fake, "s1")
    wait_clock.advance(90 * 60)
    last = await look(run_forge, fake, "s2")

    assert (first[0], last[0]) == ("waiting", "capped_out")
    assert "timed out" in run_forge.log("s1"), "a pending wait reuses its session"


async def test_pipeline_wait_not_found_tells_which_id_to_give_and_never_quotes_it(run_forge):
    assert await look(run_forge, fake_of(NOT_FOUND)) == ("config_error", "config_error")

    log = run_forge.log("s1")
    assert "pipeline id (GitLab: not the iid)" in log and "run number" in log
    assert PID not in log, "the worker's value is never echoed"
    assert "404 Not Found" in log, "the forge's own words stay"


@pytest.mark.parametrize(
    "outage", [OUTAGE, UNREACHABLE], ids=["cli-outage", "transport-unreachable"]
)
async def test_pipeline_wait_keeps_waiting_through_an_outage_then_settles(run_forge, outage):
    fake = fake_of(outage, pipe())

    first = await look(run_forge, fake, "s1")
    second = await look(run_forge, fake, "s2")

    assert (first[0], second[0]) == ("waiting", "done")
    assert "still waiting" in run_forge.log("s1")


@pytest.mark.parametrize(
    "error, want",
    [
        pytest.param(
            forge.ForgeError("denied (HTTP 401)", status=401, reason="auth"),
            ("failed", "failed"),
            id="auth-error",
        ),
        pytest.param(
            forge.ForgeError("slow down", status=429, reason="rate_limited", retry_after=30),
            ("rate_limited", "rate_limited"),
            id="rate-limited",
        ),
    ],
)
async def test_pipeline_wait_lets_other_forge_errors_take_their_usual_stop(run_forge, error, want):
    assert await look(run_forge, fake_of(error)) == want


async def test_pipeline_wait_auth_failure_keeps_the_forge_cause(run_forge, run_dirs):
    await look(run_forge, fake_of(forge.ForgeError("401", status=401, reason="auth")))

    record = json.loads(own_files(run_dirs, "s1").log.with_suffix(".forge.json").read_text())
    assert record["reason"] == "auth"


async def test_pipeline_wait_never_writes(run_forge, database):
    fake = fake_of(pipe("failed", failed_jobs=RUNNER))
    fake.push = AsyncMock()

    await look(run_forge, fake)

    assert (fake.retried, fake.pushed, fake.find_mr_calls) == ([], [], 0)
    fake.push.assert_not_called()
    row = database.read(
        lambda c: c.execute("SELECT ci_pipeline_ref FROM work_items WHERE id = 'w1'").fetchone()
    )
    assert row["ci_pipeline_ref"] is None


async def test_pipeline_wait_ignores_ci_checks_false(run_forge):
    fake = fake_of(pipe())

    assert await look(run_forge, fake, ci_checks=False) == ("done", "done")

    assert fake.pipeline_status_ids == [PID]


@pytest.mark.parametrize("value", ["12a", "", None], ids=["letters", "empty", "missing"])
async def test_pipeline_wait_bad_value_closes_waiting_row(run_forge, value):
    """A configuration stop settles the wait a pending look had parked."""
    fake = fake_of(pipe("pending"))
    assert (await look(run_forge, fake, "s1"))[0] == "waiting"

    assert (await look(run_forge, fake, "s2", pipeline=value))[0] == "config_error"

    assert [e["outcome"] for e in run_forge.events("external_wait_ended")] == ["settled"]
    assert "digits only" in run_forge.log("s1")
    assert not value or value not in run_forge.log("s1")


# --- a workspace item reads the root repository, once ---------------------------


async def _workspace_read(item_on, database, run_dirs, tmp_path, monkeypatch, fake, changed):
    """`pipeline_wait` on a root with one submodule member, `changed` or not
    (an unchanged one is filtered out of the item's targets). Returns the
    session's status, the repositories the forge was asked in, and the root's
    path as the item recorded it."""
    from support.harness import commit_all, write

    from .test_run_chain import _multi_repo_item, _run_task, _with_origin

    it, worktree, branch = await _multi_repo_item(item_on, database, run_dirs, tmp_path, "ignore")
    _with_origin(tmp_path, it, worktree)
    if changed:
        for repo in (worktree / "repos" / "pkg", worktree):
            write(repo, "new.txt", "x\n")
            commit_all(repo)
    asked = []
    read = fake.pipeline_status

    async def spy(*, repo, pipeline_id):
        asked.append(repo)
        return await read(repo=repo, pipeline_id=pipeline_id)

    fake.pipeline_status = spy
    status = await _run_task(
        database,
        run_dirs,
        it,
        worktree,
        branch,
        fake,
        monkeypatch,
        handler="pipeline_wait",
        pipeline=PID,
    )
    root = database.read(
        lambda c: c.execute(
            "SELECT repo_path FROM work_item_repos WHERE work_item_id = ? AND role = 'root'",
            (it.id,),
        ).fetchone()
    )["repo_path"]
    return status, asked, root


@pytest.mark.parametrize("changed", [True, False], ids=["targets", "no-targets"])
async def test_pipeline_wait_workspace_reads_root(
    item_on, database, run_dirs, tmp_path, monkeypatch, changed
):
    """With every target filtered out, a missing read would be a false `done`."""
    status, asked, root = await _workspace_read(
        item_on, database, run_dirs, tmp_path, monkeypatch, fake_of(pipe()), changed
    )

    assert status == "done"
    assert [str(r) for r in asked] == [root]


async def test_pipeline_wait_workspace_keeps_findings(
    item_on, database, run_dirs, tmp_path, monkeypatch
):
    fake = fake_of(pipe("failed", failed_jobs=RED))

    status, _, _ = await _workspace_read(
        item_on, database, run_dirs, tmp_path, monkeypatch, fake, True
    )

    assert status == "failed"
    payload = json.loads(own_files(run_dirs, "s1").result.read_text())
    assert len(payload["findings"]) == 1


# --- the id read from a step output ---------------------------------------------

A, B = "n.find.a", "n.find.b"
VAR = ("PIPELINE_ID",)


def _snapshot(run_dirs, writer: str, env: str) -> None:
    folder = item_paths.snapshot_dir(run_dirs, "w1", writer)
    folder.mkdir(parents=True)
    (folder / "variables.env").write_text(env)


@pytest.mark.parametrize(
    "snapshots, declared, status, read, says, lacks",
    [
        pytest.param({A: "PIPELINE_ID=123\n"}, {A: VAR}, "done", ["123"], "", "", id="set"),
        pytest.param(
            {A: "PIPELINE_ID=999\n", B: "PIPELINE_ID=123\n"},
            {A: VAR, B: VAR},
            "done",
            ["123"],
            "",
            "",
            id="later-writer-wins",
        ),
        pytest.param(
            {},
            {A: VAR, B: VAR},
            "config_error",
            [],
            f"$PIPELINE_ID is not set: none of {A}, {B}",
            "",
            id="unset",
        ),
        pytest.param(
            {A: "PIPELINE_ID=123\n"},
            {A: ()},
            "config_error",
            [],
            "$PIPELINE_ID is not set",
            "123",
            id="undeclared-in-current-chain",
        ),
        pytest.param(
            {A: "PIPELINE_ID=abc-secret\n"},
            {A: VAR},
            "config_error",
            [],
            "digits only",
            "abc-secret",
            id="value-not-digits",
        ),
    ],
)
async def test_pipeline_from_variable(
    run_forge, run_dirs, snapshots, declared, status, read, says, lacks
):
    for writer, env in snapshots.items():
        _snapshot(run_dirs, writer, env)
    fake = fake_of(pipe())

    got = await look(run_forge, fake, pipeline="$PIPELINE_ID", reads=(A, B), declared=declared)

    assert got == (status, status)
    assert fake.pipeline_status_ids == read
    assert says in run_forge.log("s1")
    assert not lacks or lacks not in run_forge.log("s1")


async def test_a_variable_that_goes_unset_closes_the_parked_wait(run_forge, run_dirs):
    """The writer was skipped while the wait was parked: a configuration stop,
    and the wait's row is settled, not left open."""
    _snapshot(run_dirs, A, "PIPELINE_ID=123\n")
    fake = fake_of(pipe("pending"))
    args = {"pipeline": "$PIPELINE_ID", "reads": (A,), "declared": {A: VAR}}
    assert (await look(run_forge, fake, "s1", **args))[0] == "waiting"
    item_paths.snapshot_dir(run_dirs, "w1", A).joinpath("variables.env").unlink()

    assert (await look(run_forge, fake, "s2", **args))[0] == "config_error"

    assert [e["outcome"] for e in run_forge.events("external_wait_ended")] == ["settled"]


async def test_a_walk_hands_the_writers_variable_to_the_wait(
    item_on, database, run_dirs, monkeypatch
):
    """Through dispatch: a subprocess task leaves PIPELINE_ID, the next node's
    `ci.pipeline` reads it by naming the writer, and the forge is asked for it."""
    import shlex
    import sys

    from support.harness import entry_of

    from kraft import executor

    write = shlex.quote(
        "open(__import__('os').environ['KRAFT_OUTPUT_DIR'] + '/variables.env', 'w')"
        ".write('PIPELINE_ID=123\\n')"
    )
    emit = {
        "id": "emit",
        "kind": "exec",
        "tasks": [
            {
                "id": "emit",
                "kind": "subprocess",
                "command": f"{shlex.quote(sys.executable)} -c {write}",
                "outputs": {"variables": ["PIPELINE_ID"]},
            }
        ],
    }
    wait = forge_node("wait", "ci.pipeline", pipeline="$PIPELINE_ID", reads=["emit.main.emit"])
    it = await item_on([emit, wait])
    fake = fake_of(pipe())
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)

    status = await executor.run(
        database,
        run_dirs,
        work_item_id=it.id,
        launch=executor.LaunchContext(entry_of({"setup_command": "", "forge": "github"})),
    )

    assert status == "completed"
    assert fake.pipeline_status_ids == ["123"]
