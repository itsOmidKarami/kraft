import asyncio
import hashlib
import json
import sys
import textwrap

import pytest
from support.harness import commit_all, git, write
from support.remote import (
    LFS_ATTRIBUTES,
    fake_git_lfs,
    lfs_pointer,
    lfs_script,
    make_rig,
    workers_file,
)

from kraft.remote import blobs, lfs
from kraft.remote import runs as runs_mod
from kraft.remote.runs import REMOTE_GAVE_UP
from kraft.remote.workdir import Copy

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


async def test_a_step_running_on_a_machine_that_is_then_disabled_finishes_and_frees_its_slot(rig):
    script = "import time; time.sleep(0.3); print('done anyway')"
    run = await rig.run([sys.executable, "-c", script])
    rig.hub.apply(workers_file(gpu={"capacity": 2, "enabled": False}))
    assert rig.hub.decide("gpu", []).wait == "waiting for gpu (disabled)"
    await rig.done(run)
    assert run.exit_code == 0 and run.spool.read_text() == "done anyway\n"
    assert rig.hub.slots_used("gpu") == 0
    assert rig.hub.decide("gpu", []).wait == "waiting for gpu (disabled)"


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


@pytest.mark.parametrize("rewrite", [False, True], ids=["local-commit", "rebase"])
async def test_a_step_after_the_branch_moved_between_steps_is_published_on_that_new_head(
    rig, database, rewrite
):
    start = rig.head
    await rig.done(await rig.run([sys.executable, "-c", COMMIT], sid="s1"))
    if rewrite:  # a gate's rebase: the first step's commit is no longer an ancestor
        git(rig.repo, "reset", "-q", "--hard", start)
    write(rig.repo, "local.txt", "l")
    git(rig.repo, "add", "-A")
    git(rig.repo, "commit", "-qm", "between steps")
    rig.head = rig.tip()
    second = COMMIT.replace("made.txt", "second.txt").replace("machine work", "second work")
    await rig.done(await rig.run([sys.executable, "-c", second], sid="s2"))
    assert git(rig.repo, "log", "-1", "--format=%s", rig.branch).strip() == "second work"
    assert git(rig.repo, "for-each-ref", "refs/kraft/unsynced").strip() == ""
    assert "sandbox_branch_not_synced" not in _event_types(database)


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


def _gitlink(repo, sha):
    """An uninitialized submodule as `git worktree add` leaves it: `.gitmodules`, a gitlink entry
    and an empty directory."""
    write(repo, ".gitmodules", '[submodule "sub"]\n\tpath = sub\n\turl = ./sub\n')
    git(repo, "add", ".gitmodules")
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},sub")
    git(repo, "commit", "-qm", "gitlink")
    (repo / "sub").mkdir()


BUMP_GITLINK = (
    "import os, subprocess; "
    "subprocess.run(['git', 'update-index', '--cacheinfo', '160000,' + '2' * 40 + ',sub'], "
    "check=True); "
    "subprocess.run(['git', 'commit', '-qm', 'bump'], check=True); "
    "open(os.environ['KRAFT_RESULT_PATH'], 'w').write('{\"status\": \"done\"}')"
)


@pytest.mark.parametrize(
    ("script", "taken"), [(COMMIT, True), (BUMP_GITLINK, False)], ids=["ordinary-file", "bump"]
)
async def test_a_repo_with_an_unused_submodule_runs_on_a_machine_but_cannot_move_its_pointer(
    rig, script, taken
):
    _gitlink(rig.repo, rig.head)
    rig.head = before = rig.tip()
    gitlink = git(rig.repo, "ls-tree", before, "sub")
    run = await rig.done(await rig.run([sys.executable, "-c", script]))
    if taken:
        assert run.exit_code == 0 and rig.tip() != before, run.spool.read_text()
        assert git(rig.repo, "ls-tree", rig.tip(), "sub") == gitlink
        assert git(rig.repo, "log", "-1", "--format=%s", rig.branch).strip() == "machine work"
    else:
        assert run.exit_code == 1 and rig.tip() == before
        assert "was not taken" in run.spool.read_text()


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

    families = (
        "remote_step_waiting",
        "remote_docs_conflict",
        "remote_step_given_up",
        "remote_machine_disconnected",
        "remote_machine_reconnected",
    )
    assert set(families) <= set(BY_VALUE)
    assert all(BY_VALUE[v] in EVENT_TRAITS for v in families)


