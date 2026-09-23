"""A CLI's hook payload in, Kraft's answer in that CLI's shape out
(Kraft-4in7z). answer_hook never raises: a failure is no opinion, or deny
when the launch installed the hook --fail-closed."""

import json

import pytest

from kraft import permission_hooks as ph
from kraft.client import reads

#: The shape of agent 2026.09.18-9a7762b's preToolUse payload (Task 1 probe, 2026-09-23).
CURSOR_SHELL = json.dumps(
    {
        "tool_name": "Shell",
        "tool_input": {"command": "echo hello", "cwd": "", "timeout": 30000},
        "tool_use_id": "46765f71",
        "session_id": "f69e079f",
        "conversation_id": "f69e079f",
        "hook_event_name": "preToolUse",
        "cursor_version": "2026.09.18-9a7762b",
        "workspace_roots": ["/tmp/wt"],
        "user_email": "someone@example.com",
        "transcript_path": "/tmp/transcript.jsonl",
        "model": "default",
    }
)
NAMES = {"Shell": ("Bash",), "Write": ("Write", "Edit")}


def _asker(behavior, seen=None):
    async def ask(tool, input, tool_use_id=None, **kw):
        if seen is not None:
            seen.append((tool, input, tool_use_id, kw))
        return {"behavior": behavior, "message": "because"}

    return ask


def _run(behavior, *, fail_closed=False, seen=None, stdin=CURSOR_SHELL):
    return ph.answer_hook(
        "cursor", stdin, NAMES, fail_closed=fail_closed, ask=_asker(behavior, seen)
    )


@pytest.mark.parametrize("fail_closed", [False, True])
def test_cursor_maps_shell_to_bash_and_asks_in_enforce_mode(fail_closed):
    seen = []
    _run("no_opinion", seen=seen, fail_closed=fail_closed)
    [(tool, input, tool_use_id, kw)] = seen
    assert (tool, input["command"], tool_use_id, kw) == (
        "Bash",
        "echo hello",
        "46765f71",
        {
            "also": (),
            "mode": "enforce",
            "harness": "cursor",
            "cli_tool": "Shell",
            "fail_closed": fail_closed,
        },
    )


def test_a_cli_tool_mapped_to_two_names_asks_as_both():
    seen = []
    payload = {**json.loads(CURSOR_SHELL), "tool_name": "Write", "tool_input": {"path": "x"}}
    _run("no_opinion", seen=seen, stdin=json.dumps(payload))
    [(tool, _input, _id, kw)] = seen
    assert (tool, kw["also"], kw["cli_tool"]) == ("Write", ("Edit",), "Write")


@pytest.mark.parametrize("use_id", [None, 7, ["x"]])
def test_cursor_sends_no_tool_use_id_unless_the_payload_has_a_string_one(use_id):
    # The gate's body types it `str | None`: anything else would be a 422.
    payload = {**json.loads(CURSOR_SHELL), "tool_use_id": use_id}
    seen = []
    _run("no_opinion", seen=seen, stdin=json.dumps(payload))
    [(_tool, _input, tool_use_id, _kw)] = seen
    assert tool_use_id is None


@pytest.mark.parametrize(
    ("behavior", "fail_closed", "expected"),
    [
        ("allow", False, {"permission": "allow"}),
        ("allow", True, {"permission": "allow"}),
        ("no_opinion", False, {}),
        ("no_opinion", True, {}),
        ("unavailable", False, {}),
        ("unresolved", False, {}),
        # A fail-closed hook's gate already answers deny for unresolvable
        # policy; an `unresolved` that still arrives is not re-decided here.
        ("unresolved", True, {}),
        ("something-new", False, {}),
    ],
)
def test_cursor_renders_each_answer(behavior, fail_closed, expected):
    out, code = _run(behavior, fail_closed=fail_closed)
    assert (json.loads(out), code) == (expected, 0)


@pytest.mark.parametrize(
    ("behavior", "fail_closed"), [("deny", False), ("unavailable", True), ("something-new", True)]
)
def test_cursor_deny_carries_the_reason(behavior, fail_closed):
    out, code = _run(behavior, fail_closed=fail_closed)
    body = json.loads(out)
    assert (body["permission"], code) == ("deny", 0)
    assert body["agent_message"].startswith("Kraft: ")


def test_a_payload_it_cannot_read_is_no_opinion_or_deny_when_fail_closed():
    assert json.loads(_run("allow", stdin="not json")[0]) == {}
    assert json.loads(_run("allow", stdin="not json", fail_closed=True)[0])["permission"] == "deny"
    assert (
        json.loads(_run("allow", stdin='{"tool_input": {}}', fail_closed=True)[0])["permission"]
        == "deny"
    )


def test_a_raising_client_never_escapes():
    async def boom(*_a, **_k):
        raise RuntimeError("x")

    out, code = ph.answer_hook("cursor", CURSOR_SHELL, NAMES, fail_closed=True, ask=boom)
    assert (json.loads(out)["permission"], code) == ("deny", 0)
    out, code = ph.answer_hook("cursor", CURSOR_SHELL, NAMES, fail_closed=False, ask=boom)
    assert (json.loads(out), code) == ({}, 0)


