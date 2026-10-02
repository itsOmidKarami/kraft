"""`kraft admin stop`, `restart` and `update --restart` name the active items
whose agents a stop ends, and the two that restart ask first in a terminal --
a split-off sibling of `tests/cli/test_admin.py`, kept under the repo's line
budget."""

from __future__ import annotations

import builtins
import os
import sys
from types import SimpleNamespace

import pytest

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
def terminal(monkeypatch):
    """A terminal answering `answer`, or `None` for no terminal at all.
    `asked` records each question."""
    state = SimpleNamespace(answer=None, asked=[])

    def fake_input(prompt=""):
        state.asked.append(prompt)
        if state.answer is None:
            pytest.fail(f"asked {prompt!r} with no terminal")
        return state.answer

    monkeypatch.setattr(sys.stdin, "isatty", lambda: state.answer is not None, raising=False)
    monkeypatch.setattr(builtins, "input", fake_input)
    return state


@pytest.fixture
def restarted(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.admin, "_restart", lambda ns: calls.append(ns))
    return calls


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
    board["active"] = active
    terminal.answer = "n"
    cli.main(["admin", "restart"])
    assert restarted
    assert terminal.asked == []
    assert "ends the agent" not in capsys.readouterr().err


def test_stop_lists_active_items_but_never_asks(board, terminal, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    run_dirs = RunDirs(tmp_path / "run")
    run_dirs.pid.parent.mkdir(parents=True, exist_ok=True)
    run_dirs.pid.write_text("4171")
    alive = [True]

    def fake_kill(pid, sig):
        if sig == 0 and not alive[0]:
            raise ProcessLookupError
        if sig != 0:
            alive[0] = False

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
        assert "nothing was installed or restarted" in capsys.readouterr().err
    assert terminal.asked == ["Go on? [y/N] "]
    assert bool(performed) is installs
    assert bool(restarted) is installs
