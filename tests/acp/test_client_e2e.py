"""The contract Kraft assumes of `opencode acp`: handshake, session, one turn, per-turn usage."""

import json

import pytest
from support.acp import run_client

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
