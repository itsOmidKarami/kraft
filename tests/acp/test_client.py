import asyncio
import io
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from support.acp import client_argv, client_env, recorded, run_client, spec


def _events(records, name):
    return [r for r in records if r.get("dir") == "client" and r.get("event") == name]


def test_one_launch_is_one_session_one_prompt_and_exit_zero(tmp_path):
    done, recs = run_client(tmp_path)
    assert done.returncode == 0, done.stderr
    methods = [m["method"] for m in recorded(tmp_path) if "method" in m]
    assert methods[:2] == ["initialize", "session/new"]
    assert methods.count("session/prompt") == 1
    assert _events(recs, "starting")[0]["agent"][-1].endswith("fake_acp_agent.py")
    assert _events(recs, "session")[0]["session_id"] == "fake-session-1"
    assert _events(recs, "stop")[0]["stop_reason"] == "end_turn"
    assert json.loads((tmp_path / "result.json").read_text()) == {"status": "done"}


def test_every_message_both_ways_is_a_log_line(tmp_path):
    _, recs = run_client(tmp_path)
    dirs = [
        (r["dir"], r["msg"].get("method") or "response")
        for r in recs
        if r["dir"] in ("to_agent", "from_agent")
    ]
    assert ("to_agent", "initialize") in dirs and ("from_agent", "session/update") in dirs
    assert ("from_agent", "response") in dirs


@pytest.mark.parametrize(
    ("flags", "code"),
    [("die", 3)],
    ids=["agent-died-mid-turn"],
)
def test_the_agents_own_exit_code_is_the_clients_when_it_died_before_the_turn_ended(
    tmp_path, flags, code
):
    done, _ = run_client(tmp_path, flags)
    assert done.returncode == code


def test_a_missing_agent_is_a_config_error_with_a_failed_result_file(tmp_path):
    done, recs = run_client(tmp_path, agent_argv=["/no/such/agent-binary"])
    assert done.returncode == 78
    assert "no/such" in _events(recs, "config_error")[0]["message"]
    assert json.loads((tmp_path / "result.json").read_text())["status"] == "failed"


def test_a_login_the_agent_wants_is_a_failed_result_not_a_hang(tmp_path):
    done, recs = run_client(tmp_path, "auth")
    assert done.returncode == 1
    assert "Authentication required" in json.loads((tmp_path / "result.json").read_text())["error"]


def test_the_agents_own_result_file_is_never_overwritten(tmp_path):
    (tmp_path / "result.json").write_text('{"status": "done_with_concerns"}')
    run_client(tmp_path, "auth")
    assert json.loads((tmp_path / "result.json").read_text())["status"] == "done_with_concerns"


def test_a_non_json_stdout_line_is_logged_as_stray_and_the_turn_still_ends(tmp_path):
    done, recs = run_client(tmp_path, "stray")
    assert done.returncode == 0
    assert [r["text"] for r in recs if r["dir"] == "stray"] == ["not json"]
    assert json.loads((tmp_path / "result.json").read_text()) == {"status": "done"}


def test_an_fs_request_gets_method_not_found_and_initialize_offers_no_fs(tmp_path):
    _, recs = run_client(tmp_path, "fs")
    sent = recorded(tmp_path)
    assert sent[0]["params"]["clientCapabilities"] == {}
    reply = next(m for m in sent if m.get("id") == "fs-1")
    assert reply["error"]["code"] == -32601
    # and both halves are in the log: the request that came in, the refusal that went out
    msgs = [(r["dir"], r["msg"]) for r in recs if r["dir"] in ("from_agent", "to_agent")]
    assert any(d == "from_agent" and m.get("method") == "fs/read_text_file" for d, m in msgs)
    assert any(d == "to_agent" and m.get("error", {}).get("code") == -32601 for d, m in msgs)


def test_a_permission_ask_is_answered_allow_once_by_kind_and_logged(tmp_path):
    _, recs = run_client(tmp_path, "ask")
    reply = next(m for m in recorded(tmp_path) if m.get("id") == "ask-1")
    assert reply["result"]["outcome"] == {"outcome": "selected", "optionId": "once"}
    assert _events(recs, "ask")[0]["answer"] == "allow_once"


