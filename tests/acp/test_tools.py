import pytest

from kraft.acp import tools

MAP = {
    "other": ["X"],
    "read": ["Read"],
    "shell": ["Bash"],
    "execute": ["Bash"],
    "edit": ["Write", "Edit"],
}


@pytest.mark.parametrize(
    ("call", "key", "names"),
    [
        ({"name": "read", "kind": "read"}, "read", ("Read",)),
        ({"name": "shell", "kind": "execute"}, "shell", ("Bash",)),
        ({"name": "Read", "kind": "read"}, "Read", ("Read",)),
        ({"name": "todowrite", "kind": "other"}, "todowrite", ("todowrite",)),
        ({"kind": "execute"}, "execute", ("Bash",)),
        ({"name": "", "kind": "edit"}, "edit", ("Write", "Edit")),
        ({"kind": "other"}, "other", ()),
        ({"kind": "fetch"}, "fetch", ()),
        ({}, "", ()),
    ],
    ids=[
        "named",
        "named-beats-kind",
        "kraft-name-passes-through",
        "unmapped-name-is-itself",
        "kind-only",
        "empty-name-falls-to-kind",
        "other-has-no-names",
        "unmapped-kind",
        "empty-call",
    ],
)
def test_a_tool_call_is_named_by_its_name_else_its_kind(call, key, names):
    assert tools.resolve(call, MAP) == tools.Tool(key, names)
