"""Helpers for driving Kraft's ACP client against the fake agent (fixtures/fake_acp_agent.py)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from kraft.acp import log
from support.server import child_env

FAKE = Path(__file__).resolve().parents[2] / "fixtures" / "fake_acp_agent.py"


def agent_argv() -> list[str]:
    """By path, never by name: `_no_real_agent_binary` stubs real agent names, not this."""
    return [sys.executable, str(FAKE)]


def spec(tmp_path: Path, **fields) -> Path:
    """Write a `LaunchSpec` for the fake agent into tmp_path and return its path."""
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
    path = tmp_path / "acp-launch.json"
    path.write_text(LaunchSpec.model_validate(body).model_dump_json())
    return path


def client_argv(spec_path: Path) -> list[str]:
    return [sys.executable, "-I", "-m", "kraft", "admin", "acp-client", "--spec", str(spec_path)]


def client_env(tmp_path: Path, flags: str = "", **extra: str) -> dict:
    return child_env(
        {
            "HOME": str(tmp_path),
            "KRAFT_HOME": str(tmp_path / "k"),
            "KRAFT_FAKE_ACP": flags,
            "KRAFT_FAKE_ACP_RECORD": str(tmp_path / "rec"),
            "KRAFT_RESULT_PATH": str(tmp_path / "result.json"),
            **extra,
        }
    )


def run_client(tmp_path: Path, flags: str = "", *, timeout: int = 60, **fields):
    """Run the client to completion; `(CompletedProcess, [log records])`."""
    done = subprocess.run(
        client_argv(spec(tmp_path, **fields)),
        capture_output=True,
        text=True,
        env=client_env(tmp_path, flags),
        timeout=timeout,
        cwd=tmp_path,
    )
    return done, list(log.records(done.stdout.splitlines()))


def recorded(tmp_path: Path) -> list[dict]:
    """Every JSON line the client sent the fake agent."""
    path = tmp_path / "rec"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
