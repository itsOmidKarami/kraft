"""One item's tab, against a fake `kraft`."""

from __future__ import annotations

import pytest
from herdr_fakes import Fakes

import kraft_herdr


def shown(*session_ids, status="running"):
    return {
        "stdout": {
            "id": "aaaaaaaa1",
            "display_status": status,
            "worker_sessions": [{"id": s} for s in session_ids],
        }
    }


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    return Fakes(tmp_path / "fakes").install(monkeypatch)


def tab(rounds):
    kraft_herdr.item_tab("kraft", "aaaaaaaa1", poll=0, max_rounds=rounds)


def test_item_tab_waits_for_a_first_session(fakes, capsys):
    """Filed paused, or queued: nothing to follow yet, and the process must stay
    up, or the pane becomes a shell and Herdr clears the agent."""
    fakes.reply("kraft", "view show", [shown(), shown(), shown("s1")])
    fakes.reply("kraft", "view logs", {"stdout": "a log line\n"})
    tab(4)
    assert len(fakes.calls("kraft", "view show")) == 4
    assert len(fakes.calls("kraft", "view logs")) == 1
    out = capsys.readouterr().out
    assert out.count("waiting for a worker session") == 1  # said once, not every poll


def test_item_tab_survives_logs_failing_with_no_session(fakes):
    """`view logs` refusing with "no worker session" is the same wait, not a crash."""
    fakes.reply("kraft", "view show", shown("s1"))
    fakes.reply(
        "kraft",
        "view logs",
        [
            {"exit": 1, "stderr": "no worker session has run for this work item yet\n"},
            {"exit": 1, "stderr": "no worker session has run for this work item yet\n"},
            {"stdout": "line\n"},
        ],
    )
    tab(5)
    assert len(fakes.calls("kraft", "view logs")) == 3


def test_item_tab_follows_the_next_session_after_one_ends(fakes):
    fakes.reply("kraft", "view show", [shown("s1"), shown("s1"), shown("s1", "s2")])
    fakes.reply("kraft", "view logs", {"stdout": "line\n"})
    tab(5)
    sessions = [a[a.index("--session") + 1] for a in fakes.calls("kraft", "view logs")]
    assert sessions == ["s1", "s2"]  # each once: an ended session is not replayed
    assert all(a[-1] == "-f" for a in fakes.calls("kraft", "view logs"))


def test_item_tab_prints_finished_once(fakes, capsys):
    fakes.reply("kraft", "view show", shown("s1", status="done"))
    fakes.reply("kraft", "view logs", {"stdout": ""})
    tab(6)
    assert capsys.readouterr().out.count("finished: done") == 1


def test_item_tab_keeps_going_when_kraft_is_down(fakes, capsys):
    fakes.reply("kraft", "view show", [{"exit": 1, "stderr": "refused"}, shown("s1")])
    fakes.reply("kraft", "view logs", {"stdout": ""})
    tab(3)
    assert len(fakes.calls("kraft", "view logs")) == 1


def test_item_main_needs_an_item(monkeypatch, capsys):
    monkeypatch.delenv("KRAFT_ITEM", raising=False)
    assert kraft_herdr.main(["item"]) == 2
    assert "KRAFT_ITEM" in capsys.readouterr().err


def test_the_resume_command_restarts_this_item_tab():
    argv = kraft_herdr.resume_command("aaaaaaaa1")
    assert argv[-2:] == ["item", "aaaaaaaa1"] and argv[1].endswith("kraft_herdr.py")