class _Mono:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def clocked(tmp_path, database, run_dirs, item_on):
    """`await clocked(give_up_after)`: the rig, with the hub's awake clock in the test's hands."""
    made = []

    async def make(give_up_after):
        mono = _Mono()
        rig = await make_rig(
            tmp_path,
            database,
            run_dirs,
            item_on,
            hub_kwargs={"mono": mono},
            workers=workers_file(gpu={"capacity": 2, "give_up_after": give_up_after}),
        )
        made.append(rig)
        return rig, mono

    yield make
    for rig in made:
        await rig.listener.close()
        runs_mod.install(None)
        await rig.hub.close()


async def _running(rig, sid="s1"):
    run = await rig.run(["sh", "-c", "sleep 30"], sid=sid)
    while run.state != "running":
        await asyncio.sleep(0.02)
    return run


async def test_a_run_whose_machine_is_gone_give_up_after_ends_unknown_and_kills_it_on_reconnect(
    clocked, database
):
    rig, mono = await clocked("2s")
    run = await _running(rig)
    await rig.disconnect()
    mono.now += 1
    watching = asyncio.create_task(rig.runs.watch(poll_s=0.02))
    await asyncio.sleep(0.2)
    assert run.exit_code is None  # not yet: only a second of it
    mono.now += 2
    await rig.done(run)
    watching.cancel()
    assert run.exit_code == REMOTE_GAVE_UP and "given up" in run.spool.read_text()
    assert "remote_step_given_up" in _event_types(database)
    assert rig.hub.slots_used("gpu") == 0
    killed = rig.machine._jobs[run.spec.key].runner
    await rig.connect()  # the server tells it to stop; what it hands in is thrown away
    while killed._proc.poll() is None:
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.3)
    assert run.exit_code == REMOTE_GAVE_UP and not rig.files("s1").result.exists()
    assert rig.runs.find(rig.wid, "s1") is None  # told `delivered`, so the machine dropped it too
    assert run.spec.key not in rig.machine._jobs


async def test_a_given_up_run_outlives_the_exit_timer_until_the_machine_hands_in(clocked):
    rig, _ = await clocked("2h")
    run = await _running(rig)
    await rig.disconnect()
    loop, later = asyncio.get_running_loop(), []
    real = loop.call_later
    loop.call_later = lambda delay, *a: later.append(delay) or real(delay, *a)
    try:
        await rig.runs.give_up(run, "gpu has been unreachable for 2h", after="2h")
    finally:
        loop.call_later = real
    assert 600 not in later
    assert rig.runs.find(rig.wid, "s1") is run


async def test_never_does_not_give_up(clocked):
    rig, mono = await clocked("never")
    run = await _running(rig)
    await rig.disconnect()
    mono.now += 86400
    watching = asyncio.create_task(rig.runs.watch(poll_s=0.02))
    await asyncio.sleep(0.3)
    watching.cancel()
    assert run.exit_code is None
    await rig.connect()
    await rig.runs.signal(run, "KILL")
    await rig.done(run)


async def test_a_reconnect_tells_the_machine_which_items_to_keep(rig, database, item_on):
    from support.chain_builders import v1_node
    from support.harness import v1_chain

    from kraft import store

    await item_on(v1_chain([v1_node("build")], repo=rig.repo), "build", repo=rig.repo, wid="old1")
    await database.write(lambda c: store.archive_work_item(c, "old1", by="you"))
    seen = []
    original = rig.machine._on_frame

    async def spy(frame):
        seen.append(frame)
        await original(frame)

    rig.machine._on_frame = spy
    await rig.reconnect()
    await asyncio.sleep(0.3)
    assert [f["items"] for f in seen if f["t"] == "keep"] == [[rig.wid]]


