"""`kraft admin stop`, `restart` and `update --restart` name the active items
whose agents a stop ends, and the two that restart ask first in a terminal --
a split-off sibling of `tests/cli/test_admin.py`, kept under the repo's line
budget."""

from __future__ import annotations

import builtins
import os
import socket
import sys
import time
from types import SimpleNamespace

import pytest
from support.pidfile import hold_pidfile

from kraft import cli, update
from kraft.paths import RunDirs

ACTIVE = [
    {
        "id": "a1b2",
        "title": "Add the parser",
        "current_node_id": "implementation",
        "in_place": True,
    }
]
WORKTREE = [{**ACTIVE[0], "id": "c3d4", "in_place": False}]


@pytest.fixture
def board(monkeypatch):
    """The server's active items, set per test; `None` is a server that does
    not answer."""
    rows = {"active": ACTIVE}

    async def active_items():
        if rows["active"] is None:
            raise ConnectionError("no server")
        return rows["active"]

    monkeypatch.setattr(cli.admin.client, "active_items", active_items)
    return rows


@pytest.fixture
def workers(monkeypatch):
    """What `GET /workers` says of the local worker, set per test (`slots_used`)."""
    state = {"slots_used": 0}

    async def list_workers():
        return {"workers": [{"name": "local", **state}, {"name": "gpu", "slots_used": 9}]}

    monkeypatch.setattr(cli.admin.client, "list_workers", list_workers)
    return state


@pytest.fixture
def terminal(monkeypatch, capsys):
    """A terminal answering `answer`, or `None` for no terminal at all.
    `asked` records each question, as it stood on stderr when the answer was
    read; a question on stdout is a failure, since `kraft admin restart > log`
    would hide it."""
    state = SimpleNamespace(answer=None, asked=[])

    class Stdin:
        def isatty(self):
            return state.answer is not None

        def readline(self):
            seen = capsys.readouterr()
            assert not seen.out.endswith("? [y/N] "), "the question went to stdout"
            state.asked.append(seen.err.rsplit("\n", 1)[-1])
            if state.answer is None:
                pytest.fail("asked with no terminal")
            sys.stderr.write(seen.err)
            return state.answer + "\n"

    def no_input(prompt=""):
        pytest.fail(f"input() asked {prompt!r}: its prompt goes to stdout")

    monkeypatch.setattr(sys, "stdin", Stdin())
    monkeypatch.setattr(builtins, "input", no_input)
    return state


@pytest.fixture
def restarted(monkeypatch, tmp_path):
    """`_restart`, recorded instead of run, behind a server that holds its
    pidfile: restart refuses outright when none does."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(cli.admin, "_service_installed", lambda: False)
    held = hold_pidfile(RunDirs(tmp_path / "run").pid, 4171)
    calls = []
    monkeypatch.setattr(cli.admin, "_restart", lambda ns: calls.append(ns))
    yield calls
    held.release()


@pytest.mark.parametrize(
    ("answer", "argv", "goes_on", "asked"),
    [
        ("y", [], True, True),
        ("n", [], False, True),
        ("", [], False, True),
        ("n", ["-y"], True, False),
        (None, [], True, False),
    ],
    ids=["tty-yes", "tty-no", "tty-default-no", "tty-with-yes-flag", "no-tty-warns-only"],
)
def test_restart_lists_active_items_and_asks_in_a_terminal(
    board, terminal, restarted, capsys, answer, argv, goes_on, asked
):
    terminal.answer = answer
    if goes_on:
        cli.main(["admin", "restart", *argv])
    else:
        with pytest.raises(SystemExit) as stopped:
            cli.main(["admin", "restart", *argv])
        # Non-zero, so `kraft admin restart && ...` does not carry on.
        assert stopped.value.code == 1
    err = capsys.readouterr().err
    assert ("nothing was restarted" in err) is not goes_on
    assert "restarting the server ends the agent of any item running in place (1)" in err
    assert "a1b2  implementation  Add the parser" in err
    assert bool(restarted) is goes_on
    assert terminal.asked == (["Go on? [y/N] "] if asked else [])


@pytest.mark.parametrize(
    "active", [[], None, WORKTREE], ids=["none-active", "no-server", "worktree-items-keep-running"]
)
def test_restart_with_nothing_running_does_not_ask(board, terminal, restarted, capsys, active):
    """`no-server`: the listing gets no answer from a server that holds its
    pidfile. With no server at all, restart refuses (test_admin.py)."""
    board["active"] = active
    terminal.answer = "n"
    cli.main(["admin", "restart"])
    assert restarted
    assert terminal.asked == []
    assert "ends the agent" not in capsys.readouterr().err


@pytest.fixture
def stoppable(tmp_path, monkeypatch):
    """A server holding its pidfile that exits on SIGTERM; `.run_dirs` and `.signalled`."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    run_dirs = RunDirs(tmp_path / "run")
    held = hold_pidfile(run_dirs.pid, 4171)
    state = SimpleNamespace(run_dirs=run_dirs, signalled=[], marked_at_signal=[])

    def fake_kill(pid, sig):
        state.signalled.append(sig)
        state.marked_at_signal.append(run_dirs.stop_now.exists())
        held.release()  # the server's exit drops its lock and its file
        run_dirs.pid.unlink()

    monkeypatch.setattr(os, "kill", fake_kill)
    return state


