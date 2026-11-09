"""The contract Kraft assumes of `opencode acp`: handshake, session, one turn, per-turn usage."""

import json

import pytest
from support.acp import fake_gate, run_client, run_inprocess

pytestmark = pytest.mark.e2e("opencode")


def test_opencode_acp_answers_one_turn_with_usage(tmp_path):
    done, recs = run_client(
        tmp_path,
        agent_argv=["opencode", "acp"],
        prompt="Reply with the single word ok. Use no tools.",
        context="",
        timeout=300,
    )
    assert done.returncode == 0, done.stdout[-2000:]
    turn = next(
        r["msg"]["result"]
        for r in recs
        if r["dir"] == "from_agent" and "stopReason" in (r["msg"].get("result") or {})
    )
    assert turn["stopReason"] == "end_turn"
    block = turn["usage"]
    assert isinstance(block, dict)
    # every part the agent sends is one addend of its total: thought tokens are in addition to
    # output tokens, and cache is apart from input tokens.
    parts = (
        "inputTokens",
        "outputTokens",
        "thoughtTokens",
        "cachedReadTokens",
        "cachedWriteTokens",
    )
    if "totalTokens" in block:
        assert sum(block.get(k, 0) for k in parts) == block["totalTokens"], json.dumps(block)
    session = next(r for r in recs if r.get("event") == "session")
    assert session["session_id"]


TOOLS = {"read": ["Read"], "write": ["Write"], "edit": ["Edit"], "shell": ["Bash"]}
ASK = {"OPENCODE_CONFIG_CONTENT": json.dumps({"permission": {"*": "ask"}})}
ALLOW = {"OPENCODE_CONFIG_CONTENT": json.dumps({"permission": {"*": "allow"}})}


def _opencode(tmp_path, monkeypatch, prompt, env, gate, **fields):
    (tmp_path / "notes.txt").write_text("hello\n")
    return run_inprocess(
        tmp_path,
        monkeypatch,
        "",
        ask=gate,
        agent_argv=["opencode", "acp"],
        prompt=prompt,
        context="",
        options={},
        agent_env=env,
        policy=True,
        tool_names=TOOLS,
        harness_id="opencode-acp",
        **fields,
    )


def test_opencode_asks_per_call_and_a_rejection_holds(tmp_path, monkeypatch):
    code, recs = _opencode(
        tmp_path,
        monkeypatch,
        "Do these in order and do each even if an earlier one is refused: read notes.txt with "
        "your read tool; create out.txt containing hi with your write tool; run `echo hi` with "
        "your shell tool.",
        ASK,
        # default deny: glob/list/todowrite also ask, and must not flake this
        fake_gate({"Read": "allow"}, default="deny"),
    )
    asks = [r for r in recs if r.get("event") == "ask"]
    assert any(a["kraft_tool"] == ["Read"] and a["answer"] == "allow_once" for a in asks)
    # OpenCode ends its turn after a rejection: the launch goes on (spec §10) until both the
    # write and the shell call have been asked about and rejected, and it ends clean
    no = [a["kraft_tool"] for a in asks if a["answer"] == "reject_once" and a["kraft_tool"]]
    rejected = {names[0] for names in no}
    assert {"Write", "Bash"} <= rejected
    assert code == 0, [r for r in recs if r.get("event") in ("rejection_limit", "error")]
    # only the Read/Write/Bash asks are asserted on: the other tools OpenCode calls are not
    # this test's business
    assert all(
        (a["answer"] == "allow_once") == (a["kraft_tool"] == ["Read"])
        for a in asks
        if a["kraft_tool"] in (["Read"], ["Write"], ["Bash"])
    )
    assert not (tmp_path / "out.txt").exists()  # the rejection held
    assert not [r for r in recs if r.get("event") == "policy_violation"]


def test_the_backstop_catches_an_opencode_that_does_not_ask(tmp_path, monkeypatch):
    # The entry *says* it asks, but its switch is wrong here (permission "allow"): the agent
    # that does not behave as declared is what the backstop is for.
    code, recs = _opencode(
        tmp_path,
        monkeypatch,
        "Create out.txt containing hi, using your write tool.",
        ALLOW,
        fake_gate(default="deny"),
        allowlist=True,
    )
    assert code == 77
    assert [r for r in recs if r.get("event") == "policy_violation"]
