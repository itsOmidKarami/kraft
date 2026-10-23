import asyncio
import sys
import textwrap

import pytest
from support.harness import git, write

COMMIT = textwrap.dedent(
    """
    import os, subprocess
    open('made.txt', 'w').write('from the machine')
    subprocess.run(['git', 'add', '-A'], check=True)
    subprocess.run(['git', 'commit', '-qm', 'machine work'], check=True)
    open(os.environ['KRAFT_RESULT_PATH'], 'w').write('{"status": "done"}')
    print('worked')
    """
)


def _event_types(database):
    return [
        e["type"] for e in database.read(lambda c: c.execute("SELECT type FROM events").fetchall())
    ]


async def test_a_step_runs_on_the_machine_and_its_result_comes_home_before_it_is_over(
    rig,
):
    run = await rig.done(await rig.run([sys.executable, "-c", COMMIT]))
    assert run.exit_code == 0
    assert (
        rig.repo / "made.txt"
    ).read_text() == "from the machine"  # the worktree followed the branch
    assert git(rig.repo, "log", "-1", "--format=%s", rig.branch).strip() == "machine work"
    assert rig.files("s1").result.read_text() == '{"status": "done"}'
    assert run.spool.read_text() == "worked\n"
    assert (
        git(rig.repo, "for-each-ref", "refs/kraft/remote").strip() == ""
    )  # the staging ref is gone


async def test_the_second_step_is_sent_only_what_the_machine_lacks_and_sees_the_first_steps_work(
    rig,
):
    await rig.done(await rig.run([sys.executable, "-c", COMMIT], sid="s1"))
    rig.head = rig.tip()
    second = await rig.done(
        await rig.run(
            [sys.executable, "-c", "import pathlib; print(pathlib.Path('made.txt').read_text())"],
            sid="s2",
        )
    )
    assert second.spool.read_text() == "from the machine\n"


async def test_a_branch_that_moved_while_the_step_ran_is_kept_unsynced_and_not_overwritten(
    rig, database
):
    slow = COMMIT + "\nimport time; time.sleep(0.5)"
    run = await rig.run([sys.executable, "-c", slow])
    await asyncio.sleep(0.2)
    write(rig.repo, "other.txt", "o")
    git(rig.repo, "add", "-A")
    git(rig.repo, "commit", "-qm", "moved")
    moved = rig.tip()
    await rig.done(run)
    assert rig.tip() == moved
    unsynced = git(rig.repo, "for-each-ref", "--format=%(refname)", "refs/kraft/unsynced").split()
    assert len(unsynced) == 1
    assert "sandbox_branch_not_synced" in _event_types(database)


async def test_a_bundle_that_plants_a_repository_fails_the_step_and_touches_nothing(rig):
    plant = (
        "import subprocess; "
        "subprocess.run(['git','update-index','--add','--cacheinfo',"
        "'160000','1'*40,'evil'],check=True); "
        "subprocess.run(['git','commit','-qm','x'],check=True); "
        "open(__import__('os').environ['KRAFT_RESULT_PATH'],'w').write('{\"status\":\"done\"}')"
    )
    before = rig.tip()
    run = await rig.done(await rig.run([sys.executable, "-c", plant]))
    assert run.exit_code == 1 and rig.tip() == before and not rig.files("s1").result.exists()
    assert "evil" in run.spool.read_text() and "was not taken" in run.spool.read_text()