def test_stop_lists_the_items_that_end_but_never_asks(board, terminal, stoppable, capsys):
    terminal.answer = "n"
    cli.main(["admin", "stop"])
    captured = capsys.readouterr()
    assert "stopping the server ends the agent of any item running in place (1)" in captured.err
    assert "stopped (pid 4171)" in captured.out
    assert terminal.asked == []


@pytest.mark.parametrize(
    ("slots", "items", "said"),
    [
        (2, WORKTREE, "2 local steps keep running without Kraft"),
        (1, WORKTREE, "1 local step keeps running without Kraft"),
        (2, ACTIVE, "1 local step keeps running without Kraft"),  # one of them is in place
        (1, ACTIVE, None),
        (0, WORKTREE, None),
    ],
    ids=["two", "one", "in-place-one-of-two", "only-in-place", "none"],
)
def test_stop_says_local_steps_keep_running(board, workers, stoppable, capsys, slots, items, said):
    workers["slots_used"] = slots
    board["active"] = items
    cli.main(["admin", "stop"])
    err = " ".join(capsys.readouterr().err.split())
    if said is None:
        assert "keep running" not in err and "keeps running" not in err
        return
    assert said in err
    assert "nothing answers their permission prompts or enforces their caps" in err
    assert "kraft admin stop --now stops them" in err
    assert not stoppable.run_dirs.stop_now.exists()


def test_stop_now_marks_the_stop_for_the_server_and_says_nothing_is_kept(
    board, workers, stoppable, capsys
):
    workers["slots_used"] = 2
    board["active"] = WORKTREE
    cli.main(["admin", "stop", "--now"])
    err = capsys.readouterr().err
    assert stoppable.marked_at_signal == [True]  # written before the SIGTERM
    assert "stopping the server ends the agent of any active item (1)" in err
    assert "keep running" not in err


def test_stop_now_writes_the_marker_only_when_a_server_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    cli.main(["admin", "stop", "--now"])
    assert "no server running" in capsys.readouterr().out
    assert not RunDirs(tmp_path / "run").stop_now.exists()


@pytest.mark.parametrize(("answer", "installs"), [("n", False), ("y", True)], ids=["no", "yes"])
def test_update_restart_asks_before_installing(
    board, terminal, restarted, tmp_path, monkeypatch, capsys, answer, installs
):
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(tmp_path / "templates"))
    release = SimpleNamespace(tag="v9.9.9")
    monkeypatch.setattr(update, "latest", lambda **k: release)
    monkeypatch.setattr(update, "installed", lambda: "1.0.0")
    monkeypatch.setattr(update, "is_behind", lambda r: True)
    monkeypatch.setattr(update, "shadowing_kraft", lambda: None)
    performed = []
    monkeypatch.setattr(update, "perform", lambda r: performed.append(r) or 0)
    terminal.answer = answer
    if installs:
        cli.main(["admin", "update", "--restart"])
    else:
        with pytest.raises(SystemExit) as stopped:
            cli.main(["admin", "update", "--restart"])
        assert stopped.value.code == 1
    assert terminal.asked == ["Go on? [y/N] "]
    assert bool(performed) is installs
    assert bool(restarted) is installs
    # Answering no stopped nothing, and installed nothing either.
    assert ("kraft: nothing installed or stopped" in capsys.readouterr().err) is not installs


