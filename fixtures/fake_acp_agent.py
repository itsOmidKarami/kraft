#!/usr/bin/env python3
"""A stand-in ACP agent (Agent Client Protocol, JSON-RPC 2.0 over stdio) for tests and `just dev`.

Stdlib only and nothing from Kraft: it stands in for a third-party agent. It answers one turn
with fixed usage and cost, and misbehaves on request. `KRAFT_FAKE_ACP` is a comma-separated list
of flags (see `tests/acp/test_client.py` and `tests/support/acp.py`):

  ask fs stray long replay no_cost no_usage no_resume no_load no_system_prompt slow term die
  refuse_config noresult auth die_after_init hang deaf die_on_int exit_on_int kill noisy late
  slow_init mute badreq die_stderr ask_all only_always end_on_reject mode_option refuse_mode
  slow_continue

`ask_all`: the agent asks before every scripted tool call (as if `asks_everything` had been
applied); the same switch is `FAKE_ACP_ASK_ALL=1` in the environment. `only_always`: an ask
offers only an `allow_always` option. `end_on_reject`: a rejected ask ends the turn `cancelled`
(OpenCode does); the calls after it wait, and the next `session/prompt` runs them, so a client
that continues the conversation sees the task finish. `slow_continue`: from the second prompt on,
the turn waits for `session/cancel` as `slow` does.
`mode_option`: the session offers a `mode` config option (`default` makes the agent ask);
`refuse_mode`: `session/set_mode` fails. `KRAFT_FAKE_ACP_CALLS` is a JSON list of scripted tool
calls, see `run_calls`.

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
MODES = ("auto", "default")  # `default` asks before every call, as OpenCode's does


ASKING = {"on": False}
LEFT: dict = {"calls": None, "prompts": 0}  # calls a rejection kept from running; prompts seen


def asking() -> bool:
    """Has the agent been told to ask before every call (asks_everything: env, config or mode)?"""
    return ASKING["on"] or "ask_all" in FLAGS or os.environ.get("FAKE_ACP_ASK_ALL") == "1"


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


def config_options(model: str = MODELS[0], effort: str = EFFORTS[0], mode: str = "auto") -> list:
    def one(id_, name, current, values):
        return {
            "id": id_,
            "name": name,
            "category": id_,
            "type": "select",
            "currentValue": current,
            "options": [{"value": v, "name": v} for v in values],
        }

    options = [one("model", "Model", model, MODELS), one("effort", "Effort", effort, EFFORTS)]
    if "mode_option" in FLAGS:
        options.append(one("mode", "Mode", mode, MODES))
    return options


def await_response(id_) -> dict | None:
    """Read until the client answers our own request `id_`."""
    while (msg := read()) is not None:
        if msg.get("id") == id_ and "method" not in msg:
            return msg
    return None


def await_many(ids: list) -> dict:
    """Read until the client has answered each of our requests in `ids`."""
    got = {}
    while len(got) < len(ids) and (msg := read()) is not None:
        if msg.get("id") in ids and "method" not in msg:
            got[msg["id"]] = msg
    return got


def settle_call(sid: str, c: dict, resp: dict | None) -> str | None:
    outcome = ((resp or {}).get("result") or {}).get("outcome") or {}
    if outcome.get("optionId") == "once" or c.get("run_anyway"):
        late = {"name": c["late_name"]} if c.get("late_name") else {}
        update(
            sid,
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": c["id"],
                "status": "completed",
                **late,
            },
        )
    else:
        update(
            sid, {"sessionUpdate": "tool_call_update", "toolCallId": c["id"], "status": "failed"}
        )
        if "end_on_reject" in FLAGS:
            return "cancelled"  # OpenCode ends the turn after a rejected ask (spec §10)
    return None


def decline(sid: str, queued: list) -> None:
    """The calls queued behind a rejection end `failed`, never having run (OpenCode's text)."""
    for cid in queued:
        declined = "The user declined this tool call"
        update(
            sid,
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": cid,
                "status": "failed",
                "content": [{"type": "content", "content": {"type": "text", "text": declined}}],
                "rawOutput": {"error": declined},
            },
        )


