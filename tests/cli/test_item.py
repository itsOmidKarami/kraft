"""`kraft item`'s own help: what `kraft item --help` and `kraft item create
--help` say, where a reviewer could not tell what a verb or argument was."""

from __future__ import annotations

import pytest

from kraft import cli


def _help(capsys, *argv: str) -> str:
    with pytest.raises(SystemExit):
        cli.main([*argv, "--help"])
    return " ".join(capsys.readouterr().out.split())


def test_raise_budget_help_says_which_cap_it_raises(capsys):
    """Only the item's own cap: a policy or daily cap's stop is refused."""
    text = _help(capsys, "item")
    assert (
        "raise-budget raise an item's own dollar cap (create --budget) once that cap "
        "stopped it, and retry it"
    ) in text


def test_create_gives_its_title_a_help_line(capsys):
    assert "title the item's title: one line, as the board shows it" in _help(
        capsys, "item", "create"
    )
