"""`targeted_command` on a test scope, and the whole-table rule that keeps a
session's command naming exactly one scope."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from support.harness import entry_of

_S = {"paths": ["**"], "command": "just ci-test", "targeted_command": "just test-targeted"}


def _area(**scope) -> dict:
    """One area whose single scope runs `web-test`, plus what `scope` adds."""
    return {
        "web": {
            "paths": ["web/**"],
            "setup": "just web-setup",
            "verification": {
                "test_scopes": [{"paths": ["web/**"], "command": "web-test", **scope}]
            },
        }
    }


@pytest.mark.parametrize(
    ("fields", "match"),
    [
        pytest.param(
            {"test_scopes": [{**_S, "targeted_command": ""}]}, "at least 1 character", id="empty"
        ),
        pytest.param(
            {"test_scopes": [{**_S, "targeted_command": "   "}]},
            "targeted_command is blank",
            id="blank",
        ),
        pytest.param(
            {"test_scopes": [{**_S, "targeted_command": "just 'test"}]},
            "cannot be read as a command",
            id="unparsable",
        ),
        pytest.param(
            {"test_scopes": [{**_S, "targeted_command": "just  ci-test"}]},
            "same as its command",
            id="own-command-normalized",
        ),
        pytest.param(
            {"test_scopes": [_S, {"paths": ["b/**"], "command": "just ci-test"}]},
            "must share their targeted_command",
            id="shared-command-differs",
        ),
        pytest.param(
            {"test_scopes": [_S, {"paths": ["b/**"], "command": "just test-targeted"}]},
            "another scope's command",
            id="equals-another-command",
        ),
        pytest.param(
            {
                "test_scopes": [
                    _S,
                    {
                        "paths": ["b/**"],
                        "command": "other",
                        "targeted_command": "just  test-targeted",
                    },
                ]
            },
            "another scope's targeted_command",
            id="equals-another-targeted-normalized",
        ),
        pytest.param(
            {
                "test_scopes": [{**_S, "targeted_command": "just web-setup"}],
                "areas": _area(),
            },
            "an area's setup",
            id="equals-an-area-setup",
        ),
        pytest.param(
            {"test_scopes": [_S], "areas": _area(targeted_command="just ci-test")},
            "another scope's command",
            id="area-scope-vs-repo-scope",
        ),
        pytest.param(
            {
                "test_command": "just test-targeted",
                "areas": _area(targeted_command="just test-targeted"),
            },
            "another scope's command",
            id="equals-the-bare-test-commands",
        ),
    ],
)
def test_an_entry_whose_commands_could_name_two_scopes_is_refused(fields, match):
    with pytest.raises(ValidationError, match=match):
        entry_of(fields)


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"test_scopes": [_S, {**_S, "paths": ["b/**"]}]}, id="one-identity"),
        pytest.param(
            {"test_scopes": [_S], "areas": _area(targeted_command="web-targeted")},
            id="area-keeps-its-own",
        ),
    ],
)
def test_a_table_of_distinct_commands_loads(fields):
    entry = entry_of(fields)

    assert entry.model_dump_repo()["test_scopes"][0]["targeted_command"] == "just test-targeted"
    if "areas" in fields:
        area_scope = entry.areas["web"]["verification"]["test_scopes"][0]
        assert area_scope["targeted_command"] == "web-targeted"
