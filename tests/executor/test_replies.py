import json

import pytest
from support.harness import write

from kraft.executor import replies

_COLLECTED = {"T1", "T2"}


@pytest.mark.parametrize(
    "content, read",
    [
        (
            {"replies": [{"thread": "T1", "kind": "declined", "body": "Not a bug."}]},
            [{"thread": "T1", "kind": "declined", "body": "Not a bug."}],
        ),
        (
            {"replies": [{"thread": "T2", "kind": "answered", "body": "It is."}]},
            [{"thread": "T2", "kind": "answered", "body": "It is."}],
        ),
        ({"replies": [{"thread": "T1", "kind": "fixed", "body": "Done."}]}, []),
        ({"replies": [{"thread": "T9", "kind": "declined", "body": "No."}]}, []),
        ({"replies": [{"thread": "T1", "kind": "declined", "body": "  "}]}, []),
        (
            {
                "replies": [
                    {"thread": "T1", "kind": "declined", "body": "First."},
                    {"thread": "T1", "kind": "answered", "body": "Second."},
                ]
            },
            [{"thread": "T1", "kind": "declined", "body": "First."}],
        ),
        ({"replies": "x"}, []),
        ("{", []),
    ],
    ids=[
        "declined",
        "answered",
        "another-kind",
        "not-collected",
        "blank-body",
        "second-for-a-thread",
        "not-a-list",
        "not-json",
    ],
)
def test_the_triage_s_replies_are_read_only_for_collected_threads(tmp_path, content, read):
    text = content if isinstance(content, str) else json.dumps(content)
    path = write(tmp_path, "result.json", text)
    assert replies.triage_replies(path, _COLLECTED) == read
