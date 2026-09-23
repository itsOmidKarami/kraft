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
