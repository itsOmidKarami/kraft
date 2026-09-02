from __future__ import annotations

import asyncio
import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import psutil

from kraft import store


def _resolve_result_file(path: Path) -> str | None:
    """Status from a result file alone, or None if it's missing/empty."""
    if not path.exists():
        return None
    try:
        raw = path.read_text().strip()
    except OSError:
        return "failed"
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "failed"
    status = data.get("status") if isinstance(data, dict) else None
    return status if status in ("done", "failed") else "failed"


def _resolve(result_path: Path, returncode: int) -> str:
    file_status = _resolve_result_file(result_path)
    if file_status is not None:
        return file_status
    return "done" if returncode == 0 else "failed"


async def run_task(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    cmd: list[str],
    cwd: str | Path,
    env: dict | None = None,
    post_resolve: Callable[[str, Path, int], str] | None = None,
) -> str:
    log_path = run_dirs.logs / f"{session_id}.log"
    result_path = run_dirs.results / f"{session_id}.json"

    await db.write(
        lambda c: store.create_session(
            c,
            id=session_id,
            work_item_id=work_item_id,
            node_id=node_id,
            hook_point=hook_point,
            log_path=str(log_path),
            result_path=str(result_path),
        )
    )

    full_env = {**os.environ, **(env or {}), "KRAFT_RESULT_PATH": str(result_path)}
    log = open(log_path, "w")
    try:
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=full_env,
            )
        except FileNotFoundError:
            await db.write(lambda c: store.session_exited(c, session_id, "failed"))
            return "failed"
    finally:
        log.close()  # the child holds its own dup'd fd

    try:
        pid_start_time = psutil.Process(proc.pid).create_time()
    except psutil.Error:
        pid_start_time = None
    await db.write(lambda c: store.session_running(c, session_id, proc.pid, pid_start_time))

    returncode = await asyncio.to_thread(proc.wait)
    status = _resolve(result_path, returncode)
    if post_resolve is not None:
        status = post_resolve(status, log_path, returncode)
    await db.write(lambda c: store.session_exited(c, session_id, status))
    return status
