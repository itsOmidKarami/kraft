import asyncio
import inspect
import io
import json
import os
import signal
import sys
import time
from types import SimpleNamespace

import pytest
from support.acp import (
    agent_argv,
    client_argv,
    client_process,
    recorded,
    run_client,
    spec,
)
from support.acp import (
    events as _events,
)

from kraft.acp import log


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


@pytest.mark.parametrize(
    ("fields", "marked"),
    [({}, True), ({"agent_argv": []}, False)],
    ids=["written-before-the-agent-spawns", "not-written-when-the-client-fails-first"],
)
def test_the_client_marks_that_it_got_as_far_as_spawning(tmp_path, fields, marked):
    marker = tmp_path / "acp-started"
    run_client(tmp_path, started_marker=str(marker), **fields)
    assert marker.exists() is marked


@pytest.mark.parametrize(
    "nested", [False, True], ids=["marker-in-its-environment", "agent-is-a-client"]
)
def test_a_client_that_is_its_own_agent_refuses_instead_of_spawning_itself(tmp_path, nested):
    # Wrapping the client's own command instead of the agent's argv makes the agent a client: the
    # inner one finds the marker the outer set for its agent, and stops there.
    from kraft.acp.client import CLIENT_ENV

    inner_result = tmp_path / "inner-result.json"
    if nested:
        inner = spec(tmp_path, at=tmp_path / "inner.json", result_path=str(inner_result))
        done, _ = run_client(tmp_path, agent_argv=client_argv(inner))
    else:
        done, _ = run_client(tmp_path, env={CLIENT_ENV: "1"})
    failed = inner_result if nested else tmp_path / "result.json"
    assert json.loads(failed.read_text()) == {
        "status": "failed",
        "error": "the agent argv is itself a Kraft ACP client",
    }
    assert done.returncode == 78
    assert not recorded(tmp_path)  # no agent ever started


def test_the_log_records_the_agent_argv_as_it_was_before_a_sandbox_wrapped_it(tmp_path):
    original = agent_argv()
    wrapped = ["env", "SECRET=canary-9f3a", *original]  # stands in for `docker run -e SECRET=… …`
    done, records = run_client(tmp_path, agent_argv=wrapped, logged_agent_argv=original)
    assert done.returncode == 0
    assert _events(records, "starting")[0]["agent"] == original
    assert "canary-9f3a" not in done.stdout  # the client's whole log goes to stdout


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
    ("flags", "code", "logged"),
    [
        ("die", 3, None),
        ("kill", 137, None),
        ("die_stderr", 3, "fatal: the agent crashed"),
    ],
    ids=[
        "agent-died-mid-turn",
        "agent-sigkilled-mid-turn-reads-as-a-shell-would",
        "agent-died-at-once-its-last-stderr-line-is-in-the-log",
    ],
)
def test_the_agents_own_exit_code_is_the_clients_when_it_died_before_the_turn_ended(
    tmp_path, flags, code, logged
):
    done, recs = run_client(tmp_path, flags)
    assert done.returncode == code
    if logged:
        assert logged in [r["text"] for r in recs if r.get("event") == "stderr"]


@pytest.mark.parametrize(
    ("argv", "needle"),
    [(["/no/such/agent-binary"], "no/such"), ([], "no agent command")],
    ids=["missing-binary", "empty-argv"],
)
def test_an_agent_that_cannot_start_is_a_config_error_with_a_failed_result_file(
    tmp_path, argv, needle
):
    done, recs = run_client(tmp_path, agent_argv=argv)
    assert done.returncode == 78
    assert needle in _events(recs, "config_error")[0]["message"]
    assert json.loads((tmp_path / "result.json").read_text())["status"] == "failed"
    # logged before the spawn is tried, so even a launch that never ran has its first line
    assert [r["event"] for r in recs if r["dir"] == "client"][0] == "starting"


@pytest.mark.parametrize("precreated", [False, True], ids=["absent", "pre-created-empty"])
def test_a_login_the_agent_wants_is_a_failed_result_not_a_hang(tmp_path, precreated):
    if precreated:  # Kraft creates the session's empty result file before the agent starts
        (tmp_path / "result.json").write_text("")
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


def test_an_agent_that_never_answers_initialize_fails_with_a_reason_not_a_hang(tmp_path):
    done, recs = run_client(tmp_path, "mute", handshake_timeout_s=1.0, timeout=20)
    assert done.returncode == 1
    assert "did not answer initialize within 1s" in _events(recs, "error")[0]["message"]
    assert "initialize" in json.loads((tmp_path / "result.json").read_text())["error"]
    assert "session/prompt" not in [m.get("method") for m in recorded(tmp_path)]


