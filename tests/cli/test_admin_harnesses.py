"""`kraft admin harnesses [ID]`: harness profiles from a terminal
(Kraft-archr) -- a table, one profile's detail, `--json`."""

from __future__ import annotations

import json

import pytest

from kraft import cli


def test_the_table_lists_each_profile_its_provider_and_the_tasks_using_it(app, capsys):
    cli.main(["admin", "harnesses"])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["ID", "PROVIDER", "ENABLED", "USED", "BY"]
    rows = {line.split()[0]: line.split() for line in lines[1:]}
    assert rows["codex"][1:] == ["fake", "yes", "-"]
    assert rows["claude"][1:3] == ["fake", "yes"]
    assert "tasks.implementer," in rows["claude"]


def test_one_profile_prints_its_settings_and_users(app, capsys):
    cli.main(["admin", "harnesses", "claude"])
    out = capsys.readouterr().out
    assert "provider    fake" in out
    assert "model=sonnet" in out
    assert "chains      default, quick-task" in out
    assert "tasks.implementer" in out


def test_json_prints_the_api_payload(app, capsys):
    cli.main(["admin", "harnesses", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert {"codex", "claude"} <= {p["id"] for p in payload["profiles"]}


def test_an_unknown_profile_is_a_kraft_message_and_exit_1(app, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["admin", "harnesses", "nope"])
    assert caught.value.code == 1
    assert "404" in capsys.readouterr().err