@pytest.mark.parametrize(("fail_closed", "expected"), [(False, None), (True, "deny")])
def test_server_down_mid_run_through_the_real_client(monkeypatch, fail_closed, expected):
    """Review focus 1: Kraft's server is down. With no allowlist the hook has
    no opinion and Cursor's classifier keeps the worker going; with one
    (--fail-closed) every call is denied, never allowed."""
    monkeypatch.setenv("KRAFT_SESSION_ID", "s1")

    async def down(*_a, **_k):
        raise ValueError("no server")

    monkeypatch.setattr(reads.transport, "_post", down)
    out, code = ph.answer_hook("cursor", CURSOR_SHELL, NAMES, fail_closed=fail_closed)
    assert (json.loads(out).get("permission"), code) == (expected, 0)


@pytest.mark.parametrize(("mode", "timeout"), [("enforce", 5), ("prompt", None)])
def test_an_enforce_ask_times_out_inside_cursors_hook_timeout(monkeypatch, mode, timeout):
    """Cursor kills a hook after 10 s; an enforce ask gives up at 5 s so the
    hook still answers (deny when fail-closed)."""
    import asyncio

    import httpx

    monkeypatch.setenv("KRAFT_SESSION_ID", "s1")
    seen = {}

    async def slow(method, path, **kw):
        seen.update(kw)
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(reads.transport, "_send", slow)
    got = asyncio.run(reads.permission_request("Bash", {}, mode=mode))
    assert seen.get("timeout") == timeout
    assert got["behavior"] == ("unavailable" if mode == "enforce" else "deny")


# -- codex (Kraft-4in7z.3) -----------------------------------------------------

#: codex-cli 0.155.0's PreToolUse payload, from a real `codex exec` (2026-09-23).
CODEX_BASH = {
    "session_id": "01a0ced8-e57d-7150-ba32-5a4af78ed608",
    "turn_id": "01a0ced8-e652-75a0-9c15-bc2eefafa199",
    "transcript_path": "/home/u/.codex/sessions/rollout.jsonl",
    "cwd": "/wt",
    "hook_event_name": "PreToolUse",
    "model": "gpt-5.6-terra",
    "permission_mode": "default",
    "tool_name": "Bash",
    "tool_input": {"command": "echo hello"},
    "tool_use_id": "exec-1d7bc1d4",
}
CODEX_NAMES = {"apply_patch": ("Write", "Edit")}


def _codex(behavior, *, worktree, seen=None, fail_closed=False, **payload):
    return ph.answer_hook(
        "codex",
        json.dumps({**CODEX_BASH, "cwd": str(worktree), **payload}),
        CODEX_NAMES,
        fail_closed=fail_closed,
        ask=_asker(behavior, seen),
    )


@pytest.fixture
def worktree(tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    (wt / "sub").mkdir(parents=True)
    (tmp_path / "codex-home" / "memories").mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    return wt


def test_codex_asks_with_its_own_tool_names(worktree):
    seen = []
    _codex("no_opinion", worktree=worktree, seen=seen)
    patch = {"command": "*** Begin Patch\n*** Add File: note.txt\n+x\n*** End Patch"}
    _codex("no_opinion", worktree=worktree, seen=seen, tool_name="apply_patch", tool_input=patch)
    (bash, bash_input, use_id, kw), (edit, _input, _id, edit_kw) = seen
    assert (bash, bash_input, use_id) == ("Bash", {"command": "echo hello"}, "exec-1d7bc1d4")
    assert (kw["harness"], kw["cli_tool"], kw["mode"]) == ("codex", "Bash", "enforce")
    assert (edit, edit_kw["also"], edit_kw["cli_tool"]) == ("Write", ("Edit",), "apply_patch")


@pytest.mark.parametrize(
    ("behavior", "expected"),
    [
        ("deny", {"permissionDecision": "deny", "permissionDecisionReason": "Kraft: because"}),
        ("allow", {"permissionDecision": "allow", "permissionDecisionReason": "Kraft: because"}),
    ],
)
def test_codex_renders_a_decision_as_claude_shaped_hook_output(worktree, behavior, expected):
    out, code = _codex(behavior, worktree=worktree)
    assert (json.loads(out), code) == (
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", **expected}},
        0,
    )


def test_codex_no_opinion_is_empty_so_its_reviewer_decides(worktree):
    assert _codex("no_opinion", worktree=worktree) == ("{}", 0)


@pytest.mark.parametrize("fail_closed", [False, True])
def test_codex_s_own_background_agents_are_not_asked(worktree, fail_closed):
    """The hook fires for codex's memory agent too (cwd under codex's home,
    bypassPermissions): no opinion, and the gate is never asked -- even
    when the worker's own session is fail-closed."""
    seen = []
    out = _codex(
        "deny",
        worktree=worktree.parent / "codex-home/memories",
        seen=seen,
        fail_closed=fail_closed,
        permission_mode="bypassPermissions",
    )
    assert (out, seen) == (("{}", 0), [])


def test_codex_calls_anywhere_but_codex_s_home_are_asked(worktree, tmp_path):
    """Outside the worktree too: a worker working from /tmp is still asked."""
    (tmp_path / "link").symlink_to(worktree)
    (tmp_path / "elsewhere").mkdir()
    for cwd in (worktree / "sub", tmp_path / "link", tmp_path / "elsewhere"):
        seen = []
        _codex("deny", worktree=cwd, seen=seen)
        assert len(seen) == 1, cwd