def test_stderr_is_drained_past_what_a_pipe_holds_and_past_a_line_over_the_limit(tmp_path):
    # `noisy` writes one 5000-byte line, then ~300 KB more, before it answers the prompt: a
    # reader that stopped at the long line would let the 64 KB pipe fill and block the agent.
    done, recs = run_client(tmp_path, "noisy", line_limit=2000, timeout=30)
    assert done.returncode == 0, done.stderr
    assert [r["text"] for r in recs if r.get("event") == "stderr"][-1] == "stderr-done"
    assert _events(recs, "line_too_long")[0]["stream"] == "stderr"
    assert _events(recs, "stop")[0]["stop_reason"] == "end_turn"


def test_an_agent_request_with_an_unhashable_id_or_non_object_params_is_answered_invalid(tmp_path):
    done, recs = run_client(tmp_path, "badreq")
    assert done.returncode == 0, done.stderr
    sent = recorded(tmp_path)
    by_id = {json.dumps(m.get("id")): m for m in sent if "error" in m}
    assert by_id["null"]["error"]["code"] == by_id['"odd-1"']["error"]["code"] == -32600
    assert not _events(recs, "error")  # the reader never failed on them


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
    done, _ = run_client(tmp_path, flags, grace_s=1.0, env={"KRAFT_FAKE_ACP_PIDFILE": str(pidfile)})
    elapsed = time.monotonic() - started
    assert done.returncode == 0, done.stderr
    # `ceiling_s` is grace + the terminate wait (+ the kill); `+ 8` is interpreter start-up margin
    assert elapsed < ceiling_s + 8
    with pytest.raises(ProcessLookupError):  # the agent is gone, not left running
        os.kill(int(pidfile.read_text()), 0)


def test_the_client_never_imports_the_server(tmp_path):
    # It sits on every ACP session's critical path: the FastAPI app stays out of it.
    done, _ = run_client(
        tmp_path, argv=[sys.executable, "-X", "importtime", *client_argv(spec(tmp_path))[1:]]
    )
    assert done.returncode == 0, done.stderr
    imported = {line.rsplit("|", 1)[-1].strip() for line in done.stderr.splitlines()}
    assert "kraft.acp.client" in imported
    assert not {m for m in imported if m.split(".")[0] in ("fastapi", "starlette", "textual")}
    assert not {m for m in imported if m.startswith("kraft.api")}


@pytest.mark.parametrize(
    ("flags", "method", "replayed"),
    [
        ("", "session/resume", False),
        ("no_resume", "session/load", False),
        ("no_resume,replay", "session/load", True),
    ],
    ids=["resume-preferred", "load-fallback", "load-replay-marked"],
)
def test_a_resume_prefers_session_resume_then_session_load(tmp_path, flags, method, replayed):
    _, recs = run_client(tmp_path, flags, resume="old-session")
    assert method in [m["method"] for m in recorded(tmp_path) if "method" in m]
    ev = _events(recs, "session")[0]
    assert ev["session_id"] == "old-session" and ev["resumed"] == method.split("/")[1]
    marked = [r for r in recs if r.get("replay")]
    assert bool(marked) is replayed


def test_an_agent_that_cannot_resume_at_all_fails_the_launch(tmp_path):
    done, _ = run_client(tmp_path, "no_resume,no_load", resume="old")
    assert done.returncode == 1
    assert "neither" in json.loads((tmp_path / "result.json").read_text())["error"]


@pytest.mark.parametrize(
    ("flags", "resume", "where"),
    [
        ("", None, "systemPrompt"),
        ("no_system_prompt", None, "prompt"),
        ("", "old-session", "systemPrompt"),
        ("no_system_prompt", "old-session", "prompt"),
    ],
    ids=["object-when-offered", "folded-when-not", "resume-object-when-offered", "resume-folded"],
)
def test_context_rides_the_system_prompt_object_or_folds_into_the_prompt(
    tmp_path, flags, resume, where
):
    run_client(tmp_path, flags, context="THE-CONTEXT", prompt="THE-TASK", resume=resume)
    sent = {m["method"]: m["params"] for m in recorded(tmp_path) if "method" in m}
    opened = sent["session/resume" if resume else "session/new"]
    if where == "systemPrompt":
        assert opened["systemPrompt"] == {
            "mode": "append",
            "content": [{"type": "text", "text": "THE-CONTEXT"}],
        }
        assert sent["session/prompt"]["prompt"][0]["text"] == "THE-TASK"
    else:
        assert "systemPrompt" not in opened
        assert sent["session/prompt"]["prompt"][0]["text"] == "THE-CONTEXT\n\nTHE-TASK"


def test_model_and_effort_go_through_set_config_option_with_the_entrys_ids(tmp_path):
    _, recs = run_client(tmp_path, model="big", effort="high")
    sets = [
        m["params"] for m in recorded(tmp_path) if m.get("method") == "session/set_config_option"
    ]
    assert [(s["configId"], s["value"]) for s in sets] == [("model", "big"), ("effort", "high")]
    assert _events(recs, "session")[0]["model"] == "big"