async def test_a_drop_and_a_reconnect_each_note_the_item_whose_step_runs_there(
    rig, database, item_on
):
    from support.chain_builders import v1_node
    from support.harness import v1_chain

    await item_on(v1_chain([v1_node("build")], repo=rig.repo), "build", repo=rig.repo, wid="idle1")
    run = await _running(rig)
    await rig.disconnect()
    await asyncio.sleep(0.2)
    await rig.connect()
    await asyncio.sleep(0.3)
    got = database.read(
        lambda c: c.execute(
            "SELECT work_item_id, type, payload FROM events WHERE type LIKE 'remote_machine_%'"
        ).fetchall()
    )
    assert [(e["work_item_id"], e["type"]) for e in got] == [
        (rig.wid, "remote_machine_disconnected"),
        (rig.wid, "remote_machine_reconnected"),
    ]  # none for "idle1": no step of it runs on the machine
    assert json.loads(got[0]["payload"]) == {"machine": "gpu", "session_id": "s1"}
    await rig.runs.signal(run, "KILL")
    await rig.done(run)


@pytest.mark.parametrize(
    ("type_", "kept"),
    [
        ("sandbox_egress_refused", True),
        ("sandbox_branch_not_synced", True),
        ("sandbox_oom_killed", False),  # the server records its own, from `finished`
        ("item_approved", False),
    ],
    ids=["egress-refused", "not-synced", "oom-kill-is-not-an-event-frame", "not-a-sandbox-event"],
)
async def test_a_machine_adds_sandbox_events_to_its_items_history_and_nothing_else(
    rig, database, type_, kept
):
    run = await rig.run(["sh", "-c", "sleep 30"])
    await rig.runs.on_frame(
        "gpu", {"t": "event", "id": run.spec.key, "type": type_, "payload": {"host": "x.test"}}
    )
    await asyncio.sleep(0.2)
    assert (type_ in _event_types(database)) is kept
    await rig.runs.signal(run, "KILL")
    await rig.done(run)


async def test_a_job_a_full_machine_refuses_ends_the_run_with_the_machines_reason(
    tmp_path, database, run_dirs, item_on
):
    """Only a server that skipped `reserve` gets here: the hub is made to think there is room."""
    rig = await make_rig(tmp_path, database, run_dirs, item_on, machine_kwargs={"limit": lambda: 1})
    try:
        held = await _running(rig, "s1")
        rig.hub.set_machine_capacity("gpu", None)
        refused = await rig.done(await rig.run(["true"], sid="s2"))
        assert refused.exit_code == 1
        assert "gpu has no free slot of its own (1 of 1 running)" in refused.spool.read_text()
        await rig.runs.signal(held, "KILL")
        await rig.done(held)
    finally:
        await rig.listener.close()
        runs_mod.install(None)
        await rig.hub.close()


BODY_A, BODY_B = b"object a", b"object b!"
OID_A, OID_B = (hashlib.sha256(b).hexdigest() for b in (BODY_A, BODY_B))


async def _lfs_rig(rig, monkeypatch, under=""):
    """The rig's server repo gains an LFS file `a.bin` (in directory `under`, whose own
    `.gitattributes` routes it) whose object the server holds, and the machine says it has
    git-lfs."""
    fake_git_lfs(monkeypatch)
    write(rig.repo, f"{under}/.gitattributes".lstrip("/"), LFS_ATTRIBUTES)
    write(rig.repo, f"{under}/a.bin".lstrip("/"), lfs_pointer(BODY_A)[1])
    commit_all(rig.repo)
    rig.head = rig.tip()
    src = rig.run_dirs.base / "a.obj"
    src.write_bytes(BODY_A)
    lfs.take(lfs.store(rig.repo), {OID_A: (src, None)})
    await rig.reconnect()  # the hub learns the machine's caps again


