"""The two stops `run_setup_command` ends a work item with, and what each
tells the person to do about it."""

from __future__ import annotations

import pytest
from support.harness import entry_of

from kraft import builtins as kraft_builtins


@pytest.mark.parametrize("entry", [None, entry_of({})], ids=["no-entry", "undeclared"])
async def test_an_undeclared_setup_command_names_both_ways_out(tmp_path, entry):
    with pytest.raises(RuntimeError) as stopped:
        await kraft_builtins.run_setup_command(tmp_path, tmp_path, entry)
    message = str(stopped.value)
    assert "no setup_command declared for" in message
    assert 'use "" for a repo that deliberately needs no preparation' in message
    assert "tick No setup needed under Templates › Repos" in message


async def test_a_failed_setup_command_names_the_command_and_its_output(tmp_path):
    with pytest.raises(RuntimeError) as stopped:
        await kraft_builtins.run_setup_command(
            tmp_path, tmp_path, entry_of({"setup_command": "echo broken >&2; exit 3"})
        )
    assert str(stopped.value) == (
        f"setup command failed for {tmp_path.name}: 'echo broken >&2; exit 3': broken"
    )


async def test_a_declared_empty_setup_command_needs_nothing(tmp_path):
    assert (
        await kraft_builtins.run_setup_command(tmp_path, tmp_path, entry_of({"setup_command": ""}))
        == ""
    )
