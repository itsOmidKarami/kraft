"""A stop during the rebase `/resume` runs (Kraft-9d8b2.80). A sibling of
test_pause_resume.py."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest
from support.api import _started
from support.harness import commit_all, git
from test_pause_resume import _PAST_THE_RESUME_WAIT, _running_agent, _wait

from kraft.config import git_read


@_PAST_THE_RESUME_WAIT
@pytest.mark.parametrize("stop", ["pause", "cancel"])
def test_a_stop_during_the_rebase_a_resume_runs_ends_it_and_moves_nothing(
    monkeypatch, repo, client, tmp_path, stop
):
    """Kraft-9d8b2.80: the rebase `/resume` runs ran inline in its request, out
    of reach of pause and cancel, so a stopped item's branch still moved and a
    `work_item_resumed` was written over the stop. It is the item's own task
    now: the stop kills the hung checkout, the resume answers 409 and writes no
    `work_item_resumed`, and the branch stays where it was."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "30")
    wid = _started(
        client, {"repo": str(repo), "title": "stop mid-refresh", "chain_template": "quick-task"}
    )
    _running_agent(client, wid)
    assert client.post(f"/api/work-items/{wid}/pause", json={}).status_code == 200
    item = _wait(
        lambda: (lambda b: b if b["status"] == "paused" else None)(
            client.get(f"/api/work-items/{wid}").json()
        ),
        "the item to read paused",
    )
    worktree = Path(item["worktree_path"])
    head = git_read(worktree, "rev-parse", "HEAD")
    resumed_before = [
        e
        for e in client.get(f"/api/work-items/{wid}/events").json()
        if e["type"] == "work_item_resumed"
    ]

    pids = tmp_path / "filter-pids"
    git(
        repo,
        "config",
        "filter.slow.smudge",
        f"sh -c 'echo $$ >> \"{pids}\"; exec sleep 60'",
    )
    (repo / ".gitattributes").write_text("slow.txt filter=slow\n")
    (repo / "slow.txt").write_text("slow\n")
    commit_all(repo, "a filtered file on the base")

    answer: dict = {}
    resuming = threading.Thread(
        target=lambda: answer.update(r=client.post(f"/api/work-items/{wid}/resume", json={}))
    )
    resuming.start()
    pid = int(
        _wait(lambda: pids.exists() and pids.read_text().split()[:1], "the filter to hang")[0]
    )
    if stop == "pause":
        assert client.post(f"/api/work-items/{wid}/pause", json={}).status_code == 200
    else:
        assert (
            client.post(f"/api/work-items/{wid}/cancel", json={"reason": "no"}).status_code == 200
        )
    resuming.join(timeout=30)

    assert not resuming.is_alive()
    assert answer["r"].status_code == 409
    _wait(lambda: not _alive(pid), "the hung filter to be killed")
    if stop == "pause":
        assert git_read(worktree, "rev-parse", "HEAD") == head
    after = client.get(f"/api/work-items/{wid}").json()
    assert after["status"] == ("paused" if stop == "pause" else "abandoned")
    resumed = [
        e
        for e in client.get(f"/api/work-items/{wid}/events").json()
        if e["type"] == "work_item_resumed"
    ]
    assert resumed == resumed_before


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
