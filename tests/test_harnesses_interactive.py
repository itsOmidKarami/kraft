"""`interactive:` in a harness file: how a person runs the CLI themselves
(`kraft item takeover`), and the argv built from it."""

import pytest

from kraft import harness


@pytest.mark.parametrize(
    ("resume", "expected"),
    [
        pytest.param(
            "X", ["claude", "--resume", "X", "--fork-session", "--session-id", "U"], id="forks"
        ),
        pytest.param(None, ["claude", "--session-id", "U"], id="fresh"),
    ],
)
def test_interactive_argv_for_claude(resume, expected):
    h = harness.load(None).valid["claude"]
    argv = harness.build_interactive_argv(h, session_id="U", resume=resume)
    assert argv == expected
    assert not {"-p", "--permission-prompt-tool", "--append-system-prompt"} & set(argv)


def test_interactive_argv_refuses_a_harness_without_the_block():
    h = harness.load(None).valid["gemini"]
    with pytest.raises(harness.HarnessError, match="takeover isn't supported for gemini yet"):
        harness.build_interactive_argv(h, session_id="U", resume=None)


_MINI = {
    "id": "x",
    "kind": "cli",
    "command": ["x"],
    "capabilities": {
        "context": {"channel": "prompt"},
        "usage": {"source": "result_file"},
        "prompt": {"cli": ["-p", "{value}"]},
    },
}


@pytest.mark.parametrize(
    ("interactive", "expect"),
    [
        pytest.param(
            {"command": ["x"], "resume": {"via": "command_resume"}},
            "command_resume",
            id="via-needs-the-prefix",
        ),
        pytest.param({"command": []}, "interactive 'command'", id="needs-a-command"),
        pytest.param(
            {"command": ["x"], "session_id": {"cli": ["--id", "{csv}"]}},
            "placeholder {csv}",
            id="only-value-exists",
        ),
        pytest.param(
            {"command": ["x"], "resume": {}}, "needs a 'cli' binding", id="binding-is-required"
        ),
    ],
)
def test_a_malformed_interactive_block_is_refused(interactive, expect):
    with pytest.raises(harness.HarnessError, match=expect):
        harness.Harness.from_mapping({**_MINI, "interactive": interactive}, where="t")


def test_interactive_resume_via_command_resume_replaces_the_prefix():
    h = harness.Harness.from_mapping(
        {
            **_MINI,
            "interactive": {
                "command": ["x"],
                "command_resume": ["x", "resume", "{value}"],
                "resume": {"via": "command_resume"},
            },
        },
        where="t",
    )
    assert harness.build_interactive_argv(h, session_id="U", resume="X") == ["x", "resume", "X"]
    assert harness.build_interactive_argv(h, session_id="U", resume=None) == ["x"]