async def test_a_document_a_person_edited_while_the_step_ran_keeps_the_servers_version(
    rig, database
):
    docs = rig.run_dirs.base / "items" / rig.wid / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "spec.md").write_text("v1")
    script = (
        "import os,time; p=os.path.join(os.environ['KRAFT_DOCS_DIR'],'spec.md'); "
        "time.sleep(0.4); open(p,'w').write('the step')"
    )
    run = await rig.run([sys.executable, "-c", script])
    await asyncio.sleep(0.2)
    (docs / "spec.md").write_text("a person")
    await rig.done(run)
    assert (docs / "spec.md").read_text() == "a person"
    assert (rig.files("s1").folder / "conflicts" / "spec.md").read_text() == "the step"
    assert "remote_docs_conflict" in _event_types(database)


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("POST", "/api/work-items/{wid}/progress", 200),
        ("GET", "/api/work-items/other", 403),
        ("POST", "/api/workers", 403),
    ],
    ids=["its-own-item", "another-item", "machine-management"],
)
async def test_the_agents_kraft_calls_cross_as_requests_held_to_its_own_sessions_allowlist(
    rig, method, path, status
):
    run = await rig.run(["sh", "-c", "sleep 1"])
    while run.state != "running":
        await asyncio.sleep(0.02)
    reply = await rig.machine.call_server(
        run.spec.key, method, path.format(wid=rig.wid), body=b"{}"
    )
    assert reply[0] == status
    assert [c[1] for c in rig.calls] == ([path.format(wid=rig.wid)] if status == 200 else [])
    assert all(c[2] == f"{rig.wid}/s1" for c in rig.calls)  # identity is the connection's
    await rig.runs.signal(run, "KILL")
    await rig.done(run)


async def test_the_log_resumes_byte_for_byte_after_the_connection_is_cut_mid_step(rig):
    script = (
        "import sys,time\nfor i in range(1,6):\n    print(f'line{i}', flush=True); time.sleep(0.15)"
    )
    run = await rig.run([sys.executable, "-c", script])
    while run.offset == 0:
        await asyncio.sleep(0.02)
    await rig.reconnect()  # closes the pair, dials a fresh one
    await rig.done(run)
    assert run.spool.read_text() == "".join(f"line{i}\n" for i in range(1, 6))


async def test_co_tasks_of_one_step_share_the_machines_copy_without_tripping_over_each_other(rig):
    first = await rig.run(
        [sys.executable, "-c", "import time; time.sleep(0.3); print('a')"], sid="a"
    )
    second = await rig.run(
        [sys.executable, "-c", "import time; time.sleep(0.3); print('b')"], sid="b"
    )
    await rig.done(first)
    await rig.done(second)
    assert (first.exit_code, second.exit_code) == (0, 0)
    assert (first.spool.read_text(), second.spool.read_text()) == ("a\n", "b\n")


async def test_int_pauses_and_kill_ends_the_step_through_the_machine(rig):
    run = await rig.run(["sh", "-c", "trap 'echo got-int; exit 130' INT; sleep 30 & wait"])
    while run.state != "running":
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.2)
    await rig.runs.signal(run, "INT")
    await rig.done(run)
    assert run.exit_code == 130 and "got-int" in run.spool.read_text()


async def test_a_frame_for_a_run_on_another_machine_is_ignored(rig):
    run = await rig.run(["sh", "-c", "sleep 1"])
    await rig.runs.on_frame("somebody-else", {"t": "finished", "id": run.spec.key, "code": 0})
    assert run.exit_code is None
    await rig.runs.signal(run, "KILL")
    await rig.done(run)


async def test_cancelling_while_the_machine_is_offline_ends_the_run_and_discards_its_later_hand_in(
    rig,
):
    run = await rig.run([sys.executable, "-c", COMMIT + "\nimport time; time.sleep(30)"])
    while run.state != "running":
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.5)  # it has committed and written its result
    await rig.disconnect()
    await rig.runs.signal(run, "KILL")
    assert run.exit_code == 143 and "offline" in run.spool.read_text()
    before = rig.tip()
    await rig.reconnect()  # the server tells it to stop; it hands in what it has
    await asyncio.sleep(1.0)
    assert rig.runs.find(rig.wid, "s1") is None
    assert (
        rig.tip() == before
        and not rig.files("s1").result.exists()
        and rig.hub.slots_used("gpu") == 0
    )


def test_the_remote_event_family_is_registered():
    from kraft.vocab.events import BY_VALUE, EVENT_TRAITS

    assert {"remote_step_waiting", "remote_docs_conflict"} <= set(BY_VALUE)
    assert all(BY_VALUE[v] in EVENT_TRAITS for v in ("remote_step_waiting", "remote_docs_conflict"))
