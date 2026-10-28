"""The names argparse prints for a typo in a command or verb.

Each subparser's `dest` is an internal field (`group`, `addon_verb`, ...), and
argparse uses it in `argument <dest>: invalid choice` unless a metavar says
otherwise. A person typing `kraft vew list` should be told about the command,
not about a field they never named.
"""

from __future__ import annotations

import re

import pytest

from kraft import cli


@pytest.mark.parametrize(
    ("argv", "typed", "word"),
    [
        pytest.param(["vew", "list"], "vew", "command", id="command"),
        pytest.param(["admin", "addon", "lits"], "lits", "verb", id="addon"),
        pytest.param(["admin", "addon", "collection", "lits"], "lits", "verb", id="collection"),
        pytest.param(["admin", "templates", "lints"], "lints", "verb", id="templates"),
        pytest.param(["admin", "worker", "ad"], "ad", "verb", id="worker"),
    ],
)
def test_a_mistyped_name_says_which_word_it_is(argv, typed, word, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == 2
    assert f"argument {word}: invalid choice: '{typed}'" in capsys.readouterr().err


def test_a_group_usage_line_says_verb(capsys):
    """`view`'s dest was already `verb`, so the metavar only shows in the usage
    line it prints: `kraft view [-h] verb ...`, not `{list,show,...} ...`."""
    with pytest.raises(SystemExit) as caught:
        cli.main(["view", "--help"])
    assert caught.value.code == 0
    assert "kraft view [-h] verb ..." in capsys.readouterr().out


def test_help_still_lists_every_group(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["--help"])
    assert caught.value.code == 0
    out = capsys.readouterr().out
    assert "command ..." in out
    for group in (*(name for name, _, _ in cli._GROUPS), "console"):
        assert re.search(rf"^\s+{group}\s", out, re.M), group