def test_a_line_past_the_limit_is_skipped_logged_and_the_turn_still_ends(tmp_path):
    # the fake's `long` flag sends one 5000-byte line; every real message is well under 2000
    done, recs = run_client(tmp_path, "long", line_limit=2000)
    assert done.returncode == 0, done.stderr
    assert _events(recs, "line_too_long")[0]["limit"] == 2000
    assert _events(recs, "stop")[0]["stop_reason"] == "end_turn"


def test_an_agent_that_dies_before_session_new_ends_the_launch_not_hangs_it(tmp_path):
    # `die_after_init` answers initialize and exits: the old client registered a future for
    # `session/new` that nobody could resolve. The timeout is the pin: it is far below a hang.
    done, recs = run_client(tmp_path, "die_after_init", timeout=20)
    assert done.returncode == 3
    assert _events(recs, "error")


def _client(tmp_path, out):
    from kraft.acp.client import Client
    from kraft.acp.spec import LaunchSpec

    return Client(LaunchSpec.model_validate_json(spec(tmp_path).read_text()), out)


def test_request_after_the_agent_is_gone_raises_at_once(tmp_path):
    from kraft.acp.client import AgentGone

    async def go():
        client = _client(tmp_path, io.StringIO())
        client.gone = True
        with pytest.raises(AgentGone):
            await asyncio.wait_for(client.request("session/new", {}), 1)
        assert client._pending == {}

    asyncio.run(go())


def test_a_pump_that_raises_is_logged_and_treated_as_eof_not_silent(tmp_path):
    from kraft.acp.client import AgentGone

    async def go():
        out = io.StringIO()
        client = _client(tmp_path, out)
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"jsonrpc":"2.0","id":9,"method":"anything"}\n')
        client.proc = SimpleNamespace(stdout=reader, stdin=None)

        async def boom(msg):
            raise RuntimeError("boom")

        client._dispatch = boom
        waiting = asyncio.get_running_loop().create_future()
        client._pending[1] = waiting
        await client.pump()
        assert client.gone and isinstance(waiting.exception(), AgentGone)
        assert "boom" in out.getvalue()

    asyncio.run(go())


@pytest.mark.parametrize(
    ("flags", "ceiling_s"),
    [("hang", 1 + 2), ("hang,deaf", 1 + 2 + 2)],
    ids=["ignores-stdin-eof-sigterm-ends-it", "ignores-sigterm-too-sigkill-ends-it"],
)
def test_an_agent_that_will_not_exit_is_ended_within_the_grace_and_the_ladder(
    tmp_path, flags, ceiling_s
):
    pidfile = tmp_path / "agent.pid"
    started = time.monotonic()
    done = subprocess.run(
        client_argv(spec(tmp_path, grace_s=1.0)),
        capture_output=True,
        text=True,
        timeout=60,
        env=client_env(tmp_path, flags, KRAFT_FAKE_ACP_PIDFILE=str(pidfile)),
        cwd=tmp_path,
    )
    elapsed = time.monotonic() - started
    assert done.returncode == 0, done.stderr
    # `ceiling_s` is grace + the terminate wait (+ the kill); `+ 8` is interpreter start-up margin
    assert elapsed < ceiling_s + 8
    with pytest.raises(ProcessLookupError):  # the agent is gone, not left running
        os.kill(int(pidfile.read_text()), 0)


def test_the_client_never_imports_the_server(tmp_path):
    # It sits on every ACP session's critical path: the FastAPI app stays out of it.
    done = subprocess.run(
        [sys.executable, "-X", "importtime", *client_argv(spec(tmp_path))[1:]],
        capture_output=True,
        text=True,
        env=client_env(tmp_path),
        timeout=60,
        cwd=tmp_path,
    )
    assert done.returncode == 0, done.stderr
    imported = {line.rsplit("|", 1)[-1].strip() for line in done.stderr.splitlines()}
    assert "kraft.acp.client" in imported
    assert not {m for m in imported if m.split(".")[0] in ("fastapi", "starlette", "textual")}
    assert not {m for m in imported if m.startswith("kraft.api")}
