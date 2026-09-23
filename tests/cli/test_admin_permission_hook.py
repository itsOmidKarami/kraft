"""The subcommand a CLI's hook runs: stdin in, the CLI's answer out
(Kraft-4in7z). Run as a real process, the way Cursor runs it."""

import argparse
import io
import json
import subprocess
import sys

import pytest

from kraft import permission_hooks
from kraft.cli import admin

CURSOR_SHELL = json.dumps({"tool_name": "Shell", "tool_input": {"command": "ls"}})


def _hook(tmp_path, *args, stdin=CURSOR_SHELL, env=None):
    # No KRAFT_SESSION_ID: not a worker, so the client answers `unavailable`.
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "KRAFT_HOME": str(tmp_path / "k"),
        **(env or {}),
    }
    return subprocess.run(
        [sys.executable, "-m", "kraft", "admin", "permission-hook", *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


@pytest.mark.parametrize(("flags", "expected"), [((), None), (("--fail-closed",), "deny")])
@pytest.mark.parametrize("stdin", [CURSOR_SHELL, "", "not json"])
def test_permission_hook_without_kraft_is_no_opinion_or_deny_when_fail_closed(
    tmp_path, flags, expected, stdin
):
    done = _hook(tmp_path, "cursor", *flags, stdin=stdin)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout).get("permission") == expected


def test_the_session_env_makes_the_hook_fail_closed(tmp_path):
    done = _hook(tmp_path, "cursor", env={"KRAFT_PERMISSION_FAIL_CLOSED": "1"})
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout).get("permission") == "deny"


def test_an_unknown_harness_is_a_usage_error(tmp_path):
    done = _hook(tmp_path, "nosuch")
    assert (done.returncode, done.stdout) == (2, "")
    assert "invalid choice" in done.stderr


def test_the_hook_never_imports_the_server(tmp_path):
    # It runs before every tool call a worker makes: start-up is on the
    # agent's critical path, so the FastAPI app stays out of it.
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "KRAFT_HOME": str(tmp_path / "k")}
    done = subprocess.run(
        [sys.executable, "-X", "importtime", "-m", "kraft", "admin", "permission-hook", "cursor"],
        input=CURSOR_SHELL,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    imported = {line.rsplit("|", 1)[-1].strip() for line in done.stderr.splitlines()}
    assert "kraft.permission_hooks" in imported
    assert not {m for m in imported if m.split(".")[0] in ("fastapi", "starlette")}
    assert not {m for m in imported if m.startswith("kraft.api")}


def test_the_hook_passes_its_harness_tool_names(monkeypatch):
    """Cursor's `Shell` reaches the gate as `Bash` only if the subcommand
    hands answer_hook the harness's own `tool_names`."""
    seen = []
    monkeypatch.setattr(
        permission_hooks, "answer_hook", lambda h, s, names, **kw: seen.append(names) or ("{}", 0)
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(CURSOR_SHELL))
    with pytest.raises(SystemExit):
        admin._cmd_permission_hook(argparse.Namespace(harness="cursor", fail_closed=False))
    assert seen[0]["Shell"] == ("Bash",)
