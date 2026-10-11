"""`kraft repo library`: review a repo's `.kraft/` library, take it, and the one
question asked after a first load."""

from __future__ import annotations

import asyncio

import pytest
from support.addons import (
    AGENT,
    a_terminal,
    library_outcome,
    repo_library_files,
    repo_with_library,
)
from support.harness import make_repo

from kraft import cli, client

# `app` fixture: tests/conftest.py.


def _main(argv) -> int:
    try:
        cli.main(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    return 0


@pytest.mark.parametrize(
    "argv, typed, outcome, code",
    [
        pytest.param(["--check"], None, "waiting", 1, id="check-says-waiting"),
        pytest.param([], None, "waiting", 0, id="no-terminal-takes-nothing"),
        pytest.param([], ["n"], "waiting", 0, id="declined"),
        pytest.param([], ["y", "n"], "current", 0, id="accepted-at-the-prompt"),
        pytest.param(["-y"], None, "current", 0, id="yes-flag"),
    ],
)
def test_repo_library_reviews_and_takes(
    app, tmp_path, capsys, monkeypatch, argv, typed, outcome, code
):
    repo = repo_with_library(tmp_path)
    cli.main(["repo", "connect", str(repo), "--no-tests", "-y"])
    if typed is not None:
        a_terminal(monkeypatch, typed)

    assert _main(["repo", "library", str(repo), *argv]) == code

    assert "Content:" in capsys.readouterr().out
    assert library_outcome(repo) == outcome


def _auto_update(repo) -> bool:
    return asyncio.run(client.repo_library(str(repo)))["auto_update"]


@pytest.mark.parametrize(
    "argv, typed, key, says",
    [
        pytest.param(
            [], ["y", "y"], True, "later text changes will be taken automatically", id="yes"
        ),
        pytest.param([], ["y", "n"], False, None, id="no"),
        pytest.param([], ["y", ""], False, None, id="enter-is-no"),
        pytest.param(["-y"], [], False, None, id="the-yes-flag-is-not-asked"),
    ],
)
def test_a_first_accept_asks_once_about_later_changes(
    app, tmp_path, capsys, monkeypatch, argv, typed, key, says
):
    repo = repo_with_library(tmp_path)
    cli.main(["repo", "connect", str(repo), "--no-tests", "-y"])
    a_terminal(monkeypatch, typed)

    assert _main(["repo", "library", str(repo), *argv]) == 0

    out = capsys.readouterr().out
    assert _auto_update(repo) is key
    assert ("Whoever can merge to" in out) is bool(typed)
    if says:
        assert says in out


def test_a_later_accept_is_not_asked_again(app, tmp_path, monkeypatch):
    repo = repo_with_library(tmp_path)
    cli.main(["repo", "connect", str(repo), "--no-tests", "-y"])
    cli.main(["repo", "library", str(repo), "-y"])
    repo_library_files(repo, library={"tasks": {"base": {**AGENT, "prompt": "v2"}}})
    a_terminal(monkeypatch, ["y"])

    assert _main(["repo", "library", str(repo)]) == 0

    assert _auto_update(repo) is False


def test_a_key_that_cannot_be_saved_does_not_fail_the_take(app, tmp_path, capsys, monkeypatch):
    repo = repo_with_library(tmp_path)
    cli.main(["repo", "connect", str(repo), "--no-tests", "-y"])
    a_terminal(monkeypatch, ["y", "y"])

    async def refused(_path, _fields):
        raise ValueError("kraft 422: repos.yaml does not load")

    monkeypatch.setattr(client, "update_repo", refused)

    assert _main(["repo", "library", str(repo)]) == 0

    out = capsys.readouterr().out
    assert "not saved: kraft 422: repos.yaml does not load" in out
    assert library_outcome(repo) == "current"


def test_repo_library_says_when_there_is_none(app, tmp_path, capsys):
    repo = make_repo(tmp_path, "plain")
    cli.main(["repo", "connect", str(repo), "--no-tests", "-y"])
    capsys.readouterr()

    assert _main(["repo", "library", str(repo)]) == 0

    assert "no repo library on origin's default branch" in capsys.readouterr().out


def test_a_worker_may_not_take_a_repo_library(app, tmp_path, monkeypatch):
    repo = repo_with_library(tmp_path)
    cli.main(["repo", "connect", str(repo), "--no-tests", "-y"])
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "w1")

    with pytest.raises(SystemExit, match="a worker does not change the instance's repo libraries"):
        cli.main(["repo", "library", str(repo), "-y"])

    monkeypatch.delenv("KRAFT_WORK_ITEM_ID")
    assert library_outcome(repo) == "waiting"
