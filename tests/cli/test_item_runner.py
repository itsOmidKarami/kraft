"""`kraft item --runner`: where an item's steps run, from the terminal."""

from __future__ import annotations

import asyncio
import json
import os
import pathlib

import pytest

from kraft import cli, client


@pytest.mark.parametrize("verb", ["create", "set-overrides", "set-node-override"])
def test_runner_is_a_flag_with_a_help_line_on_each_verb(capsys, verb):
    with pytest.raises(SystemExit):
        cli.main(["item", verb, "--help"])
    assert "--runner NAME where its steps run" in " ".join(capsys.readouterr().out.split())


def test_a_runner_is_set_from_the_terminal_at_filing_and_afterwards(app, monkeypatch, capsys, repo):
    """`--runner` at filing; `set-overrides --runner` and `set-node-override --runner` after (the
    last names a group). The machine is in workers.yaml first, because the lifespan the `app`
    fixture opens per call reads the file."""
    from kraft.remote import registry

    (pathlib.Path(os.environ["KRAFT_CONFIG_DIR"]) / "workers.yaml").write_text(
        f"workers:\n  gpu:\n    token_sha256: {registry.hash_token('kwt_x')}\n    groups: [farm]\n"
    )
    asyncio.run(client.ensure_repo(str(repo)))
    monkeypatch.chdir(repo)
    cli.main(
        ["item", "create", "t", "--runner", "gpu", "--node-override", "plan.runner=any", "--json"]
    )
    wid = json.loads(capsys.readouterr().out)["id"]
    cli.main(["item", "set-overrides", wid, "--runner", "any", "--json"])
    cli.main(
        ["item", "set-node-override", wid, "--node", "implementation", "--runner", "farm", "--json"]
    )
    capsys.readouterr()
    cli.main(["view", "show", wid, "--json"])
    item = json.loads(capsys.readouterr().out)
    assert item["agent_overrides"] == {"runner": "any"}
    assert item["node_overrides"] == {
        "plan": {"runner": "any"},
        "implementation": {"runner": "farm"},
    }


@pytest.mark.parametrize(
    "args, expected",
    [
        (["--model", "opus"], {"model": "opus", "runner": "gpu"}),
        (["--runner", "any"], {"runner": "any"}),
        (["--clear"], {}),
    ],
    ids=["a-model-edit-keeps-the-runner", "naming-a-runner-replaces-it", "clear-drops-it-too"],
)
def test_set_overrides_keeps_the_runner_it_does_not_name(
    app, monkeypatch, capsys, repo, args, expected
):
    from kraft.remote import registry

    (pathlib.Path(os.environ["KRAFT_CONFIG_DIR"]) / "workers.yaml").write_text(
        f"workers:\n  gpu:\n    token_sha256: {registry.hash_token('kwt_x')}\n"
    )
    asyncio.run(client.ensure_repo(str(repo)))
    monkeypatch.chdir(repo)
    cli.main(["item", "create", "t", "--runner", "gpu", "--json"])
    wid = json.loads(capsys.readouterr().out)["id"]
    cli.main(["item", "set-overrides", wid, *args, "--json"])
    capsys.readouterr()
    cli.main(["view", "show", wid, "--json"])
    assert (json.loads(capsys.readouterr().out)["agent_overrides"] or {}) == expected
