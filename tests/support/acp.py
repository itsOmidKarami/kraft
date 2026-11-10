"""Helpers for driving Kraft's ACP client against the fake agent (fixtures/fake_acp_agent.py)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from kraft import harness
from kraft.acp import log
from kraft.acp.client import CLIENT_ENV
from support.server import child_env

FAKE = Path(__file__).resolve().parents[2] / "fixtures" / "fake_acp_agent.py"


def agent_argv() -> list[str]:
    """By path, never by name: `_no_real_agent_binary` stubs real agent names, not this."""
    return [sys.executable, str(FAKE)]


def spec(tmp_path: Path, *, at: Path | None = None, **fields) -> Path:
    """Write a `LaunchSpec` for the fake agent into tmp_path (or to `at`) and return its path."""
    from kraft.acp.spec import LaunchSpec

    body = {
        "agent_argv": agent_argv(),
        "cwd": str(tmp_path),
        "prompt": "do it",
        "context": "ctx",
        "result_path": str(tmp_path / "result.json"),
        "options": {"model": "model", "effort": "effort"},
        **fields,
    }
    path = at or tmp_path / "acp-launch.json"
    path.write_text(LaunchSpec.model_validate(body).model_dump_json())
    return path


def client_argv(spec_path: Path) -> list[str]:
    return [sys.executable, "-I", "-m", "kraft", "admin", "acp-client", "--spec", str(spec_path)]


def client_env(tmp_path: Path, flags: str = "", **extra: str) -> dict:
    env = child_env(
        {
            "HOME": str(tmp_path),
            "KRAFT_HOME": str(tmp_path / "k"),
            "KRAFT_FAKE_ACP": flags,
            "KRAFT_FAKE_ACP_RECORD": str(tmp_path / "rec"),
            "KRAFT_RESULT_PATH": str(tmp_path / "result.json"),
        }
    )
    # A test run under an ACP agent inherits the recursion marker: it is not this client's.
    env.pop(CLIENT_ENV, None)
    return {**env, **extra}


@contextmanager
def client_process(
    tmp_path: Path,
    flags: str = "",
    *,
    argv: list[str] | None = None,
    stderr: int | None = None,
    env: dict[str, str] | None = None,
    **fields,
) -> Iterator[subprocess.Popen]:
    """Kraft's client as a child, in a session of its own, and its whole process group killed on the
    way out, a timeout included: `subprocess.run(timeout=)` kills only the direct child, and a
    client that spawned itself (or its agent) would outlive the test. Every test that starts the
    client does it here."""
    proc = subprocess.Popen(
        argv or client_argv(spec(tmp_path, **fields)),
        stdout=subprocess.PIPE,
        stderr=stderr,
        text=True,
        env=client_env(tmp_path, flags, **(env or {})),
        cwd=tmp_path,
        start_new_session=True,
    )
    try:
        yield proc
    finally:
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        if proc.stdout:
            proc.stdout.close()


def run_client(
    tmp_path: Path,
    flags: str = "",
    *,
    timeout: int = 60,
    argv: list[str] | None = None,
    env: dict[str, str] | None = None,
    **fields,
):
    """Run the client to completion; `(CompletedProcess, [log records])`. `env` is set on the
    child on top of `client_env`'s: a real agent that needs the operator's login passes
    `env={"HOME": str(Path.home())}`."""
    with client_process(
        tmp_path, flags, argv=argv, stderr=subprocess.PIPE, env=env, **fields
    ) as proc:
        out, err = proc.communicate(timeout=timeout)
    done = subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
    return done, list(log.records(done.stdout.splitlines()))


def events(records, name):
    """The client's own log events called `name`."""
    return [r for r in records if r.get("dir") == "client" and r.get("event") == name]


def recorded(tmp_path: Path) -> list[dict]:
    """Every JSON line the client sent the fake agent."""
    path = tmp_path / "rec"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def fake_gate(decisions=None, seen=None, *, default="allow", delay=0.0, message=None):
    """Stands in for `reads.permission_request`: `decisions` maps a Kraft tool name to allow/deny; a
    tool it does not list gets `default`. `message` overrides the answer's text. `ask.peak` is the
    most calls that were inside the gate at once: a serial client leaves it at 1."""
    import asyncio

    inside = 0

    async def ask(tool, input, tool_use_id=None, **kw):
        nonlocal inside
        if seen is not None:
            seen.append((tool, kw))
        inside += 1
        ask.peak = max(ask.peak, inside)
        try:
            await asyncio.sleep(delay)
        finally:
            inside -= 1
        return {
            "behavior": (decisions or {}).get(tool, default),
            "message": message or f"{tool}: decided",
        }

    ask.peak = 0
    return ask


def run_inprocess(
    tmp_path, monkeypatch, flags="", *, ask=None, calls=None, env=None, timeout=280, **fields
):
    """Kraft's client in this process, against the fake agent, with `ask` as the gate;
    `(exit code, log records)`. The in-process route is how the unit tier reaches the gate seam."""
    import asyncio
    import io

    from kraft.acp.client import Client
    from kraft.acp.spec import LaunchSpec

    monkeypatch.delenv(CLIENT_ENV, raising=False)
    for k, v in {
        "KRAFT_FAKE_ACP": flags,
        "KRAFT_FAKE_ACP_RECORD": str(tmp_path / "rec"),
        "KRAFT_RESULT_PATH": str(tmp_path / "result.json"),
        **({"KRAFT_FAKE_ACP_CALLS": json.dumps(calls)} if calls else {}),
        **(env or {}),
    }.items():
        monkeypatch.setenv(k, v)
    out = io.StringIO()
    client = Client(
        LaunchSpec.model_validate_json(spec(tmp_path, **fields).read_text()), out, ask=ask
    )
    code = asyncio.run(asyncio.wait_for(client.run(), timeout))
    return code, list(log.records(out.getvalue().splitlines()))


#: The three calls the probe's prompt asks for, as the fake agent scripts them.
PROBE_CALLS = [
    {"id": "c1", "kind": "read", "name": "read", "input": {"path": "probe-notes.txt"}},
    {"id": "c2", "kind": "edit", "name": "edit", "input": {"path": "probe-out.txt"}},
    {"id": "c3", "kind": "execute", "name": "execute", "input": {"command": "echo kraft-probe"}},
]

#: A `kind: acp` entry for the fake agent that asks on every call (`FAKE_ACP_ASK_ALL`).
PROBE_ENTRY = {
    "id": "fake",
    "kind": "acp",
    "command": agent_argv(),
    "reports": ["tokens", "cost"],
    "options": {"model": {"config": "model"}},
    "asks_everything": {"env": {"FAKE_ACP_ASK_ALL": "1"}},
    "tool_names": {"read": "Read", "edit": "Write", "execute": "Bash"},
}


def probe_entry(**over) -> harness.Harness:
    return harness.Harness.from_mapping({**PROBE_ENTRY, **over}, where="test", path=None)
