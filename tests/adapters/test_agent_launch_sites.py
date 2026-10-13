"""Every work item's launch hands `resolve_agent_task` the item's repository
id: one that passes None, or nothing, reads no repo library, so an item would
silently lose its own repository's skills and profiles at that site alone."""

import ast
from pathlib import Path

import pytest

import kraft

#: Sites that launch for no work item: doctor previews each chain on its own
#: pins, with no repository to scope to.
_NO_ITEM = {"doctor.py"}

#: Every site that launches for a work item, or previews one in a repository.
_SITES = [
    "executor/dispatch.py",
    "gate_review.py",
    "escalate.py",
    "review_reply.py",
    "api/routes/sessions.py",
    "api/routes/settings.py",
]


def _launches():
    source = Path(kraft.__file__).parent
    for file in sorted(source.rglob("*.py")):
        for node in ast.walk(ast.parse(file.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "resolve_agent_task"
            ):
                yield file.relative_to(source).as_posix(), node


@pytest.mark.parametrize("site", _SITES)
def test_an_items_launch_passes_its_repository_id(site):
    calls = [node for file, node in _launches() if file == site]
    assert calls, f"{site} no longer launches; drop its row"
    for call in calls:
        (value,) = [k.value for k in call.keywords if k.arg == "repo_id"] or [None]
        assert value is not None, f"{site}:{call.lineno} passes no repo_id"
        assert not (isinstance(value, ast.Constant) and value.value is None), (
            f"{site}:{call.lineno} passes repo_id=None"
        )


def test_every_launch_site_is_listed():
    """A new launch site is a new row above, or one with no item."""
    assert {file for file, _ in _launches()} - _NO_ITEM == set(_SITES)
