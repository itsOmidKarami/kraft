"""Where a session started now keeps its files (`kraft.item_paths`), for a
test that reads what a launch left or fakes what an agent writes. Both are
re-exported by `support.harness`."""

from __future__ import annotations

import json
from pathlib import Path

from kraft import item_paths


def own_files(run_dirs, sid: str, wid: str = "w1"):
    """Where session `sid` of item `wid`, started now, keeps its files: its
    own folder under `run/items/<wid>/sessions/` (`item_paths.session_files`)."""
    return item_paths.session_files(run_dirs, wid, sid)


def write_own_result(run_dirs, wid: str, sid: str, payload: dict) -> Path:
    """Write `payload` as session `sid`'s result file, where a session started
    now has it (`own_files(...).result`): what a faked agent launch leaves."""
    path = own_files(run_dirs, sid, wid).result
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path
