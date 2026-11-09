import json
import subprocess
import sys

import pytest
from support.acp import FAKE


def _drive(flags: str, requests: list[dict], tmp_path, extra_env=None):
    env = {
        "KRAFT_FAKE_ACP": flags,
        "KRAFT_FAKE_ACP_RECORD": str(tmp_path / "rec"),
        "KRAFT_RESULT_PATH": str(tmp_path / "result.json"),
        "PATH": "/usr/bin:/bin",
        **(extra_env or {}),
    }
    stdin = "".join(json.dumps(r) + "\n" for r in requests)
    done = subprocess.run(
        [sys.executable, str(FAKE)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return done, [json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")]


def _rpc(id_, method, params=None):
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}


def test_the_fake_agent_runs_one_turn_with_usage_and_cost(tmp_path):
    done, out = _drive(
        "",
        [
            _rpc(1, "initialize", {"protocolVersion": 1}),
            _rpc(2, "session/new", {"cwd": "/", "mcpServers": []}),
            _rpc(3, "session/prompt", {"sessionId": "fake-session-1", "prompt": []}),
        ],
        tmp_path,
    )
    by_id = {m["id"]: m for m in out if "id" in m and "method" not in m}
    assert by_id[1]["result"]["agentCapabilities"]["loadSession"] is True
    assert by_id[2]["result"]["sessionId"] == "fake-session-1"
    assert by_id[3]["result"]["stopReason"] == "end_turn"
    usage = by_id[3]["result"]["usage"]
    assert usage["thoughtTokens"] == 3
    assert usage["totalTokens"] == sum(
        usage[k]
        for k in (
            "inputTokens",
            "outputTokens",
            "thoughtTokens",
            "cachedReadTokens",
            "cachedWriteTokens",
        )
    )
    updates = [m["params"]["update"] for m in out if m.get("method") == "session/update"]
    assert any(u.get("cost", {}).get("amount") == 0.0123 for u in updates)
    assert json.loads((tmp_path / "result.json").read_text()) == {"status": "done"}


@pytest.mark.parametrize(
    ("flags", "must_see"),
    [("stray", "not json"), ("replay", "tool_call"), ("long", "x" * 5000)],
    ids=["stray-stdout-line", "load-replays-history", "overlong-line"],
)
def test_the_fake_agent_misbehaves_on_request(tmp_path, flags, must_see):
    done, out = _drive(
        flags,
        [
            _rpc(1, "initialize", {}),
            _rpc(2, "session/load", {"sessionId": "old", "cwd": "/", "mcpServers": []}),
        ],
        tmp_path,
    )
    assert must_see in done.stdout