def run_calls(sid: str, calls: list) -> str | None:
    """Scripted tool calls: `KRAFT_FAKE_ACP_CALLS` is a JSON list of {id, name?, kind, input?,
    ask?, end?, run_anyway?, withdraw?, wait?}. A call that asks announces itself pending, goes
    in_progress (OpenCode does, *before* it asks), asks, then completes if allowed and fails if
    rejected; one that does not ask just completes. `ask` absent = ask when `asking()`.
    `run_anyway`: completes although rejected. `withdraw`: sends `$/cancel_request` for its own
    ask. `end: "declined"`: queued, it fails "declined" when a rejection ends the turn.
    `wait: false`: go on without the answer. Returns "cancelled" when a rejection ends the
    turn (flag `end_on_reject`)."""
    waiting, queued = [], []
    for i, c in enumerate(calls):
        cid = c["id"]
        view = {
            "toolCallId": cid,
            "title": c.get("title", c.get("name", c["kind"])),
            "kind": c["kind"],
            "rawInput": c.get("input", {}),
        }
        if c.get("name"):
            view["name"] = c["name"]
        update(sid, {"sessionUpdate": "tool_call", "status": "pending", **view})
        if c.get("end") == "declined":  # queued: it fails when a rejection ends the turn
            queued.append(cid)
            continue
        if not c.get("ask", asking()):
            update(
                sid,
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": cid,
                    "status": c.get("end", "completed"),
                    **({"name": c["late_name"]} if c.get("late_name") else {}),
                },
            )
            continue
        update(
            sid,
            {
                "sessionUpdate": "tool_call_update",
                "status": "in_progress",
                **{k: v for k, v in view.items() if k != "name"},
            },
        )
        always = {"optionId": "always", "name": "Always", "kind": "allow_always"}
        options = (
            [always]
            if "only_always" in FLAGS
            else [
                always,
                {"optionId": "once", "name": "Once", "kind": "allow_once"},
                {"optionId": "no", "name": "No", "kind": "reject_once"},
            ]
        )
        send(
            {
                "jsonrpc": "2.0",
                "id": f"ask-{cid}",
                "method": "session/request_permission",
                "params": {
                    "sessionId": sid,
                    "toolCall": {**view, "status": "pending"},
                    "options": options,
                },
            }
        )
        if c.get("withdraw"):
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "$/cancel_request",
                    "params": {"requestId": f"ask-{cid}"},
                }
            )
        if c.get("wait", True):
            if stop := settle_call(sid, c, await_response(f"ask-{cid}")):
                LEFT["calls"] = calls[i + 1 :]
                decline(sid, queued)
                return stop
        else:
            waiting.append(c)
    got = await_many([f"ask-{c['id']}" for c in waiting])
    for c in waiting:
        if stop := settle_call(sid, c, got.get(f"ask-{c['id']}")):
            return stop
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
    LEFT["prompts"] += 1
    later = LEFT["prompts"] > 1  # a continuation: only what a rejection kept from running is left
    if later:
        scripted, LEFT["calls"] = LEFT["calls"] or [], None
    else:
        scripted = json.loads(os.environ.get("KRAFT_FAKE_ACP_CALLS") or "[]")
    if "ask" in FLAGS and not later:
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
    if "fs" in FLAGS and not later:
        send(
            {
                "jsonrpc": "2.0",
                "id": "fs-1",
                "method": "fs/read_text_file",
                "params": {"sessionId": sid, "path": "/etc/hosts"},
            }
        )
        await_response("fs-1")
    ended = run_calls(sid, scripted) if scripted else None
    if not scripted and not later:
        if asking() and "ask" not in FLAGS:
            # the agent was told to ask: the default call asks and honours the answer
            ended = run_calls(
                sid,
                [{"id": "tc-1", "title": "Run ls", "kind": "execute", "input": {"command": "ls"}}],
            )
        else:  # legacy: the flag-driven ask block above, or no asking at all
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
    stop = ended or "end_turn"
    if "slow" in FLAGS or ("slow_continue" in FLAGS and later):
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
    if ended != "cancelled":
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
            offered = {"model": MODELS, "effort": EFFORTS, "mode": MODES}.get(
                params.get("configId"), ()
            )
            if "refuse_config" in FLAGS or value not in offered:
                fail(
                    id_,
                    -32602,
                    f"invalid value {value!r} for {params.get('configId')!r}; "
                    f"offered: {list(offered)}",
                )
            else:
                if params.get("configId") == "mode" and value == "default":
                    ASKING["on"] = True
                reply(id_, {"configOptions": config_options(**{params["configId"]: value})})
        elif method == "session/set_mode":
            if "refuse_mode" in FLAGS:
                fail(id_, -32602, f"invalid mode {params.get('modeId')!r}")
            else:
                if params.get("modeId") == "default":
                    ASKING["on"] = True
                reply(id_, {})
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