def test_a_refused_config_option_is_a_config_error_naming_what_was_offered(tmp_path):
    done, recs = run_client(tmp_path, "refuse_config", model="nope")
    assert done.returncode == 78
    message = _events(recs, "config_error")[0]["message"]
    assert "model" in message and "fake-model" in message


def _until(proc, match):
    for line in proc.stdout:
        rec = next(log.records([line]), None)
        if rec and match(rec):
            return


def _until_prompting(proc):
    _until(proc, lambda rec: rec.get("event") == "prompting")


def test_sigint_becomes_session_cancel_and_the_usage_still_lands(tmp_path):
    with client_process(tmp_path, "slow") as proc:
        _until_prompting(proc)
        proc.send_signal(signal.SIGINT)  # the client only: the fake ignores SIGINT itself
        rest, _ = proc.communicate(timeout=30)
    recs = list(log.records(rest.splitlines()))
    assert "session/cancel" in [m.get("method") for m in recorded(tmp_path)]
    assert _events(recs, "stop")[0]["stop_reason"] == "cancelled"
    assert proc.returncode == 130
    assert any(r.get("msg", {}).get("result", {}).get("usage") for r in recs)


def test_a_sigint_during_the_handshake_starts_no_turn(tmp_path):
    # `slow_init` holds the initialize answer a second: the pause lands before any prompt.
    with client_process(tmp_path, "slow_init") as proc:
        _until(proc, lambda rec: rec.get("dir") == "to_agent")  # initialize is out; handlers set
        proc.send_signal(signal.SIGINT)
        rest, _ = proc.communicate(timeout=30)
    assert proc.returncode == 130
    assert "session/prompt" not in [m.get("method") for m in recorded(tmp_path)]
    assert not (tmp_path / "result.json").exists()  # a pause is not a failed result
    assert _events(list(log.records(rest.splitlines())), "interrupted")[0]["before"] == "prompt"


def test_a_late_answer_after_the_cancel_wait_is_dropped_not_a_reader_failure(tmp_path):
    # `late` answers the cancel a second after the client stopped waiting (0.3s): the answer
    # reaches a future the timeout cancelled.
    with client_process(tmp_path, "slow,late", cancel_wait_s=0.3) as proc:
        _until_prompting(proc)
        proc.send_signal(signal.SIGINT)
        rest, _ = proc.communicate(timeout=30)
    recs = list(log.records(rest.splitlines()))
    assert proc.returncode == 130
    assert _events(recs, "cancel_timeout")
    assert any(r.get("msg", {}).get("result", {}).get("stopReason") for r in recs)  # it did land
    assert not _events(recs, "error")


@pytest.mark.parametrize(
    ("rc", "interrupted", "terminated", "expect"),
    [(0, True, False, 130), (1, True, False, 130), (0, False, True, 143), (0, True, True, 143)],
    ids=["exit-0-after-sigint", "exit-1-after-sigint", "exit-0-after-sigterm", "term-wins"],
)
def test_an_agent_that_is_gone_after_a_signal_is_that_signals_code_not_a_failure(
    tmp_path, rc, interrupted, terminated, expect
):
    async def go():
        client = _client(tmp_path, io.StringIO())

        async def wait():
            return rc

        client.proc = SimpleNamespace(wait=wait, returncode=rc)
        if interrupted:
            client.interrupted.set()
        client.terminated = terminated
        return await client.agent_exit()

    assert asyncio.run(go()) == expect


def test_the_cancel_wait_ends_before_kraft_kills_the_group_after_a_pause():
    from kraft.acp.spec import LaunchSpec
    from kraft.adapters import subprocess as run_subprocess

    flush_grace = inspect.signature(run_subprocess._run_task).parameters["flush_grace"].default
    assert LaunchSpec.model_fields["cancel_wait_s"].default < flush_grace


@pytest.mark.parametrize(
    "flags",
    ["slow,die_on_int", "slow,exit_on_int"],
    ids=["agent-dies-on-it", "agent-exits-0-on-it"],
)
def test_a_group_sigint_ends_the_client_130_whatever_the_agent_exits_with(tmp_path, flags):
    # Kraft's pause signals the whole group: the fake exits on that SIGINT, with code 130 or 0.
    # The client's own SIGINT takes the cancel path here, so this proves the group signal ends 130
    # against a real process; the agent_exit race itself is pinned by the unit rows above.
    with client_process(tmp_path, flags) as proc:
        _until_prompting(proc)
        os.killpg(proc.pid, signal.SIGINT)
        proc.communicate(timeout=30)
    assert proc.returncode == 130


def test_sigterm_is_forwarded_to_the_agent_and_its_code_is_the_clients(tmp_path):
    mark = tmp_path / "term-mark"
    with client_process(tmp_path, "slow,term", env={"KRAFT_FAKE_ACP_TERM_MARK": str(mark)}) as proc:
        _until_prompting(proc)
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=30)
    assert mark.exists()
    assert proc.returncode == 143
