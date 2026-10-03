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

ACTIVE = [{"id": "a1b2", "title": "Add the parser", "current_node_id": "implementation"}]


@pytest.fixture
def board(monkeypatch):
    """The server's active items, set per test; `None` is a server that does
    not answer."""
    rows = {"active": ACTIVE}

    async def list_work_items(status=None, **kwargs):
        if rows["active"] is None:
            raise ConnectionError("no server")
        assert status == "active"
        return rows["active"]

    monkeypatch.setattr(cli.admin.client, "list_work_items", list_work_items)
    return rows


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
    assert "restarting the server ends the agent of any active item (1)" in err
    assert "a1b2  implementation  Add the parser" in err
    assert bool(restarted) is goes_on
    assert terminal.asked == (["Go on? [y/N] "] if asked else [])


@pytest.mark.parametrize("active", [[], None], ids=["none-active", "no-server"])
def test_restart_with_nothing_running_does_not_ask(board, terminal, restarted, capsys, active):
    """`no-server`: the listing gets no answer from a server that holds its
    pidfile. With no server at all, restart refuses (test_admin.py)."""
    board["active"] = active
    terminal.answer = "n"
    cli.main(["admin", "restart"])
    assert restarted
    assert terminal.asked == []
    assert "ends the agent" not in capsys.readouterr().err


def test_stop_lists_active_items_but_never_asks(board, terminal, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    run_dirs = RunDirs(tmp_path / "run")
    held = hold_pidfile(run_dirs.pid, 4171)

    def fake_kill(pid, sig):
        held.release()  # the server's exit drops its lock and its file
        run_dirs.pid.unlink()

    monkeypatch.setattr(os, "kill", fake_kill)
    terminal.answer = "n"
    cli.main(["admin", "stop"])
    captured = capsys.readouterr()
    assert "stopping the server ends the agent of any active item (1)" in captured.err
    assert "stopped (pid 4171)" in captured.out
    assert terminal.asked == []


@pytest.mark.parametrize(("answer", "installs"), [("n", False), ("y", True)], ids=["no", "yes"])
def test_update_restart_asks_before_installing(
    board, terminal, restarted, tmp_path, monkeypatch, capsys, answer, installs
):
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(tmp_path / "templates"))
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
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(tmp_path / "templates"))
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
