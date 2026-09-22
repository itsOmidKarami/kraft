"""`kraft admin templates library [ID]`: the template library's components
from a terminal (Kraft-6xkkm) -- a table, one component's detail, `--json`."""

from __future__ import annotations

import json

import pytest
import yaml

from kraft import cli


def test_the_table_lists_each_component_its_kind_and_the_chains_using_it(app, capsys):
    cli.main(["admin", "templates", "library"])
    rows = {line.split()[0]: line.split() for line in capsys.readouterr().out.splitlines()[1:]}
    assert rows["tasks.implementer"][1:] == ["tasks", "default,", "quick-task"]
    assert rows["nodes.verification"][1:] == ["nodes", "default"]


def test_one_component_prints_its_definition_and_users(app, capsys):
    cli.main(["admin", "templates", "library", "implementer"])
    out = capsys.readouterr().out
    assert "tasks.implementer" in out
    assert "used by  default, quick-task" in out
    # The definition as written, readable as YAML.
    definition = yaml.safe_load(out.split("definition:\n", 1)[1])
    assert definition["prompt"] == "Implement the approved plan."


def test_json_prints_the_api_payload(app, capsys):
    cli.main(["admin", "templates", "library", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert "tasks.implementer" in {c["id"] for c in payload["components"]}


def test_an_unknown_component_is_a_kraft_message_and_exit_1(app, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["admin", "templates", "library", "tasks.nope"])
    assert caught.value.code == 1
    assert "404" in capsys.readouterr().err