@pytest.mark.parametrize("under", ["", "assets"], ids=["root", "nested"])
async def test_lfs_objects_go_to_the_machine_and_new_ones_come_back_into_the_servers_store(
    rig, monkeypatch, under
):
    await _lfs_rig(rig, monkeypatch, under)
    script = (
        "from pathlib import Path\nfrom kraft.remote import lfs\n"
        f"assert lfs.object_path(lfs.store(Path('.')), {OID_A!r}).read_bytes() == {BODY_A!r}\n"
        + lfs_script(BODY_B, under=under)
    )
    run = await rig.done(await rig.run([sys.executable, "-c", script]))
    assert run.exit_code == 0, run.spool.read_text()
    assert lfs.object_path(lfs.store(rig.repo), OID_B).read_bytes() == BODY_B
    assert git(rig.repo, "log", "-1", "--format=%s", rig.branch).strip() == "lfs"
    assert git(rig.repo, "for-each-ref", "refs/kraft/remote").strip() == ""


@pytest.mark.parametrize(
    "case",
    [
        pytest.param({"stored": b"not the bytes"}, id="forged"),
        pytest.param({"pointer": False}, id="unnamed"),
        pytest.param({"store": False}, id="unsent"),
    ],
)
async def test_a_returned_lfs_object_that_does_not_check_out_refuses_the_result(
    rig, monkeypatch, case
):
    await _lfs_rig(rig, monkeypatch)
    if "pointer" in case:  # an honest machine would not send it: this one does
        monkeypatch.setattr(
            Copy,
            "new_lfs",
            lambda self, branch, tip, exclude: {
                OID_B: lfs.object_path(lfs.store(self.bare), OID_B)
            },
        )
    before = rig.tip()
    run = await rig.done(await rig.run([sys.executable, "-c", lfs_script(BODY_B, **case)]))
    assert run.exit_code != 0 and rig.tip() == before
    assert OID_B not in lfs.held(lfs.store(rig.repo))
    assert "was not taken" in run.spool.read_text()
    assert git(rig.repo, "for-each-ref", "refs/kraft/remote").strip() == ""


async def test_the_second_lfs_step_is_sent_only_objects_the_machine_lacks(rig, monkeypatch):
    await _lfs_rig(rig, monkeypatch)
    sent = []
    real = blobs.send_blob

    async def spy(t, *, job, name, source):
        sent.append((job, name))
        await real(t, job=job, name=name, source=source)

    monkeypatch.setattr(runs_mod.blobs, "send_blob", spy)
    for sid in ("s1", "s2"):
        await rig.done(await rig.run([sys.executable, "-c", "print('ok')"], sid=sid))
    assert [(j, n) for j, n in sent if n.startswith("lfs-")] == [(f"{rig.wid}/s1", f"lfs-{OID_A}")]


@pytest.mark.parametrize("held", [5, [1], {"x": 1}, "abc"], ids=["int", "non-str", "dict", "str"])
async def test_a_have_reply_whose_lfs_is_not_a_list_of_strings_counts_as_none_held(
    rig, monkeypatch, held
):
    await _lfs_rig(rig, monkeypatch)
    real = rig.runs._ask

    async def ask(machine, frame, **kw):
        reply = await real(machine, frame, **kw)
        return {**reply, "lfs": held}

    monkeypatch.setattr(rig.runs, "_ask", ask)
    sent = []
    real_send = blobs.send_blob

    async def spy(t, *, job, name, source):
        sent.append(name)
        await real_send(t, job=job, name=name, source=source)

    monkeypatch.setattr(runs_mod.blobs, "send_blob", spy)
    run = await rig.done(await rig.run([sys.executable, "-c", "print('ok')"]))
    assert run.exit_code == 0, run.spool.read_text()
    assert f"lfs-{OID_A}" in sent


async def test_a_step_of_an_lfs_repo_given_a_machine_without_git_lfs_ends_as_a_config_error(
    rig_offline, monkeypatch
):
    rig = rig_offline
    monkeypatch.setattr(lfs, "available", lambda: False)  # a runner may have the real git-lfs
    await rig.connect()
    write(rig.repo, ".gitattributes", LFS_ATTRIBUTES)
    commit_all(rig.repo)
    rig.head = rig.tip()
    run = await rig.done(await rig.run([sys.executable, "-c", "print('ok')"]))
    assert run.exit_code == runs_mod.REMOTE_CONFIG_ERROR
    assert "has no git-lfs" in run.spool.read_text()
