from __future__ import annotations

import json
import shlex
from pathlib import Path

from kraft.adapters import subprocess as _subprocess

_CTX = (
    "You are working on a Kraft work item.\n"
    "Title: {title}\n"
    "Task: {task_instruction}\n"
    "Repo: {repo_path}"
)


def _envelope_is_error(_base_status: str, log_path: Path, _returncode: int) -> str:
    try:
        lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
    except OSError:
        return _base_status
    if not lines:
        return _base_status
    try:
        envelope = json.loads(lines[-1])
    except json.JSONDecodeError:
        return _base_status
    if isinstance(envelope, dict) and envelope.get("is_error") is True:
        return "failed"
    return _base_status


async def run_agent_task(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    command: str,
    title: str,
    task_instruction: str,
    repo_path: str,
    cwd,
) -> str:
    ctx = _CTX.format(title=title, task_instruction=task_instruction, repo_path=repo_path)
    cmd = [
        *shlex.split(command),
        "-p",
        task_instruction,
        "--append-system-prompt",
        ctx,
        "--output-format",
        "json",
    ]
    return await _subprocess.run_task(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=node_id,
        hook_point=hook_point,
        cmd=cmd,
        cwd=cwd,
        post_resolve=_envelope_is_error,
    )
