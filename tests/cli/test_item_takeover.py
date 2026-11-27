"""`kraft item takeover`: the CLI runs the agent in the foreground on the person's
terminal between taking the lock and giving it back."""

from __future__ import annotations

import os

import pytest

from kraft import cli

BEGAN = {
    "argv": ["claude", "--session-id", "U"],
    "cwd": "/work/tree",
    "session_id": "U",
    "resumed_from": "s0",
}


@pytest.fixture
def takeover(monkeypatch):
    """The two server calls and the child process, recorded: `calls` is what the
    CLI did, in order; `ran` the child's arguments."""
    calls: list[str] = []
    ran: dict = {}
    answers = {"began": BEGAN, "ended": {"status": "paused", "recorded": True, "warnings": []}}

    async def begin(wid, *, pid, pid_start):
        calls.append("begin")
        ran["lock_holder"] = (pid, pid_start)
        return answers["began"]

    async def end(wid=None):
        calls.append("end")
        return answers["ended"]

    def run(argv, **kwargs):
        calls.append("run")
        ran.update(argv=argv, **kwargs)
        if "raises" in answers:
            raise answers["raises"]

    monkeypatch.setattr(cli.item.client, "takeover_begin", begin)
    monkeypatch.setattr(cli.item.client, "takeover_end", end)
    monkeypatch.setattr(cli.item.subprocess, "run", run)
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID", raising=False)
    monkeypatch.delenv("KRAFT_SESSION_ID", raising=False)
    return calls, ran, answers


def test_takeover_refused_inside_a_worker(takeover, monkeypatch, capsys):
    calls, _, _ = takeover
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "w1")

    with pytest.raises(SystemExit) as stopped:
        cli.main(["item", "takeover", "w1"])

    assert stopped.value.code == 1
    assert "a worker does not take over an item" in capsys.readouterr().err
    assert calls == []


def test_takeover_runs_the_agent_between_the_lock_and_its_release(takeover, capsys):
    calls, ran, _ = takeover

    cli.main(["item", "takeover", "w1"])

    assert calls == ["begin", "run", "end"]
    assert ran["argv"] == BEGAN["argv"] and ran["cwd"] == BEGAN["cwd"]
    assert ran["lock_holder"][0] == os.getpid()  # the CLI holds the lock, not the agent
    assert "forking your session from s0" in capsys.readouterr().out


def test_takeover_says_when_it_starts_fresh(takeover, capsys):
    _, _, answers = takeover
    answers["began"] = {**BEGAN, "resumed_from": None}

    cli.main(["item", "takeover", "w1"])

    assert "starting a fresh session (nothing to resume)" in capsys.readouterr().out


def test_takeover_strips_worker_env_from_the_child(takeover, monkeypatch):
    """Review Focus 5: the person's own `kraft item ...` calls from inside the session
    are not a worker acting on its own item."""
    _, ran, _ = takeover
    monkeypatch.setenv("KRAFT_HOME", "/kept")
    # Taken after the CLI's own check: a worker would have been refused before this.
    monkeypatch.setattr(
        cli.item.os, "environ", {"KRAFT_SESSION_ID": "w1/s1", "KRAFT_HOME": "/kept"}
    )

    cli.main(["item", "takeover", "w1"])

    assert ran["env"] == {"KRAFT_HOME": "/kept"}


def test_takeover_releases_when_the_child_fails(takeover, capsys):
    calls, _, answers = takeover
    answers["raises"] = FileNotFoundError("claude")

    with pytest.raises(SystemExit) as stopped:
        cli.main(["item", "takeover", "w1"])

    assert stopped.value.code == 1
    assert "could not start claude" in capsys.readouterr().err
    assert calls == ["begin", "run", "end"]


@pytest.mark.parametrize(
    ("status", "recorded", "expect"),
    [
        pytest.param(
            "paused",
            True,
            ["kraft item resume w1", "continues your conversation", "kraft item skip w1"],
            id="paused",
        ),
        pytest.param(
            "needs_human",
            False,
            ["kraft item retry w1", "won't carry over", "kraft item skip w1"],
            id="needs-human",
        ),
    ],
)
def test_takeover_prints_next_steps(takeover, capsys, status, recorded, expect):
    _, _, answers = takeover
    answers["ended"] = {"status": status, "recorded": recorded, "warnings": ["HEAD is not on b"]}

    cli.main(["item", "takeover", "w1"])

    out = capsys.readouterr().out
    assert "warning: HEAD is not on b" in out
    for text in expect:
        assert text in out
    # Only the verbs that fit the status: `retry` never restores the conversation.
    assert ("kraft item retry" in out) == (status == "needs_human")


def test_takeover_release_flag_only_frees_the_lock(takeover, capsys):
    calls, _, _ = takeover

    cli.main(["item", "takeover", "w1", "--release"])

    assert calls == ["end"]
    assert "released w1" in capsys.readouterr().out
