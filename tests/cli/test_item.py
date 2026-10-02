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
    """The item's own cap or its policy's item-wide one: a node's, a token or
    the daily cap's stop is refused."""
    text = _help(capsys, "item")
    assert (
        "raise-budget raise the dollar cap that stopped an item, its own (create --budget) "
        "or its policy's item-wide budget_usd, and retry it"
    ) in text


def test_set_overrides_help_says_each_call_replaces_the_override(capsys):
    """`set-node-override` merges and this one does not, and a person who ran
    one then the other lost the model without being told."""
    text = _help(capsys, "item", "set-overrides")
    assert "Each call replaces the item-wide override" in text
    assert "a flag you leave out goes back to the template's own binding" in text


def test_set_node_override_help_says_it_merges(capsys):
    text = _help(capsys, "item", "set-node-override")
    assert "Each call changes only the flags you give" in text and "--clear resets the node" in text


def test_create_gives_its_title_a_help_line(capsys):
    assert "title the item's title: one line, as the board shows it" in _help(
        capsys, "item", "create"
    )


@pytest.mark.parametrize(
    ("status", "advice"),
    [
        ("active", "To keep them, cancel the item instead."),
        (
            "abandoned",
            "This item is already cancelled; abandoning it only reclaims its worktree and branch.",
        ),
    ],
    ids=["open", "cancelled"],
)
def test_abandon_refusal_fits_the_items_state(monkeypatch, capsys, status, advice):
    """It used to tell a cancelled item's owner to cancel it."""

    async def get_work_item(item_id=None, **_):
        return {"id": item_id, "status": status}

    monkeypatch.setattr(cli.item.client, "get_work_item", get_work_item)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["item", "abandon", "w1"])
    assert stopped.value.code == 1
    err = capsys.readouterr().err
    assert advice in err and "pass --yes" in err
    assert ("cancel the item instead" in err) is (status != "abandoned")


def test_abandon_refusal_still_works_when_the_item_cannot_be_read(monkeypatch, capsys):
    async def get_work_item(item_id=None, **_):
        raise ConnectionError("no server")

    monkeypatch.setattr(cli.item.client, "get_work_item", get_work_item)
    with pytest.raises(SystemExit):
        cli.main(["item", "abandon", "w1"])
    assert "To keep them, cancel the item instead." in capsys.readouterr().err
