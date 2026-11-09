"""The contract Kraft assumes of `claude-agent-acp` (spec §6, §7, §11).

These tests depend on the machine's Claude settings: the bridge loads `settingSources`
user/project/local (and, with the operator's HOME, their login), so a `permissions` rule of the
operator's own can make it ask less or more than the entry says. They run with the real HOME.
"""

from pathlib import Path

import pytest
from support.acp import fake_gate, run_client, run_inprocess

from kraft import harness
from kraft.usage import READERS
from kraft.yamlload import safe_load

# `bd`: the operator's own Claude hooks (a `bd prime` SessionStart hook, say) run inside the bridge.
pytestmark = pytest.mark.e2e("claude", "claude-agent-acp", "bd")
ENTRY = Path(__file__).resolve().parents[2] / "fixtures" / "claude-acp.yaml"
REAL_HOME = {"HOME": str(Path.home())}  # the suite points HOME at an empty folder: no Claude login


def fields(**more):
    """The client spec fields the entry yields, as `launch.spec_for` builds them."""
    h = harness.Harness.from_mapping(safe_load(ENTRY.read_text()), where=str(ENTRY), path=ENTRY)
    asks = h.acp.asks_everything
    return {
        "agent_argv": list(h.command),
        "harness_id": h.id,
        "asks_mode": asks.mode,
        "always_deny": list(h.acp.always_deny),
        "settles_itself": list(h.acp.settles_itself),
        "options": dict(h.acp.options),
        "model": "haiku",
        "context": "",
        "policy": True,
        "tool_names": {k: list(v) for k, v in h.tool_names.items()},
        **more,
    }


def agent_text(recs) -> str:
    out = []
    for r in recs:
        msg = r.get("msg") or {}
        upd = (msg.get("params") or {}).get("update") or {}
        if r.get("dir") == "from_agent" and upd.get("sessionUpdate") == "agent_message_chunk":
            out.append((upd.get("content") or {}).get("text", ""))
    return "".join(out)


def test_manual_mode_asks_before_a_write_by_its_tool_name_and_a_rejection_holds(
    tmp_path, monkeypatch
):
    code, recs = run_inprocess(
        tmp_path,
        monkeypatch,
        "",
        ask=fake_gate(default="deny"),
        env=REAL_HOME,
        prompt="Create the file out.txt containing hi, using your Write tool.",
        **fields(),
    )
    asks = [r for r in recs if r.get("event") == "ask"]
    assert any(a["kraft_tool"] == ["Write"] and a["answer"] == "reject_once" for a in asks)
    assert not (tmp_path / "out.txt").exists()
    assert not [r for r in recs if r.get("event") == "policy_violation"]


def test_a_turn_reports_per_turn_usage_and_a_cost_and_takes_the_system_prompt_as_an_object(
    tmp_path,
):
    done, recs = run_client(
        tmp_path,
        prompt="Reply with the single word ok. Use no tools.",
        timeout=240,
        env=REAL_HOME,
        **fields(policy=False, context="You answer in lowercase."),
    )
    assert done.returncode == 0, done.stdout[-2000:]
    new = next(
        r["msg"] for r in recs if r["dir"] == "to_agent" and r["msg"].get("method") == "session/new"
    )
    assert new["params"]["systemPrompt"] == {
        "mode": "append",
        "content": [{"type": "text", "text": "You answer in lowercase."}],
    }
    used = READERS["acp"].stream(done.stdout.splitlines(), {})
    assert used.tokens_in + used.tokens_out + used.tokens_cache_read > 0 and used.cost_usd > 0


def test_a_session_resumes_in_a_new_process_with_its_history(tmp_path):
    first, recs = run_client(
        tmp_path,
        prompt="Remember the number 7319. Reply ok.",
        timeout=240,
        env=REAL_HOME,
        **fields(policy=False),
    )
    sid = next(r["session_id"] for r in recs if r.get("event") == "session")
    second, recs2 = run_client(
        tmp_path,
        prompt="What number did I ask you to remember? Reply with it.",
        timeout=240,
        env=REAL_HOME,
        **fields(policy=False, resume=sid),
    )
    assert second.returncode == 0, second.stdout[-2000:]
    assert next(r for r in recs2 if r.get("event") == "session")["resumed"] == "resume"
    assert "7319" in agent_text(recs2)
