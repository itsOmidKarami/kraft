#!/usr/bin/env python3
"""A stand-in ACP agent (Agent Client Protocol, JSON-RPC 2.0 over stdio) for tests and `just dev`.

Stdlib only and nothing from Kraft: it stands in for a third-party agent. It answers one turn
with fixed usage and cost, and misbehaves on request. `KRAFT_FAKE_ACP` is a comma-separated list
of flags (see `tests/acp/test_client.py` and `tests/support/acp.py`):

  ask fs stray long replay no_cost no_usage no_resume no_load no_system_prompt slow term die
  refuse_config noresult auth die_after_init hang deaf die_on_int exit_on_int kill noisy late
  slow_init mute badreq die_stderr

`KRAFT_FAKE_ACP_RECORD` is a path: every line read from stdin is appended to it, so a test sees
exactly what the client sent. `KRAFT_FAKE_ACP_STATUS` is the status written to
`$KRAFT_RESULT_PATH` after the turn (default `done`); `KRAFT_FAKE_ACP_TERM_MARK` and
`KRAFT_FAKE_ACP_PIDFILE` are paths a test reads to see a SIGTERM arrive and the pid it was sent to.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

FLAGS = {f for f in os.environ.get("KRAFT_FAKE_ACP", "").split(",") if f}
RECORD = os.environ.get("KRAFT_FAKE_ACP_RECORD")
SESSION = "fake-session-1"
USAGE = {
    "inputTokens": 10,
    "outputTokens": 5,
    "thoughtTokens": 3,
    "totalTokens": 21,
    "cachedReadTokens": 2,
    "cachedWriteTokens": 1,
}
MODELS = ("fake-model", "big")
EFFORTS = ("low", "high")


def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def reply(id_, result: dict) -> None:
    send({"jsonrpc": "2.0", "id": id_, "result": result})


def fail(id_, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})


def update(session_id: str, body: dict) -> None:
    send(
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": session_id, "update": body},
        }
    )


def read():
    """The next JSON line from the client, recorded; None at EOF."""
    while True:
        line = sys.stdin.readline()
        if not line:
            return None
        if RECORD:
            with open(RECORD, "a") as out:
                out.write(line if line.endswith("\n") else line + "\n")
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict):
            return msg


def config_options(model: str = MODELS[0], effort: str = EFFORTS[0]) -> list:
    def one(id_, name, current, values):
        return {
            "id": id_,
            "name": name,
            "category": id_,
            "type": "select",
            "currentValue": current,
            "options": [{"value": v, "name": v} for v in values],
        }

    return [one("model", "Model", model, MODELS), one("effort", "Effort", effort, EFFORTS)]


def await_response(id_) -> dict | None:
    """Read until the client answers our own request `id_`."""
    while (msg := read()) is not None:
        if msg.get("id") == id_ and "method" not in msg:
            return msg
    return None


def write_result() -> None:
    path = os.environ.get("KRAFT_RESULT_PATH")
    if path and "noresult" not in FLAGS:
        with open(path, "w") as out:
            json.dump({"status": os.environ.get("KRAFT_FAKE_ACP_STATUS") or "done"}, out)


def prompt(id_, params: dict) -> None:
    sid = params.get("sessionId", SESSION)
    update(
        sid,
        {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "working on it"},
        },
    )
    if "ask" in FLAGS:
        send(
            {
                "jsonrpc": "2.0",
                "id": "ask-1",
                "method": "session/request_permission",
                "params": {
                    "sessionId": sid,
                    "toolCall": {
                        "toolCallId": "tc-1",
                        "title": "Run ls",
                        "kind": "execute",
                        "rawInput": {"command": "ls"},
                    },
                    "options": [
                        {"optionId": "always", "name": "Always", "kind": "allow_always"},
                        {"optionId": "once", "name": "Once", "kind": "allow_once"},
                        {"optionId": "no", "name": "No", "kind": "reject_once"},
                    ],
                },
            }
        )
        await_response("ask-1")
    if "fs" in FLAGS:
        send(
            {
                "jsonrpc": "2.0",
                "id": "fs-1",
                "method": "fs/read_text_file",
                "params": {"sessionId": sid, "path": "/etc/hosts"},
            }
        )
        await_response("fs-1")
    update(
        sid,
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "tc-1",
            "title": "Run ls",
            "kind": "execute",
            "status": "completed",
        },
    )
    if "no_cost" not in FLAGS:
        update(
            sid,
            {
                "sessionUpdate": "usage_update",
                "used": 999999,
                "size": 200000,
                "cost": {"amount": 0.0123, "currency": "USD"},
            },
        )
    if "badreq" in FLAGS:  # requests no client can make sense of: it must answer, not die
        send({"jsonrpc": "2.0", "id": ["not", "hashable"], "method": "x/odd", "params": {}})
        send(
            {"jsonrpc": "2.0", "id": "odd-1", "method": "session/request_permission", "params": [1]}
        )
        await_response("odd-1")
    if "noisy" in FLAGS:  # past the 64 KB a pipe holds, after one line past any limit
        sys.stderr.write("x" * 5000 + "\n")
        for n in range(300):
            sys.stderr.write(f"noise {n} " + "y" * 900 + "\n")
        sys.stderr.write("stderr-done\n")
        sys.stderr.flush()
    if "die" in FLAGS:
        os._exit(3)
    if "die_stderr" in FLAGS:
        # Gone at once with a failure code, but a child it left behind still holds stderr and
        # writes the crash's last line 0.3s later: the client must wait for the readers.
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys, time; time.sleep(0.3); "
                "print('fatal: the agent crashed', file=sys.stderr)",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            start_new_session=True,
        )
        os._exit(3)
    if "kill" in FLAGS:
        os.kill(os.getpid(), signal.SIGKILL)
    stop = "end_turn"
    if "slow" in FLAGS:
        if "die_on_int" in FLAGS:
            signal.signal(signal.SIGINT, lambda *_: os._exit(130))
        elif "exit_on_int" in FLAGS:
            signal.signal(signal.SIGINT, lambda *_: os._exit(0))
        else:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        while (msg := read()) is not None:
            if msg.get("method") == "session/cancel":
                break
        if "late" in FLAGS:  # answers the cancel after the client has stopped waiting
            time.sleep(1.0)
        stop = "cancelled"
    write_result()
    result = {"stopReason": stop}
    if "no_usage" not in FLAGS:
        result["usage"] = dict(USAGE)
    reply(id_, result)


def main() -> None:
    pidfile = os.environ.get("KRAFT_FAKE_ACP_PIDFILE")
    if pidfile:
        with open(pidfile, "w") as out:
            out.write(str(os.getpid()))
    if "term" in FLAGS:

        def on_term(*_):
            mark = os.environ.get("KRAFT_FAKE_ACP_TERM_MARK")
            if mark:
                with open(mark, "w") as out:
                    out.write("term")
            os._exit(143)

        signal.signal(signal.SIGTERM, on_term)
    if "deaf" in FLAGS:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if "stray" in FLAGS:
        print("not json", flush=True)

    while (msg := read()) is not None:
        method, id_, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method == "initialize":
            if "mute" in FLAGS:
                continue  # never answers
            if "slow_init" in FLAGS:
                time.sleep(1.0)
            if "long" in FLAGS:
                send({"jsonrpc": "2.0", "method": "x/pad", "params": {"pad": "x" * 5000}})
            caps = {}
            if "no_resume" not in FLAGS:
                caps["resume"] = {}
            if "no_system_prompt" not in FLAGS:
                caps["systemPrompt"] = {}
            reply(
                id_,
                {
                    "protocolVersion": 1,
                    "agentInfo": {"name": "fake-acp-agent", "version": "0.0.0"},
                    "agentCapabilities": {
                        "loadSession": "no_load" not in FLAGS,
                        "sessionCapabilities": caps,
                    },
                    "authMethods": [],
                },
            )
            if "die_after_init" in FLAGS:
                os._exit(3)
        elif method == "session/new":
            if "auth" in FLAGS:
                fail(id_, -32000, "Authentication required")
            else:
                reply(id_, {"sessionId": SESSION, "configOptions": config_options()})
        elif method in ("session/load", "session/resume"):
            sid = params.get("sessionId", SESSION)
            if method == "session/load" and "replay" in FLAGS:
                for n in (1, 2):
                    update(
                        sid,
                        {
                            "sessionUpdate": "tool_call",
                            "toolCallId": f"old-{n}",
                            "title": "earlier call",
                            "kind": "execute",
                            "status": "completed",
                        },
                    )
            reply(id_, {"configOptions": config_options()})
        elif method == "session/set_config_option":
            value = params.get("value")
            offered = {"model": MODELS, "effort": EFFORTS}.get(params.get("configId"), ())
            if "refuse_config" in FLAGS or value not in offered:
                fail(
                    id_,
                    -32602,
                    f"invalid value {value!r} for {params.get('configId')!r}; "
                    f"offered: {list(offered)}",
                )
            else:
                reply(id_, {"configOptions": config_options(**{params["configId"]: value})})
        elif method == "session/prompt":
            prompt(id_, params)
        elif method == "session/cancel":
            pass
        elif id_ is not None and method is not None:
            fail(id_, -32601, f"method not found: {method}")

    if "hang" in FLAGS:
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()
