"""A stand-in for a running remote step whose own is gone (it died with the server, or the machine
was rebooted)."""

from __future__ import annotations

import subprocess

import psutil

from kraft import store
from kraft.remote import runs as runs_mod
from kraft.remote import standin
from kraft.worker.env import worker_env


async def standin_for(db, run_dirs, row) -> int | None:
    """The new stand-in's pid, or None when the step is not one this server can recover."""
    runs = runs_mod.current()
    if runs is None or await runs.recover(row["work_item_id"], row["id"]) is None:
        return None
    with open(row["log_path"], "ab") as log:  # it resumes at the file's size
        proc = subprocess.Popen(
            standin.argv(run_dirs.base, row["work_item_id"], row["id"]),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=worker_env(None, {"KRAFT_RUN_DIR": str(run_dirs.base)}),
        )
    started = psutil.Process(proc.pid).create_time()
    await db.write(
        lambda c: store.session_running(
            c, row["id"], proc.pid, started, work_item_id=row["work_item_id"]
        )
    )
    return proc.pid