def test_stop_does_not_wait_on_a_server_that_never_answers(tmp_path, monkeypatch, capsys):
    """A wedged server is the usual reason to stop one. Its socket still
    accepts, so the listing before SIGTERM would otherwise sit out the
    client's 30 s timeout first."""
    hung = socket.socket()
    hung.bind(("127.0.0.1", 0))
    hung.listen(8)  # the kernel completes the handshake; nobody ever reads or replies
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(tmp_path / "templates"))
    monkeypatch.setenv("KRAFT_HOST", "127.0.0.1")
    monkeypatch.setenv("KRAFT_PORT", str(hung.getsockname()[1]))
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(cli.admin, "_LIST_TIMEOUT", 0.3, raising=False)
    run_dirs = RunDirs(tmp_path / "run")
    held = hold_pidfile(run_dirs.pid, 4171)
    signalled = []

    def fake_kill(pid, sig):
        signalled.append(time.monotonic())
        held.release()  # the server's exit drops its lock and its file
        run_dirs.pid.unlink()

    monkeypatch.setattr(os, "kill", fake_kill)
    began = time.monotonic()
    try:
        cli.main(["admin", "stop"])
    finally:
        hung.close()
    assert signalled and signalled[0] - began < 5
    captured = capsys.readouterr()
    assert "did not list its active items within 0.3s" in captured.err
    assert "stopped (pid 4171)" in captured.out


def test_restart_help_says_when_it_exits_1(capsys):
    """R10D-07, R10c-06: `--help` said nothing of the exit 1 with no server, a
    change the notes call script-breaking, nor of the attached server."""
    with pytest.raises(SystemExit):
        cli.main(["admin", "restart", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "With no server running it starts nothing and exits 1" in text
    assert "attached to a terminal is stopped and not started again" in text
    assert "restart exits 1" in text


# --- a new major version -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("installed", "target", "crosses"),
    [
        ("1.4.2", "2.0.0", True),
        ("1.4.2", "1.5.0", False),
        ("2.0.0rc1", "2.0.0", False),
        ("0.0.0+source", "2.0.0", False),
        ("1.4.2", "2.0.0.dev3+g1a2b3c", False),
    ],
    ids=["1.4-to-2.0", "1.4-to-1.5", "rc-to-final", "source-checkout", "dev-build"],
)
def test_crosses_major(installed, target, crosses):
    assert cli.admin._crosses_major(installed, target) is crosses


@pytest.fixture
def new_release(monkeypatch, tmp_path):
    """`kraft admin update` finds v2.0.0 while 1.4.2 is installed, and records what it does:
    `.performed` (installs), `.marked` (tags marked for when Kraft is free)."""
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(tmp_path / "templates"))
    state = SimpleNamespace(performed=[], marked=[], running="1.4.2")
    monkeypatch.setattr(update, "latest", lambda **k: SimpleNamespace(tag="v2.0.0"))
    monkeypatch.setattr(update, "installed", lambda: "1.4.2")
    monkeypatch.setattr(update, "is_behind", lambda r: True)
    monkeypatch.setattr(update, "shadowing_kraft", lambda: None)
    monkeypatch.setattr(update, "perform", lambda r: state.performed.append(r.tag) or 0)
    monkeypatch.setattr(cli.admin, "_running_server_version", lambda: state.running)
    monkeypatch.setattr(
        cli.admin, "_update_when_free", lambda ns: state.marked.append(ns.when_free)
    )
    return state


