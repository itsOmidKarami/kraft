"""`kraft item`'s reviewer flags: the automated reviewers a node's review wait
reads, named per item at intake (`--node-override`) or later
(`set-node-override --reviewer`). The flag lives in its own file because
test_item.py and test_verbs.py are at their line budget."""

from __future__ import annotations

import json

import pytest
from support.harness import connect_repo

from kraft import cli

NODE = "merge_request_feedback"


def _node_overrides(capsys, wid: str) -> dict:
    capsys.readouterr()
    cli.main(["view", "show", wid, "--json"])
    return json.loads(capsys.readouterr().out)["node_overrides"]


@pytest.mark.parametrize(
    "flags, stored",
    [
        (
            ["--reviewer", "bot:coderabbitai", "--reviewer", "check:scan"],
            [{"bot": "coderabbitai"}, {"check": "scan"}],
        ),
        (["--reviewer", "none"], []),
    ],
    ids=["a-bot-and-a-check", "none-sends-empty"],
)
def test_a_node_gets_its_reviewers_from_the_terminal(app, monkeypatch, capsys, repo, flags, stored):
    connect_repo(repo)
    monkeypatch.chdir(repo)
    cli.main(["item", "create", "t", "--json"])
    wid = json.loads(capsys.readouterr().out)["id"]

    cli.main(["item", "set-node-override", wid, "--node", NODE, *flags, "--json"])

    assert _node_overrides(capsys, wid) == {NODE: {"reviewers": stored}}


def test_reviewers_can_be_named_at_intake(app, monkeypatch, capsys, repo):
    connect_repo(repo)
    monkeypatch.chdir(repo)

    cli.main(["item", "create", "t", "--node-override", f"{NODE}.reviewers=[{{bot: x}}]", "--json"])
    wid = json.loads(capsys.readouterr().out)["id"]

    assert _node_overrides(capsys, wid) == {NODE: {"reviewers": [{"bot": "x"}]}}


def test_a_malformed_reviewer_flag_is_an_argument_error(app, monkeypatch, capsys, repo):
    connect_repo(repo)
    monkeypatch.chdir(repo)
    cli.main(["item", "create", "t", "--json"])
    wid = json.loads(capsys.readouterr().out)["id"]

    with pytest.raises(SystemExit) as exit_:
        cli.main(["item", "set-node-override", wid, "--node", NODE, "--reviewer", "bogus"])

    assert exit_.value.code == 2
    assert "bot:LOGIN, check:NAME or none" in capsys.readouterr().err
