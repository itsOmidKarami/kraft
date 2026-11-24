"""`interactive:` in a harness file: how a person runs the CLI themselves
(`kraft item takeover`), and the argv built from it."""

import glob
import json
import os
import subprocess
import uuid
from pathlib import Path

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


@pytest.mark.e2e("claude")
def test_claude_forks_into_the_minted_session_id(tmp_path):
    """The contract `kraft item takeover` relies on, against the real CLI (claude
    2.1.328): `--resume X --fork-session --session-id U` makes a session `U`, written as
    the harness's `session_file` says, and leaves X's transcript as it was."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")):
        pytest.skip("export ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN (claude setup-token)")
    h = harness.load(None).valid["claude"]
    home = tmp_path / "home"
    work = tmp_path / "work"
    work.mkdir()
    env = {**os.environ, "HOME": str(home)}

    def claude(*argv: str) -> dict:
        done = subprocess.run(
            ["claude", *argv, "--output-format", "json", "--model", "haiku"],
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)

    def written(session_id: str) -> list[Path]:
        pattern = h.interactive.session_file.replace("{value}", session_id)
        return [Path(p) for p in glob.glob(str(home / pattern.removeprefix("~/")))]

    first = claude("-p", "say hi")["session_id"]
    [original] = written(first)
    before = (original.stat().st_mtime_ns, original.read_bytes())
    minted = str(uuid.uuid4())
    argv = harness.build_interactive_argv(h, session_id=minted, resume=first)

    forked = claude(*argv[1:], "-p", "say bye")

    assert forked["session_id"] == minted
    assert len(written(minted)) == 1
    assert (original.stat().st_mtime_ns, original.read_bytes()) == before