@pytest.mark.parametrize(
    ("argv", "running", "performed", "marked", "restarts"),
    [
        (["--restart"], "1.4.2", False, ["v2.0.0"], False),
        (["--restart"], "1.4.1", False, ["v2.0.0"], False),
        (["--restart"], "2.0.0", True, [], True),
        (["--restart"], None, True, [], True),
        (["--now"], "1.4.2", True, [], True),
        ([], "1.4.2", True, [], False),
    ],
    ids=[
        "across-a-major-waits-for-free",
        "across-a-major-from-another-minor",
        "within-a-major-restarts-at-once",
        "no-server-installs-only",
        "now-restarts-at-once",
        "no-restart-installs-only",
    ],
)
def test_update_across_a_major_waits_for_running_steps(
    board, terminal, restarted, new_release, capsys, argv, running, performed, marked, restarts
):
    board["active"] = []
    new_release.running = running
    cli.main(["admin", "update", *argv])
    assert (bool(new_release.performed), new_release.marked, bool(restarted)) == (
        performed,
        marked,
        restarts,
    )
    said = capsys.readouterr().out
    assert ("running steps are not interrupted" in said) is bool(marked)


def test_update_now_marks_the_stop_for_the_server_and_asks_about_every_item(
    board, terminal, new_release, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    run_dirs = RunDirs(tmp_path / "run")
    held = hold_pidfile(run_dirs.pid, 4171)
    monkeypatch.setattr(cli.admin, "_service_installed", lambda: True)
    marked_when_restarted = []
    monkeypatch.setattr(
        cli.admin,
        "_restart_service",
        lambda: marked_when_restarted.append(run_dirs.stop_now.exists()),
    )
    board["active"] = WORKTREE
    terminal.answer = "y"
    try:
        cli.main(["admin", "update", "--now"])
    finally:
        held.release()
    assert marked_when_restarted == [True]
    assert "restarting the server ends the agent of any active item (1)" in capsys.readouterr().err
    assert terminal.asked == ["Go on? [y/N] "]


@pytest.mark.parametrize(
    ("running", "active", "refused"),
    [
        ("1.4.2", ACTIVE, True),
        ("1.4.2", WORKTREE, True),
        ("1.4.2", [], False),
        ("2.0.0", ACTIVE, False),
        (None, ACTIVE, False),
    ],
    ids=["item-active", "worktree-item-active", "idle", "same-major", "no-server"],
)
def test_restart_onto_a_new_major_with_an_item_active_is_refused(
    board, terminal, restarted, monkeypatch, capsys, running, active, refused
):
    monkeypatch.setattr(update, "installed", lambda: "2.0.0")
    monkeypatch.setattr(cli.admin, "_running_server_version", lambda: running)
    board["active"] = active
    if refused:
        with pytest.raises(SystemExit) as stopped:
            cli.main(["admin", "restart"])
        assert stopped.value.code == 1
        err = " ".join(capsys.readouterr().err.split())
        assert (
            "the installed kraft is a new major version: kraft admin restart --when-free waits "
            "for running steps, kraft admin stop --now && kraft admin start stops them" in err
        )
    else:
        terminal.answer = None
        cli.main(["admin", "restart"])
    assert bool(restarted) is not refused


@pytest.mark.parametrize("answering", [["2.3.0-abcd1234"], []], ids=["worker-running", "none"])
def test_uninstall_service_says_a_worker_is_still_finishing_its_steps(
    tmp_path, monkeypatch, capsys, answering
):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(cli.admin.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(cli.admin, "_systemd_unit_path", lambda: tmp_path / "kraft.service")
    monkeypatch.setattr(cli.admin, "_launchd_plist_path", lambda: tmp_path / "kraft.plist")
    from kraft.remote import local_serve

    monkeypatch.setattr(local_serve, "answering", lambda run_base: answering)
    cli.main(["admin", "uninstall-service"])
    said = capsys.readouterr().out
    assert ("the local worker is still finishing its steps; it exits on its own" in said) is bool(
        answering
    )
